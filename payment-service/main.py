"""
Payment Service
---------------
Responsável por processar pagamentos, persistir pedidos e publicar
eventos de aprovação no Redis Stream.

Funcionalidades:
- Processamento de pagamentos com simulação de aprovação/recusa
- Persistência de pedidos em PostgreSQL
- Publicação de eventos no Redis Stream após aprovação
- Consulta de histórico de pagamentos por usuário

Lógica de aprovação simulada:
- PIX e Cartão: 90% de taxa de aprovação (aleatoriedade controlada)
- Boleto: sempre gera status 'pending' (aguarda compensação bancária)

Porta padrão: 8003
"""

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

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
PORT      = int(os.getenv("PORT",    "8003"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5435"))
DB_NAME   = os.getenv("DB_NAME",    "payment")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

# Nome do Redis Stream onde eventos de pagamento aprovado são publicados.
# O catalog-service consome este stream para decrementar o estoque.
STREAM_NAME      = "payments"
ACCEPTED_METHODS = {"card", "pix", "boleto"}

_redis = redis.from_url(REDIS_URL, decode_responses=True)


def get_db():
    """Abre e retorna uma conexão com o banco PostgreSQL."""
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,
    )


def init_db():
    """
    Cria a tabela de pagamentos se não existir.
    Executado na inicialização do serviço.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id     VARCHAR(8)    PRIMARY KEY,   -- ID curto gerado via UUID
                user_id        VARCHAR(255)  NOT NULL,
                items          JSONB         NOT NULL,       -- snapshot dos itens no momento da compra
                total          NUMERIC(10,2) NOT NULL,
                payment_method VARCHAR(50)   NOT NULL,
                status         VARCHAR(50)   NOT NULL,       -- 'approved', 'declined' ou 'pending'
                message        TEXT,
                created_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
            )
        """)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Inicializa o banco de dados na subida do serviço."""
    init_db()
    yield


app = FastAPI(title="Payment Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Modelos de entrada ────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    """Representa um item dentro de um pedido."""
    product_id: int
    name: str
    unit_price: float
    quantity: int


class PaymentRequest(BaseModel):
    """Dados enviados pelo cart-service para processar um pagamento."""
    user_id: str
    items: list[OrderItem]
    payment_method: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Verifica se o serviço está operacional."""
    return {"status": "ok", "service": "payment-service"}


@app.get("/payments/user/{user_id}")
def get_user_payments(user_id: str):
    """Retorna o histórico de pagamentos de um usuário, ordenado do mais recente."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return {"user_id": user_id, "total": len(rows), "payments": [_fmt(r) for r in rows]}


@app.post("/payments", status_code=201)
def process_payment(req: PaymentRequest):
    """
    Processa um pagamento e persiste o resultado.

    Fluxo:
    1. Valida método de pagamento e itens do pedido
    2. Calcula o total
    3. Simula aprovação/recusa com base no método de pagamento
    4. Persiste o pedido no PostgreSQL
    5. Se aprovado, publica evento no Redis Stream para atualização de estoque

    O payment_id é gerado a partir dos primeiros 8 caracteres de um UUID v4,
    garantindo unicidade com identificador curto e legível.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")
    if req.payment_method not in ACCEPTED_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid method. Accepted: {', '.join(ACCEPTED_METHODS)}")

    total      = sum(i.unit_price * i.quantity for i in req.items)
    payment_id = str(uuid.uuid4())[:8].upper()
    items_data = [i.model_dump() for i in req.items]

    # Simulação de aprovação de pagamento:
    # - Boleto: sempre 'pending' (requer compensação bancária de até 3 dias)
    # - PIX/Cartão: aprovação com 90% de probabilidade
    if req.payment_method == "boleto":
        status, message = "pending", "Bank slip generated. Payment confirmed within 3 business days."
    elif random.random() > 0.1:
        status, message = "approved", "Payment approved successfully!"
    else:
        status, message = "declined", "Payment declined. Please check your details and try again."

    # Persiste o pedido independentemente do status (aprovado, recusado ou pendente)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO payments
                   (payment_id, user_id, items, total, payment_method, status, message)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payment_id, req.user_id, json.dumps(items_data), total,
             req.payment_method, status, message),
        )

    # Publica evento no Redis Stream somente em caso de aprovação.
    # O catalog-service consome este evento de forma assíncrona para
    # decrementar o estoque — padrão de consistência eventual.
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
    """Retorna os detalhes de um pagamento específico pelo seu ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE payment_id = %s", (payment_id.upper(),)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    return _fmt(row)


def _fmt(row: dict) -> dict:
    """
    Serializa um registro do banco para JSON:
    - Converte objetos datetime para string ISO 8601
    - Converte Decimal do PostgreSQL para float nativo Python
    """
    result = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif hasattr(v, "__float__"):
            result[k] = float(v)
        else:
            result[k] = v
    return result
