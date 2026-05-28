import os
import time
import logging
import uuid
import random
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PORT = int(os.getenv("PORT", "8003"))

app = FastAPI(title="Pagamento Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("pagamento-service")

pedidos: dict[str, dict] = {}

METODOS_ACEITOS = {"cartao", "pix", "boleto"}


class ItemPedido(BaseModel):
    produto_id: int
    nome: str
    preco_unitario: float
    quantidade: int


class CheckoutRequest(BaseModel):
    session_id: str
    itens: list[ItemPedido]
    metodo_pagamento: str


@app.middleware("http")
async def add_request_id_and_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
    request.state.request_id = request_id
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
    return {"status": "ok", "service": "pagamento-service"}


@app.post("/pagamento/checkout", status_code=201)
def checkout(req: CheckoutRequest):
    if not req.itens:
        raise HTTPException(status_code=400, detail="Carrinho não pode estar vazio")

    if req.metodo_pagamento not in METODOS_ACEITOS:
        raise HTTPException(
            status_code=400,
            detail=f"Método inválido. Use: {', '.join(METODOS_ACEITOS)}"
        )

    total = sum(i.preco_unitario * i.quantidade for i in req.itens)
    pedido_id = str(uuid.uuid4())[:8].upper()

    # Simula aprovação: 90% de sucesso, boleto sempre pendente
    if req.metodo_pagamento == "boleto":
        status = "pendente"
        mensagem = "Boleto gerado. Pagamento confirmado em até 3 dias úteis."
    elif random.random() > 0.1:
        status = "aprovado"
        mensagem = "Pagamento aprovado com sucesso!"
    else:
        status = "recusado"
        mensagem = "Pagamento recusado. Verifique os dados e tente novamente."

    pedido = {
        "pedido_id": pedido_id,
        "session_id": req.session_id,
        "itens": [i.model_dump() for i in req.itens],
        "total": round(total, 2),
        "metodo_pagamento": req.metodo_pagamento,
        "status": status,
        "mensagem": mensagem,
        "criado_em": datetime.now().isoformat(),
    }

    pedidos[pedido_id] = pedido
    return pedido


@app.get("/pagamento/{pedido_id}")
def consultar_pedido(pedido_id: str):
    pedido = pedidos.get(pedido_id.upper())
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return pedido


@app.get("/pagamento")
def listar_pedidos():
    return {"total": len(pedidos), "pedidos": list(pedidos.values())}
