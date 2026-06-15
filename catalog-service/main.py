import os
import json
import time
import threading
from contextlib import asynccontextmanager

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PORT      = int(os.getenv("PORT",    "8001"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5434"))
DB_NAME   = os.getenv("DB_NAME",    "catalog")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

STREAM_NAME    = "payments"
CONSUMER_GROUP = "catalog-group"
CONSUMER_NAME  = "catalog-consumer"


def get_db():
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,
    )


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id          SERIAL PRIMARY KEY,
                name        VARCHAR(255)  NOT NULL,
                price       NUMERIC(10,2) NOT NULL,
                description TEXT,
                stock       INTEGER       NOT NULL DEFAULT 0,
                category    VARCHAR(100)
            )
        """)
        row = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()
        if row["n"] == 0:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO products (name, price, description, stock, category) VALUES (%s,%s,%s,%s,%s)",
                    [
                        ("Notebook Dell Inspiron",    3500.00, "Notebook com processador Intel Core i5, 8GB RAM, 256GB SSD",    10, "Informática"),
                        ("Mouse Logitech MX Master",   450.00, "Mouse sem fio ergonômico com scroll de alta precisão",          50, "Periféricos"),
                        ("Teclado Mecânico Redragon",  350.00, "Teclado mecânico com switches blue e iluminação RGB",           30, "Periféricos"),
                        ('Monitor LG 24" Full HD',    1200.00, "Monitor IPS 24 polegadas Full HD 75Hz",                         15, "Monitores"),
                        ("Headset HyperX Cloud",       600.00, "Headset gamer com som surround 7.1 e microfone removível",      25, "Áudio"),
                    ],
                )


def stream_consumer():
    """Background worker that consumes 'payment_approved' events and decrements stock in PostgreSQL."""
    r = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        r.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

    while True:
        try:
            messages = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_NAME: ">"},
                count=10, block=2000,
            )
            if not messages:
                continue
            for _, msgs in messages:
                for msg_id, data in msgs:
                    if data.get("type") == "payment_approved":
                        items = json.loads(data["items"])
                        with get_db() as conn:
                            for item in items:
                                conn.execute(
                                    """UPDATE products
                                          SET stock = stock - %s
                                        WHERE id = %s AND stock >= %s""",
                                    (item["quantity"], item["product_id"], item["quantity"]),
                                )
                    r.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
        except Exception as e:
            print(f"[stream-consumer] error: {e}")
            time.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    threading.Thread(target=stream_consumer, daemon=True).start()
    yield


app = FastAPI(title="Catalog Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ────────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    price: float
    description: str
    stock: int
    category: str


class StockUpdate(BaseModel):
    stock: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "catalog-service"}


@app.get("/products")
def list_products(skip: int = 0, limit: int = 20, category: str = None):
    with get_db() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM products WHERE LOWER(category) = LOWER(%s) LIMIT %s OFFSET %s",
                (category, limit, skip),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM products WHERE LOWER(category) = LOWER(%s)", (category,)
            ).fetchone()["n"]
        else:
            rows = conn.execute(
                "SELECT * FROM products LIMIT %s OFFSET %s", (limit, skip)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    return {"total": total, "skip": skip, "limit": limit, "products": rows}


@app.get("/products/{product_id}")
def get_product(product_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return row


@app.post("/products", status_code=201)
def create_product(product: ProductCreate):
    with get_db() as conn:
        row = conn.execute(
            """INSERT INTO products (name, price, description, stock, category)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (product.name, product.price, product.description, product.stock, product.category),
        ).fetchone()
    return row


@app.put("/products/{product_id}/stock")
def update_stock(product_id: int, body: StockUpdate):
    if body.stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot be negative")
    with get_db() as conn:
        row = conn.execute(
            "UPDATE products SET stock = %s WHERE id = %s RETURNING *",
            (body.stock, product_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return row
