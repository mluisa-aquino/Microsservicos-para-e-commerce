import os
import time
import logging
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

PORT = int(os.getenv("PORT", "8001"))

app = FastAPI(title="Catálogo Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("catalogo-service")

PRODUTOS = [
    {"id": 1, "nome": "Notebook", "preco": 3500.00, "estoque": 10, "categoria": "Informática"},
    {"id": 2, "nome": "Mouse", "preco": 450.00, "estoque": 50, "categoria": "Periféricos"},
    {"id": 3, "nome": "Teclado", "preco": 350.00, "estoque": 30, "categoria": "Periféricos"},
    {"id": 4, "nome": "Monitor", "preco": 1200.00, "estoque": 15, "categoria": "Monitores"},
    {"id": 5, "nome": "Headset", "preco": 600.00, "estoque": 25, "categoria": "Áudio"},
]


@app.middleware("http")
async def add_request_id_and_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
    started_at = time.perf_counter()

    logger.info("[%s] Entrada %s %s", request_id, request.method, request.url.path)
    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "[%s] Saida %s %s -> %s em %.2fms",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


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
