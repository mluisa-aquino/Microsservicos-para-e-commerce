"""
Payment Service
---------------
Responsável por processar pagamentos, persistir pedidos e publicar
eventos de resultado via Redis Streams.

Funcionalidades:
- Consumo assíncrono de eventos 'checkout.requested' do stream 'checkout'
- Processamento de pagamentos com simulação de aprovação/recusa
- Autenticação via JWT no endpoint HTTP (mantido para testes diretos)
- Persistência de pedidos em PostgreSQL
- Publicação de 'payment.processed' em 'payment_results' (para cart-service)
- Publicação de 'payment_approved' em 'payments' (para catalog-service)
- Chave de idempotência: evita reprocessamento em caso de reentrega pelo stream
- Consumer name dinâmico por hostname — seguro para escalar com --scale

Lógica de aprovação simulada:
- PIX e Cartão: 90% de taxa de aprovação
- Boleto: sempre gera status 'pending'

Porta padrão: 8003
"""

import os
import json
import uuid
import random
import socket
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime

import jwt
import psycopg
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
PORT      = int(os.getenv("PORT",    "8003"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5435"))
DB_NAME   = os.getenv("DB_NAME",    "payment")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

JWT_SECRET    = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# Redis Streams
PAYMENTS_STREAM         = "payments"         # → catalog-service (decrementa estoque)
CHECKOUT_STREAM         = "checkout"         # ← cart-service (pedidos)
PAYMENT_RESULTS_STREAM  = "payment_results"  # → cart-service (resultado)
CHECKOUT_CONSUMER_GROUP = "payment-group"
# Hostname único por container — evita conflito de consumer name ao escalar
CHECKOUT_CONSUMER_NAME  = f"payment-consumer-{socket.gethostname()}"

ACCEPTED_METHODS = {"card", "pix", "boleto"}

_redis = redis.from_url(REDIS_URL, decode_responses=True)


# ── Banco de dados ────────────────────────────────────────────────────────────

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


# ── Helpers de pagamento ──────────────────────────────────────────────────────

def _simulate_payment(payment_method: str) -> tuple[str, str]:
    if payment_method == "boleto":
        return "pending", "Bank slip generated. Payment confirmed within 3 business days."
    if random.random() > 0.1:
        return "approved", "Payment approved successfully!"
    return "declined", "Payment declined. Please check your details and try again."


def _persist_payment(payment_id: str, user_id: str, items: list, total: float,
                     payment_method: str, status: str, message: str):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO payments
                   (payment_id, user_id, items, total, payment_method, status, message)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payment_id, user_id, json.dumps(items), total, payment_method, status, message),
        )


# ── Consumer de pedidos de checkout ──────────────────────────────────────────

