"""
Catalog Service
---------------
Responsável por gerenciar o catálogo de produtos da plataforma.

Funcionalidades:
- CRUD de produtos com persistência em PostgreSQL
- Autenticação via JWT — operações de escrita exigem role 'admin'
- Controle de estoque em tempo real
- Consumo de eventos do Redis Stream para decrementar estoque
  após aprovação de pagamento (arquitetura event-driven)

Porta padrão: 8001
"""

import os
import json
import socket
import time
import threading
from contextlib import asynccontextmanager

import jwt
import psycopg
import redis
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
# Permite sobrescrever valores no docker-compose sem alterar o código
PORT      = int(os.getenv("PORT",    "8001"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5434"))
DB_NAME   = os.getenv("DB_NAME",    "catalog")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

# Mesma chave usada pelo auth-service para assinar os JWTs. A validação aqui
# é local (sem chamar o auth-service): cada serviço verifica a assinatura
# de forma independente, evitando um ponto único de falha na autenticação.
JWT_SECRET    = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# Identificadores do Consumer Group no Redis Stream
# O Consumer Group garante que cada mensagem seja processada uma única vez,
# mesmo que múltiplas instâncias deste serviço estejam rodando
STREAM_NAME    = "payments"
CONSUMER_GROUP = "catalog-group"
# Hostname único por container — permite escalar sem conflito no Consumer Group
CONSUMER_NAME  = f"catalog-consumer-{socket.gethostname()}"


def get_db():
    """Abre e retorna uma conexão com o banco PostgreSQL."""
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,  # retorna linhas como dicionários
    )


def init_db():
    """
    Cria a tabela de produtos se não existir e popula com dados iniciais.
    Executado na inicialização do serviço via lifespan.
    """
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
        # Insere produtos de demonstração apenas se o banco estiver vazio
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
    """
    Worker em background que consome eventos do Redis Stream 'payments'.

    Fluxo:
    1. Aguarda mensagens do tipo 'payment_approved' no stream
    2. Para cada item do pedido aprovado, decrementa o estoque no PostgreSQL
    3. Confirma o processamento com XACK para evitar reprocessamento

    Este padrão implementa consistência eventual: o estoque não é
    decrementado no momento da compra, mas sim após a confirmação
    assíncrona do pagamento.
    """
    r = redis.from_url(REDIS_URL, decode_responses=True)

    # Cria o Consumer Group se não existir; ignora erro caso já exista
    try:
        r.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

    while True:
        try:
            # Lê até 10 mensagens, bloqueando por até 2s se não houver nenhuma
            messages = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_NAME: ">"},  # ">" significa: apenas mensagens ainda não entregues
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
                                # Decrementa estoque apenas se houver quantidade suficiente,
                                # evitando estoque negativo mesmo em condições de corrida
                                conn.execute(
                                    """UPDATE products
                                          SET stock = stock - %s
                                        WHERE id = %s AND stock >= %s""",
                                    (item["quantity"], item["product_id"], item["quantity"]),
                                )
                    # Confirma processamento para que a mensagem não seja reentregue
                    r.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)

        except Exception as e:
            print(f"[stream-consumer] erro: {e}")
            time.sleep(1)  # pausa antes de tentar novamente em caso de falha


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Gerencia o ciclo de vida da aplicação:
    - Na inicialização: cria tabelas e inicia o consumer do Redis em background
    - No encerramento: nenhuma ação necessária (thread daemon encerra com o processo)
    """
    init_db()
    # Thread daemon: encerra automaticamente quando o processo principal terminar
    threading.Thread(target=stream_consumer, daemon=True).start()
    yield


app = FastAPI(title="Catalog Service", version="2.0.0", lifespan=lifespan)

# CORS aberto para permitir chamadas do frontend em qualquer origem
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Autenticação ──────────────────────────────────────────────────────────────

def get_current_user(authorization: str = Header(None)) -> dict:
    """Dependency que extrai e valida o JWT do header Authorization: Bearer <token>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency que, além de autenticar, exige role 'admin' (cadastro/estoque)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


# ── Modelos de entrada ────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    """Dados necessários para cadastrar um novo produto."""
    name: str
    price: float
    description: str
    stock: int
    category: str


class StockUpdate(BaseModel):
    """Payload para atualização manual de estoque."""
    stock: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Verifica se o serviço está operacional. Usado pelo gateway e orquestrador."""
    return {"status": "ok", "service": "catalog-service"}


@app.get("/products")
def list_products(skip: int = 0, limit: int = 20, category: str = None):
    """
    Lista produtos com suporte a paginação e filtro por categoria.

    Parâmetros:
    - skip: número de registros a pular (offset)
    - limit: máximo de registros retornados
    - category: filtra por categoria (busca case-insensitive)
    """
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
    """Retorna os dados de um produto específico pelo seu ID."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return row


@app.post("/products", status_code=201)
def create_product(product: ProductCreate, _: dict = Depends(require_admin)):
    """
    Cadastra um novo produto no catálogo. Requer role 'admin'.
    Retorna o produto criado com o ID gerado pelo banco.
    """
    with get_db() as conn:
        row = conn.execute(
            """INSERT INTO products (name, price, description, stock, category)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (product.name, product.price, product.description, product.stock, product.category),
        ).fetchone()
    return row


@app.put("/products/{product_id}/stock")
def update_stock(product_id: int, body: StockUpdate, _: dict = Depends(require_admin)):
    """
    Atualiza manualmente o estoque de um produto. Requer role 'admin'.
    Usado pelo painel administrativo do frontend.
    """
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
