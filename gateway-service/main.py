"""
Gateway Service — porta 8000

Ponto de entrada único para todos os microsserviços (padrão API Gateway).
O cliente conhece apenas o endereço do gateway; os endereços internos ficam opacos.

Valida o JWT antes de rotear (1ª camada); cada serviço interno valida de novo
de forma independente (2ª camada — defesa em profundidade).
X-Request-ID é propagado para rastrear uma requisição por todos os serviços nos logs.
"""

import asyncio
import os
import time
import logging
import uuid
import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response

app = FastAPI(title="API Gateway")

# URLs dos serviços internos (sobrescritas via variáveis de ambiente no docker-compose)
CATALOGO_URL  = os.getenv("CATALOGO_URL",  "http://localhost:8001")
CARRINHO_URL  = os.getenv("CARRINHO_URL",  "http://localhost:8002")
PAGAMENTO_URL = os.getenv("PAGAMENTO_URL", "http://localhost:8003")
AUTH_URL      = os.getenv("AUTH_URL",      "http://localhost:8004")

# Mesma chave usada pelo auth-service para assinar os JWTs. O gateway valida
# o token antes mesmo de rotear a requisição (1ª camada); o serviço interno
# valida de novo de forma independente (2ª camada) — defesa em profundidade.
JWT_SECRET    = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# Configuração do logger centralizado do gateway
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("api-gateway")


@app.middleware("http")
async def add_request_id_and_log(request: Request, call_next):
    """
    Gera ou propaga o X-Request-ID e registra cada requisição com tempo de resposta.
    Se o cliente enviar o header, ele é reutilizado; caso contrário, um novo é gerado.
    """
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


def require_auth(authorization: str = Header(None)) -> dict:
    # 1ª camada de validação; o serviço interno ainda valida de forma independente
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "gateway-service"}


@app.get("/services/health")
async def services_health(request: Request):
    """
    Verifica todos os microsserviços em paralelo via asyncio.gather.
    Retorna 'degraded' se qualquer serviço estiver fora do ar.
    """
    services = {
        "catalogo-service": CATALOGO_URL,
        "carrinho-service": CARRINHO_URL,
        "pagamento-service": PAGAMENTO_URL,
        "auth-service": AUTH_URL,
    }

    request_id = request.state.request_id

    # asyncio.gather dispara todos em paralelo: tempo de resposta = max(latências), não soma
    async with httpx.AsyncClient(timeout=3.0) as client:
        async def check(name: str, url: str):
            try:
                response = await client.get(
                    f"{url}/health",
                    headers={"x-request-id": request_id},
                )
                return name, {"status_code": response.status_code, "response": response.json()}
            except httpx.RequestError as exc:
                return name, {"status_code": 503, "error": str(exc)}

        pairs = await asyncio.gather(*(check(name, url) for name, url in services.items()))

    result = dict(pairs)
    all_ok = all(service["status_code"] == 200 for service in result.values())
    return {"status": "ok" if all_ok else "degraded", "services": result}


async def proxy(request: Request, service_name: str, base_url: str, path: str):
    """
    Encaminha a requisição para o serviço interno preservando método, params,
    headers e body. Remove o header 'host' para evitar conflito com o destino.
    """
    request_id = request.state.request_id
    started_at = time.perf_counter()
    target_url = f"{base_url}{path}"

    logger.info(
        "[%s] Gateway roteando %s %s -> %s",
        request_id, request.method, request.url.path, target_url,
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        body = await request.body()

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() != "host"
        }
        headers["x-request-id"] = request_id  # propaga rastreamento

        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.error(
                "[%s] %s indisponivel para %s %s em %.2fms: %s",
                request_id, service_name, request.method, request.url.path, elapsed_ms, exc,
            )
            raise HTTPException(status_code=503, detail=f"{service_name} indisponivel")

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "[%s] %s respondeu %s para %s %s em %.2fms",
        request_id, service_name, response.status_code,
        request.method, request.url.path, elapsed_ms,
    )

    proxy_response = Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )
    proxy_response.headers["X-Request-ID"] = request_id
    return proxy_response


# ── Rotas de proxy ─────────────────────────────────────────────────────────────

@app.api_route("/produtos", methods=["GET"])
@app.api_route("/produtos/{path:path}", methods=["GET"])
async def catalogo_proxy(request: Request, path: str = ""):
    suffix = f"/produtos/{path}" if path else "/produtos"
    return await proxy(request, "catalogo-service", CATALOGO_URL, suffix)


@app.api_route("/carrinho/{path:path}", methods=["GET", "POST", "DELETE"])
async def carrinho_proxy(request: Request, path: str, _: dict = Depends(require_auth)):
    return await proxy(request, "carrinho-service", CARRINHO_URL, f"/carrinho/{path}")


@app.api_route("/pagamento", methods=["GET"])
@app.api_route("/pagamento/{path:path}", methods=["GET", "POST"])
async def pagamento_proxy(request: Request, path: str = "", _: dict = Depends(require_auth)):
    suffix = f"/pagamento/{path}" if path else "/pagamento"
    return await proxy(request, "pagamento-service", PAGAMENTO_URL, suffix)


@app.api_route("/auth/{path:path}", methods=["GET", "POST"])
async def auth_proxy(request: Request, path: str):
    # Sem exigência de token: é aqui que o token é obtido (login/registro)
    return await proxy(request, "auth-service", AUTH_URL, f"/auth/{path}")
