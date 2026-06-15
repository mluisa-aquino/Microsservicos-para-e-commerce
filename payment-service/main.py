import os
import json
import uuid
import random
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PORT      = int(os.getenv("PORT",    "8003"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5435"))
DB_NAME   = os.getenv("DB_NAME",    "payment")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

STREAM_NAME      = "payments"
ACCEPTED_METHODS = {"card", "pix", "boleto"}

_redis = redis.from_url(REDIS_URL, decode_responses=True)


def get_db():
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,
    )


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id     VARCHAR(8)    PRIMARY KEY,
                user_id        VARCHAR(255)  NOT NULL,
                items          JSONB         NOT NULL,
                total          NUMERIC(10,2) NOT NULL,
                payment_method VARCHAR(50)   NOT NULL,
                status         VARCHAR(50)   NOT NULL,
                message        TEXT,
                created_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
            )
        """)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Payment Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ────────────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    product_id: int
    name: str
    unit_price: float
    quantity: int


class PaymentRequest(BaseModel):
    user_id: str
    items: list[OrderItem]
    payment_method: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "payment-service"}


@app.get("/payments/user/{user_id}")
def get_user_payments(user_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return {"user_id": user_id, "total": len(rows), "payments": [_fmt(r) for r in rows]}


@app.post("/payments", status_code=201)
def process_payment(req: PaymentRequest):
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")
    if req.payment_method not in ACCEPTED_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid method. Accepted: {', '.join(ACCEPTED_METHODS)}")

    total      = sum(i.unit_price * i.quantity for i in req.items)
    payment_id = str(uuid.uuid4())[:8].upper()
    items_data = [i.model_dump() for i in req.items]

    if req.payment_method == "boleto":
        status, message = "pending", "Bank slip generated. Payment confirmed within 3 business days."
    elif random.random() > 0.1:
        status, message = "approved", "Payment approved successfully!"
    else:
        status, message = "declined", "Payment declined. Please check your details and try again."

    with get_db() as conn:
        conn.execute(
            """INSERT INTO payments
                   (payment_id, user_id, items, total, payment_method, status, message)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payment_id, req.user_id, json.dumps(items_data), total,
             req.payment_method, status, message),
        )

    if status == "approved":
        _redis.xadd(STREAM_NAME, {
            "type":       "payment_approved",
            "payment_id": payment_id,
            "user_id":    req.user_id,
            "items":      json.dumps(items_data),
        })

    return {
        "payment_id":     payment_id,
        "user_id":        req.user_id,
        "items":          items_data,
        "total":          round(total, 2),
        "payment_method": req.payment_method,
        "status":         status,
        "message":        message,
        "created_at":     datetime.now().isoformat(),
    }


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE payment_id = %s", (payment_id.upper(),)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    return _fmt(row)


def _fmt(row: dict) -> dict:
    result = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif hasattr(v, "__float__"):
            result[k] = float(v)
        else:
            result[k] = v
    return result
