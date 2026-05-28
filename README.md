# E-Commerce Microsserviços

## Como Rodar

**Pré-requisito:** Python 3.11+ instalado.

Abra **5 terminais separados** e execute os seguintes comandos em cada:

```bash
# Terminal 1 — Catálogo (porta 8001)
cd catalogo-service
pip install -r requirements.txt
uvicorn main:app --port 8001 --reload

# Terminal 2 — Carrinho (porta 8002)
cd carrinho-service
pip install -r requirements.txt
uvicorn main:app --port 8002 --reload

# Terminal 3 — Pagamento (porta 8003)
cd pagamento-service
pip install -r requirements.txt
uvicorn main:app --port 8003 --reload

# Terminal 4 — API Gateway (porta 8000)
cd gateway-service
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload

# Terminal 5 - Testes

$RID = "demo-mvp-001"
$H = @{ "X-Request-ID" = $RID }
$S = "sessao-demo"

#$RID será usado para rastrear a requisição nos logs.
#$S será o identificador do carrinho.

Invoke-RestMethod -Headers $H http://localhost:8000/services/health | ConvertTo-Json -Depth 5
#Mostra através do Gateway que os serviços estão rodando

```
