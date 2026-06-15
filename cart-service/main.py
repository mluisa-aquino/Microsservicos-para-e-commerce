import os
import json

import httpx
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

CATALOG_URL = os.getenv("CATALOG_URL", "http://localhost:8001")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://localhost:8003")
REDIS_URL   = os.getenv("REDIS_URL",   "redis://localhost:6379")

CART_TTL = 86400  # 24 hours — abandoned carts expire automatically

_redis = redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="Cart Service", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


# ── Models ────────────────────────────────────────────────────────────────────

class ItemRequest(BaseModel):
    product_id: int
    quantity: int


class CheckoutRequest(BaseModel):
    payment_method: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "cart-service"}


@app.get("/cart/{user_id}")
def get_cart(user_id: str):
    items = load_cart(user_id)
    total = sum(i["unit_price"] * i["quantity"] for i in items)
    return {"user_id": user_id, "items": items, "total": round(total, 2)}


@app.post("/cart/{user_id}/items")
def add_item(user_id: str, item: ItemRequest):
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{CATALOG_URL}/products/{item.product_id}")
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
def remove_item(user_id: str, product_id: int):
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

    if order.get("status") == "approved":
        save_cart(user_id, [])

    return order
