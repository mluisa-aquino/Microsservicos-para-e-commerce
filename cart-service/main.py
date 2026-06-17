"""
Cart Service
------------
Responsável por gerenciar o carrinho de compras dos usuários
e orquestrar o checkout via mensageria assíncrona.

Funcionalidades:
- Armazenamento do carrinho no Redis com TTL de 24 horas
- Validação de estoque em tempo real via catalog-service (async)
- Autenticação via JWT — cada endpoint exige token válido
- Checkout assíncrono: publica em 'checkout', consome de 'payment_results'
- Chave de idempotência: reutiliza order_id para evitar publicação duplicada
- Consumer name dinâmico por hostname — seguro para escalar com --scale

Porta padrão: 8002
"""

import os
import json
import socket
import uuid
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import jwt
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
CATALOG_URL   = os.getenv("CATALOG_URL", "http://localhost:8001")
REDIS_URL     = os.getenv("REDIS_URL",   "redis://localhost:6379")

JWT_SECRET    = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM = "HS256"

CART_TTL  = 86400  # 24h — TTL do carrinho no Redis
ORDER_TTL = 3600   # 1h  — TTL do status do pedido (suficiente para polling)

# Redis Streams para checkout assíncrono
CHECKOUT_STREAM          = "checkout"
PAYMENT_RESULTS_STREAM   = "payment_results"
PAYMENT_RESULTS_GROUP    = "cart-group"
# Hostname único por container — evita conflito de consumer name ao escalar
PAYMENT_RESULTS_CONSUMER = f"cart-consumer-{socket.gethostname()}"

_redis = redis.from_url(REDIS_URL, decode_responses=True)


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


def require_owner(user_id: str, user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency que, além de autenticar, garante que o usuário só acesse o
    próprio carrinho (o user_id da URL precisa bater com o 'sub' do token).
    """
    if user["sub"] != user_id and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="You can only access your own cart")
    return user


# ── Redis helpers ─────────────────────────────────────────────────────────────

def _key(user_id: str) -> str:
    return f"cart:{user_id}"


def load_cart(user_id: str) -> list:
    raw = _redis.get(_key(user_id))
    return json.loads(raw) if raw else []


def save_cart(user_id: str, items: list):
    if items:
        _redis.setex(_key(user_id), CART_TTL, json.dumps(items))
    else:
        _redis.delete(_key(user_id))


# ── Consumer de resultados de pagamento ───────────────────────────────────────

def payment_results_consumer():
    """
    Worker em background que consome resultados de pagamento do Redis Stream.

    Fluxo:
    1. Aguarda eventos 'payment.processed' publicados pelo payment-service
    2. Atualiza o status do pedido no Redis (order:{order_id})
    3. Se aprovado, remove o carrinho do usuário (chave Redis)
    4. Confirma processamento com XACK para evitar reentrega
    """
    r = redis.from_url(REDIS_URL, decode_responses=True)

    try:
        r.xgroup_create(PAYMENT_RESULTS_STREAM, PAYMENT_RESULTS_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

    while True:
        try:
            messages = r.xreadgroup(
                PAYMENT_RESULTS_GROUP, PAYMENT_RESULTS_CONSUMER,
                {PAYMENT_RESULTS_STREAM: ">"},
                count=10, block=2000,
            )
            if not messages:
                continue

            for _, msgs in messages:
                for msg_id, data in msgs:
                    if data.get("type") == "payment.processed":
                        order_id = data.get("order_id")
                        raw = r.get(f"order:{order_id}")
                        if raw:
                            order = json.loads(raw)
                            order.update({
                                "status":     data.get("status"),
                                "payment_id": data.get("payment_id"),
                                "message":    data.get("message"),
                            })
                            r.setex(f"order:{order_id}", ORDER_TTL, json.dumps(order))
                            if data.get("status") == "approved":
                                r.delete(_key(order["user_id"]))

                    r.xack(PAYMENT_RESULTS_STREAM, PAYMENT_RESULTS_GROUP, msg_id)

        except Exception as e:
            print(f"[payment-results-consumer] erro: {e}")
            time.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Inicia o consumer de resultados de pagamento em background."""
    threading.Thread(target=payment_results_consumer, daemon=True).start()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Cart Service", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Modelos de entrada ────────────────────────────────────────────────────────

class ItemRequest(BaseModel):
    product_id: int
    quantity: int


class CheckoutRequest(BaseModel):
    payment_method: str  # 'pix', 'card' ou 'boleto'


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "cart-service"}


