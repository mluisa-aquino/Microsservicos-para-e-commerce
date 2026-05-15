import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

PORT = int(os.getenv("PORT", "8001"))

app = FastAPI(title="Catálogo Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PRODUTOS = [
    {"id": 1, "nome": "Notebook Dell Inspiron", "preco": 3500.00, "estoque": 10, "categoria": "Informática"},
    {"id": 2, "nome": "Mouse Logitech MX Master", "preco": 450.00, "estoque": 50, "categoria": "Periféricos"},
    {"id": 3, "nome": "Teclado Mecânico Redragon", "preco": 350.00, "estoque": 30, "categoria": "Periféricos"},
    {"id": 4, "nome": "Monitor LG 24\" Full HD", "preco": 1200.00, "estoque": 15, "categoria": "Monitores"},
    {"id": 5, "nome": "Headset HyperX Cloud", "preco": 600.00, "estoque": 25, "categoria": "Áudio"},
]


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "catalogo-service"}


@app.get("/produtos")
def listar_produtos(categoria: str = None):
    if categoria:
        filtrados = [p for p in PRODUTOS if p["categoria"].lower() == categoria.lower()]
        return {"total": len(filtrados), "produtos": filtrados}
    return {"total": len(PRODUTOS), "produtos": PRODUTOS}


@app.get("/produtos/{produto_id}")
def buscar_produto(produto_id: int):
    produto = next((p for p in PRODUTOS if p["id"] == produto_id), None)
    if not produto:
        raise HTTPException(status_code=404, detail=f"Produto {produto_id} não encontrado")
    return produto
