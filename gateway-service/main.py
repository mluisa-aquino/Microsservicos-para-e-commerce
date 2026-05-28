import os
import time
import logging
import uuid
import httpx
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="API Gateway")

CATALOGO_URL = os.getenv("CATALOGO_URL", "http://localhost:8001")
CARRINHO_URL = os.getenv("CARRINHO_URL", "http://localhost:8002")
PAGAMENTO_URL = os.getenv("PAGAMENTO_URL", "http://localhost:8003")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("api-gateway")


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
    return {"status": "ok", "service": "gateway-service"}


@app.get("/services/health")
async def services_health(request: Request):
    services = {
        "catalogo-service": CATALOGO_URL,
        "carrinho-service": CARRINHO_URL,
        "pagamento-service": PAGAMENTO_URL,
    }

    result = {}
    request_id = request.state.request_id
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in services.items():
            try:
                response = await client.get(f"{url}/health", headers={"x-request-id": request_id})
                result[name] = {
                    "status_code": response.status_code,
                    "response": response.json(),
                }
            except httpx.RequestError as exc:
                result[name] = {
                    "status_code": 503,
                    "error": str(exc),
                }

    all_ok = all(service["status_code"] == 200 for service in result.values())
    return {"status": "ok" if all_ok else "degraded", "services": result}


async def proxy(request: Request, service_name: str, base_url: str, path: str):
    request_id = request.state.request_id
    started_at = time.perf_counter()
    target_url = f"{base_url}{path}"
    logger.info(
        "[%s] Gateway roteando %s %s -> %s",
        request_id,
        request.method,
        request.url.path,
        target_url,
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        body = await request.body()
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() != "host"
        }
        headers["x-request-id"] = request_id

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
                request_id,
                service_name,
                request.method,
                request.url.path,
                elapsed_ms,
                exc,
            )
            raise HTTPException(status_code=503, detail=f"{service_name} indisponivel")

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "[%s] %s respondeu %s para %s %s em %.2fms",
        request_id,
        service_name,
        response.status_code,
        request.method,
        request.url.path,
        elapsed_ms,
    )

    proxy_response = Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )
    proxy_response.headers["X-Request-ID"] = request_id
    return proxy_response


@app.api_route("/produtos", methods=["GET"])
@app.api_route("/produtos/{path:path}", methods=["GET"])
async def catalogo_proxy(request: Request, path: str = ""):
    suffix = f"/produtos/{path}" if path else "/produtos"
    return await proxy(request, "catalogo-service", CATALOGO_URL, suffix)


@app.api_route("/carrinho/{path:path}", methods=["GET", "POST", "DELETE"])
async def carrinho_proxy(request: Request, path: str):
    return await proxy(request, "carrinho-service", CARRINHO_URL, f"/carrinho/{path}")


@app.api_route("/pagamento", methods=["GET"])
@app.api_route("/pagamento/{path:path}", methods=["GET", "POST"])
async def pagamento_proxy(request: Request, path: str = ""):
    suffix = f"/pagamento/{path}" if path else "/pagamento"
    return await proxy(request, "pagamento-service", PAGAMENTO_URL, suffix)
