"""
Cart Service — porta 8002

Gerencia o carrinho de compras no Redis (TTL 24h) e orquestra o checkout
de forma assíncrona: publica no stream 'checkout' e consome de
'payment_results' para atualizar o status do pedido.

Chave de idempotência: requisições repetidas com o mesmo Idempotency-Key
devolvem o pedido já existente, evitando cobranças duplicadas.
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
    # Garante que o usuário só acesse o próprio carrinho; admin tem acesso irrestrito
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
    Consome resultados de pagamento do stream 'payment_results'.
    Atualiza o status do pedido no Redis e, se aprovado, limpa o carrinho.
    XACK confirma o processamento para evitar reentrega pelo Consumer Group.
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
    # Consulta o catalog-service de forma assíncrona para não bloquear o event loop
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
    Publica o pedido no stream 'checkout' e retorna imediatamente com status
    'processing'. O cliente deve fazer polling em GET /orders/{order_id} até
    o payment-service processar e publicar o resultado em 'payment_results'.

    Se Idempotency-Key já foi vista, devolve o pedido existente sem republicar.
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
            order_id = idempotency_key
    else:
        order_id = str(uuid.uuid4())[:8].upper()

    items = load_cart(user_id)
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    subtotal    = round(sum(i["unit_price"] * i["quantity"] for i in items), 2)
    pix_discount = round(subtotal * 0.05, 2) if req.payment_method == "pix" else 0
    total        = round(subtotal - pix_discount, 2)
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
    raw = _redis.get(f"order:{order_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Order not found")
    return json.loads(raw)