@app.get("/cart/{user_id}")
def get_cart(user_id: str, _: dict = Depends(require_owner)):
    items = load_cart(user_id)
    total = sum(i["unit_price"] * i["quantity"] for i in items)
    return {"user_id": user_id, "items": items, "total": round(total, 2)}


@app.post("/cart/{user_id}/items")
async def add_item(user_id: str, item: ItemRequest, _: dict = Depends(require_owner)):
    """
    Adiciona um item ao carrinho após validar disponibilidade no catalog-service.
    Usa httpx.AsyncClient para não bloquear o event loop do Uvicorn.
    """
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CATALOG_URL}/products/{item.product_id}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Product not found in catalog")

    product = resp.json()

    if item.quantity > product["stock"]:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock. Available: {product['stock']}",
        )

    cart = load_cart(user_id)

    for i in cart:
        if i["product_id"] == item.product_id:
            i["quantity"] += item.quantity
            save_cart(user_id, cart)
            total = sum(x["unit_price"] * x["quantity"] for x in cart)
            return {"message": "Quantity updated", "items": cart, "total": round(total, 2)}

    cart.append({
        "product_id": product["id"],
        "name":       product["name"],
        "unit_price": float(product["price"]),
        "quantity":   item.quantity,
    })
    save_cart(user_id, cart)

    total = sum(i["unit_price"] * i["quantity"] for i in cart)
    return {"message": "Item added successfully", "items": cart, "total": round(total, 2)}


@app.delete("/cart/{user_id}/items/{product_id}")
def remove_item(user_id: str, product_id: int, _: dict = Depends(require_owner)):
    cart = load_cart(user_id)
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    original = len(cart)
    cart = [i for i in cart if i["product_id"] != product_id]

    if len(cart) == original:
        raise HTTPException(status_code=404, detail="Product not in cart")

    save_cart(user_id, cart)
    total = sum(i["unit_price"] * i["quantity"] for i in cart)
    return {"message": "Item removed", "items": cart, "total": round(total, 2)}


@app.post("/cart/{user_id}/checkout")
async def checkout(
    user_id: str,
    req: CheckoutRequest,
    request: Request,
    _: dict = Depends(require_owner),
):
    """
    Inicia o checkout de forma assíncrona via Redis Streams.

    Fluxo:
    1. Se Idempotency-Key já foi vista → devolve o pedido existente (sem republicar)
    2. Gera um order_id (ou usa a própria chave de idempotência como order_id)
    3. Salva o pedido com status 'processing' no Redis
    4. Publica evento 'checkout.requested' no stream 'checkout'
    5. Retorna imediatamente — o cliente faz polling em GET /orders/{order_id}

    O payment-service consome o evento, processa e publica em 'payment_results'.
    O consumer deste serviço atualiza o status do pedido no Redis.
    """
    idempotency_key = request.headers.get("idempotency-key")

    if idempotency_key:
        existing = _redis.get(f"order:{idempotency_key}")
        if existing:
            order = json.loads(existing)
            resp = JSONResponse(content=order)
            if order["status"] != "processing":
                resp.headers["X-Idempotent-Replayed"] = "true"
            return resp
        # Usa a chave como order_id — garante que retries do cliente
        # sempre apontem para o mesmo pedido no Redis
        order_id = idempotency_key
    else:
        order_id = str(uuid.uuid4())[:8].upper()

    items = load_cart(user_id)
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    total = round(sum(i["unit_price"] * i["quantity"] for i in items), 2)
    order = {
        "order_id":   order_id,
        "user_id":    user_id,
        "status":     "processing",
        "payment_id": None,
        "message":    "Processando pagamento...",
        "total":      total,
        "created_at": datetime.now().isoformat(),
    }
    _redis.setex(f"order:{order_id}", ORDER_TTL, json.dumps(order))

    _redis.xadd(CHECKOUT_STREAM, {
        "type":           "checkout.requested",
        "order_id":       order_id,
        "user_id":        user_id,
        "items":          json.dumps(items),
        "payment_method": req.payment_method,
    })

    return order


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    """
    Retorna o status atual de um pedido. Usado pelo frontend para polling
    após o checkout assíncrono.
    """
    raw = _redis.get(f"order:{order_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Order not found")
    return json.loads(raw)
