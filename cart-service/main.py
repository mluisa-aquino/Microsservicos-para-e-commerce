"""
Cart Service
------------
Responsável por gerenciar o carrinho de compras dos usuários
e orquestrar o fluxo de checkout.

Funcionalidades:
- Armazenamento do carrinho no Redis com TTL de 24 horas
- Validação de estoque em tempo real via catalog-service
- Orquestração do pagamento via payment-service
- Carrinhos abandonados expiram automaticamente (sem necessidade de cron job)

Porta padrão: 8002
"""

import os
import json

import httpx
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
CATALOG_URL = os.getenv("CATALOG_URL", "http://localhost:8001")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://localhost:8003")
REDIS_URL   = os.getenv("REDIS_URL",   "redis://localhost:6379")

# TTL do carrinho: 24 horas em segundos.
# Após esse período sem atividade, o Redis remove automaticamente a chave,
# evitando acúmulo de dados de sessões abandonadas.
CART_TTL = 86400

# Conexão Redis compartilhada (thread-safe para operações síncronas)
_redis = redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="Cart Service", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _key(user_id: str) -> str:
    """Gera a chave Redis para o carrinho de um usuário."""
    return f"cart:{user_id}"


def load_cart(user_id: str) -> list:
    """
    Carrega o carrinho do Redis.
    Retorna lista vazia se o carrinho não existir ou tiver expirado.
    """
    raw = _redis.get(_key(user_id))
    return json.loads(raw) if raw else []


def save_cart(user_id: str, items: list):
    """
    Persiste o carrinho no Redis.
    - Se a lista estiver vazia, remove a chave (carrinho esvaziado após checkout)
    - Caso contrário, salva com TTL renovado a cada modificação
    """
    if items:
        _redis.setex(_key(user_id), CART_TTL, json.dumps(items))
    else:
        _redis.delete(_key(user_id))


# ── Modelos de entrada ────────────────────────────────────────────────────────

class ItemRequest(BaseModel):
    """Dados para adicionar um item ao carrinho."""
    product_id: int
    quantity: int


class CheckoutRequest(BaseModel):
    """Dados para iniciar o checkout."""
    payment_method: str  # 'pix', 'card' ou 'boleto'


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Verifica se o serviço está operacional."""
    return {"status": "ok", "service": "cart-service"}


@app.get("/cart/{user_id}")
def get_cart(user_id: str):
    """
    Retorna o carrinho atual do usuário com total calculado.
    O user_id é gerado no frontend e persistido no localStorage do browser.
    """
    items = load_cart(user_id)
    total = sum(i["unit_price"] * i["quantity"] for i in items)
    return {"user_id": user_id, "items": items, "total": round(total, 2)}


@app.post("/cart/{user_id}/items")
def add_item(user_id: str, item: ItemRequest):
    """
    Adiciona um item ao carrinho após validar disponibilidade no catalog-service.

    Fluxo de validação:
    1. Consulta o catalog-service para obter dados e estoque do produto
    2. Verifica se a quantidade solicitada está disponível
    3. Se o produto já está no carrinho, incrementa a quantidade
    4. Caso contrário, adiciona como novo item

    Preço é capturado no momento da adição para evitar divergências
    caso o preço mude antes do checkout.
    """
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    # Consulta síncrona ao catalog-service para validar estoque em tempo real
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{CATALOG_URL}/products/{item.product_id}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Catalog service unavailable")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Product not found in catalog")

    product = resp.json()

    # Valida se há estoque suficiente antes de adicionar ao carrinho
    if item.quantity > product["stock"]:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock. Available: {product['stock']}",
        )

    cart = load_cart(user_id)

    # Verifica se o produto já está no carrinho para apenas incrementar quantidade
    for i in cart:
        if i["product_id"] == item.product_id:
            i["quantity"] += item.quantity
            save_cart(user_id, cart)
            total = sum(x["unit_price"] * x["quantity"] for x in cart)
            return {"message": "Quantity updated", "items": cart, "total": round(total, 2)}

    # Produto novo no carrinho: snapshot do preço atual
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
def remove_item(user_id: str, product_id: int):
    """Remove um item específico do carrinho pelo product_id."""
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
def checkout(user_id: str, req: CheckoutRequest):
    """
    Orquestra o processo de checkout:
    1. Valida que o carrinho não está vazio
    2. Envia o pedido para o payment-service processar
    3. Se aprovado, esvazia o carrinho automaticamente

    O cart-service age como orquestrador: não processa o pagamento diretamente,
    mas coordena a chamada ao serviço especializado.
    """
    items = load_cart(user_id)
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{PAYMENT_URL}/payments",
                json={"user_id": user_id, "items": items, "payment_method": req.payment_method},
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Payment service unavailable")

    order = resp.json()

    # Limpa o carrinho somente após confirmação de aprovação
    # Pedidos pendentes (boleto) ou recusados mantêm o carrinho intacto
    if order.get("status") == "approved":
        save_cart(user_id, [])

    return order
