# E-Commerce Microsserviços

## Como Rodar

**Pré-requisito:** Python 3.11+ instalado.

Abra **3 terminais separados** e execute um serviço em cada:

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
```

Depois abra o frontend:

```bash
cd frontend
python -m http.server 3000
```

Acesse **http://localhost:3000** no navegador.
