import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Carrinho Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CATALOGO_URL = os.getenv("CATALOGO_URL", "http://localhost:8001")

# Mock storage: session_id -> lista de itens
carrinhos: dict[str, list] = {}


class ItemRequest(BaseModel):
    produto_id: int
    quantidade: int


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "carrinho-service"}


@app.get("/carrinho/{session_id}")
def ver_carrinho(session_id: str):
    itens = carrinhos.get(session_id, [])
    total = sum(i["preco_unitario"] * i["quantidade"] for i in itens)
    return {"session_id": session_id, "itens": itens, "total": round(total, 2)}


@app.post("/carrinho/{session_id}/adicionar")
def adicionar_item(session_id: str, item: ItemRequest):
    if item.quantidade <= 0:
        raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

    # Valida produto consultando o catálogo
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{CATALOGO_URL}/produtos/{item.produto_id}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Catálogo service indisponível")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Produto não encontrado no catálogo")

    produto = resp.json()

    if item.quantidade > produto["estoque"]:
        raise HTTPException(
            status_code=400,
            detail=f"Estoque insuficiente. Disponível: {produto['estoque']}"
        )

    carrinho = carrinhos.setdefault(session_id, [])

    for i in carrinho:
        if i["produto_id"] == item.produto_id:
            i["quantidade"] += item.quantidade
            total = sum(x["preco_unitario"] * x["quantidade"] for x in carrinho)
            return {"mensagem": "Quantidade atualizada", "itens": carrinho, "total": round(total, 2)}

    carrinho.append({
        "produto_id": produto["id"],
        "nome": produto["nome"],
        "preco_unitario": produto["preco"],
        "quantidade": item.quantidade,
    })

    total = sum(i["preco_unitario"] * i["quantidade"] for i in carrinho)
    return {"mensagem": "Item adicionado com sucesso", "itens": carrinho, "total": round(total, 2)}


@app.delete("/carrinho/{session_id}/remover/{produto_id}")
def remover_item(session_id: str, produto_id: int):
    if session_id not in carrinhos:
        raise HTTPException(status_code=404, detail="Carrinho não encontrado")

    original = len(carrinhos[session_id])
    carrinhos[session_id] = [i for i in carrinhos[session_id] if i["produto_id"] != produto_id]

    if len(carrinhos[session_id]) == original:
        raise HTTPException(status_code=404, detail="Produto não está no carrinho")

    total = sum(i["preco_unitario"] * i["quantidade"] for i in carrinhos[session_id])
    return {"mensagem": "Item removido", "itens": carrinhos[session_id], "total": round(total, 2)}


@app.delete("/carrinho/{session_id}")
def limpar_carrinho(session_id: str):
    carrinhos.pop(session_id, None)
    return {"mensagem": "Carrinho limpo com sucesso"}