def checkout_consumer():
    """
    Worker em background que consome pedidos de checkout do Redis Stream.

    Fluxo:
    1. Aguarda eventos 'checkout.requested' publicados pelo cart-service
    2. Verifica idempotência via Redis (evita processar o mesmo order_id duas vezes)
    3. Processa o pagamento (simulação) e persiste no PostgreSQL
    4. Publica 'payment.processed' em 'payment_results' para o cart-service
    5. Se aprovado, publica 'payment_approved' em 'payments' para o catalog-service
    6. Confirma processamento com XACK

    A idempotência aqui é importante: se o consumer crashar após processar
    mas antes do XACK, o stream reentregará a mensagem. Sem essa checagem,
    o mesmo pedido seria cobrado duas vezes.
    """
    r = redis.from_url(REDIS_URL, decode_responses=True)

    try:
        r.xgroup_create(CHECKOUT_STREAM, CHECKOUT_CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

    while True:
        try:
            messages = r.xreadgroup(
                CHECKOUT_CONSUMER_GROUP, CHECKOUT_CONSUMER_NAME,
                {CHECKOUT_STREAM: ">"},
                count=10, block=2000,
            )
            if not messages:
                continue

            for _, msgs in messages:
                for msg_id, data in msgs:
                    if data.get("type") == "checkout.requested":
                        order_id       = data["order_id"]
                        user_id        = data["user_id"]
                        items          = json.loads(data["items"])
                        payment_method = data.get("payment_method", "card")

                        if payment_method not in ACCEPTED_METHODS:
                            payment_method = "card"

                        # Idempotência no consumer: se já processamos este order_id,
                        # republica o resultado sem gravar de novo no banco
                        cached_raw = r.get(f"idempotency:payment:{order_id}")
                        if cached_raw:
                            cached = json.loads(cached_raw)
                            r.xadd(PAYMENT_RESULTS_STREAM, {
                                "type":       "payment.processed",
                                "order_id":   order_id,
                                "payment_id": cached["payment_id"],
                                "status":     cached["status"],
                                "message":    cached["message"],
                            })
                            r.xack(CHECKOUT_STREAM, CHECKOUT_CONSUMER_GROUP, msg_id)
                            continue

                        total      = sum(i["unit_price"] * i["quantity"] for i in items)
                        payment_id = str(uuid.uuid4())[:8].upper()
                        status, message = _simulate_payment(payment_method)

                        _persist_payment(payment_id, user_id, items, total,
                                         payment_method, status, message)

                        result = {
                            "payment_id": payment_id,
                            "status":     status,
                            "message":    message,
                        }
                        r.setex(f"idempotency:payment:{order_id}", 86400, json.dumps(result))

                        # Notifica o cart-service com o resultado do pagamento
                        r.xadd(PAYMENT_RESULTS_STREAM, {
                            "type":       "payment.processed",
                            "order_id":   order_id,
                            "payment_id": payment_id,
                            "status":     status,
                            "message":    message,
                        })

                        # Notifica o catalog-service para decrementar o estoque
                        if status == "approved":
                            r.xadd(PAYMENTS_STREAM, {
                                "type":       "payment_approved",
                                "payment_id": payment_id,
                                "user_id":    user_id,
                                "items":      json.dumps(items),
                            })

                    r.xack(CHECKOUT_STREAM, CHECKOUT_CONSUMER_GROUP, msg_id)

        except Exception as e:
            print(f"[checkout-consumer] erro: {e}")
            time.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Inicializa o banco e inicia o consumer de checkout em background."""
    init_db()
    threading.Thread(target=checkout_consumer, daemon=True).start()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Payment Service", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Autenticação (usada apenas no endpoint HTTP direto) ───────────────────────

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


def require_owner(user_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Dependency que garante que o usuário só acesse os próprios pagamentos."""
    if user["sub"] != user_id and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="You can only access your own payments")
    return user


# ── Modelos de entrada ────────────────────────────────────────────────────────

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
def get_user_payments(user_id: str, _: dict = Depends(require_owner)):
    """Retorna o histórico de pagamentos de um usuário, ordenado do mais recente."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return {"user_id": user_id, "total": len(rows), "payments": [_fmt(r) for r in rows]}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str, user: dict = Depends(get_current_user)):
    """Retorna os detalhes de um pagamento específico pelo seu ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE payment_id = %s", (payment_id.upper(),)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    if row["user_id"] != user["sub"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="You can only access your own payments")
    return _fmt(row)


@app.post("/payments", status_code=201)
def process_payment(req: PaymentRequest, request: Request, user: dict = Depends(get_current_user)):
    """
    Endpoint HTTP direto mantido para testes e uso externo.
    O fluxo principal de checkout usa mensageria (stream 'checkout').
    """
    idempotency_key = request.headers.get("idempotency-key")

    if idempotency_key:
        cached = _redis.get(f"idempotency:payment:{idempotency_key}")
        if cached:
            return JSONResponse(
                content=json.loads(cached),
                status_code=201,
                headers={"X-Idempotent-Replayed": "true"},
            )

    if user["sub"] != req.user_id and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="You can only pay for your own orders")
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart cannot be empty")
    if req.payment_method not in ACCEPTED_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid method. Accepted: {', '.join(ACCEPTED_METHODS)}")

    total      = sum(i.unit_price * i.quantity for i in req.items)
    payment_id = str(uuid.uuid4())[:8].upper()
    items_data = [i.model_dump() for i in req.items]
    status, message = _simulate_payment(req.payment_method)

    _persist_payment(payment_id, req.user_id, items_data, total,
                     req.payment_method, status, message)

    if status == "approved":
        _redis.xadd(PAYMENTS_STREAM, {
            "type":       "payment_approved",
            "payment_id": payment_id,
            "user_id":    req.user_id,
            "items":      json.dumps(items_data),
        })

    result = {
        "payment_id":     payment_id,
        "user_id":        req.user_id,
        "items":          items_data,
        "total":          round(total, 2),
        "payment_method": req.payment_method,
        "status":         status,
        "message":        message,
        "created_at":     datetime.now().isoformat(),
    }

    if idempotency_key:
        _redis.setex(f"idempotency:payment:{idempotency_key}", 86400, json.dumps(result))

    return result


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
