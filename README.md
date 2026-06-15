# E-Commerce Microservices

Plataforma de e-commerce construída com arquitetura de microsserviços usando **FastAPI**, **PostgreSQL**, **Redis** e **Vanilla JS**.

## Arquitetura

```
Frontend (porta 3000)
    │
    ├── gateway-service  (porta 8000)  → Proxy reverso + logging de requests
    ├── catalog-service  (porta 8001)  → PostgreSQL + Redis Consumer
    ├── cart-service     (porta 8002)  → Redis (armazenamento do carrinho)
    └── payment-service  (porta 8003)  → PostgreSQL + Redis Stream Publisher
```

### Fluxo de dados

```
Usuário clica "Ir para o pagamento"
    → cart-service valida estoque no catalog-service
    → cart-service chama payment-service
    → payment-service persiste o pedido no PostgreSQL
    → payment-service publica evento no Redis Stream ("payments")
    → catalog-service consome o evento e decrementa o estoque (consistência eventual)
```

## Estrutura de pastas

```
ecommerce-microservices/
├── docker-compose.yml
├── gateway-service/        # API Gateway com logging e proxy reverso
│   ├── main.py
│   └── requirements.txt
├── catalog-service/        # Gerenciamento do catálogo de produtos
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── cart-service/           # Carrinho de compras (Redis)
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── payment-service/        # Processamento de pagamentos
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
└── frontend/               # SPA em Vanilla JS + Bootstrap 5
    ├── index.html
    ├── app.js
    └── Dockerfile
```

## Como rodar (Docker — recomendado)

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado e em execução

### 1 comando para subir tudo

```bash
docker compose up --build
```

> Na primeira execução, o Docker irá baixar as imagens base e instalar as dependências (~2–3 min). Nas próximas execuções será muito mais rápido.

### Acessar o site

**http://localhost:3000**

### Outros endpoints disponíveis

| Serviço | URL | Documentação interativa |
|---|---|---|
| Gateway | http://localhost:8000 | http://localhost:8000/docs |
| Catálogo | http://localhost:8001 | http://localhost:8001/docs |
| Carrinho | http://localhost:8002 | http://localhost:8002/docs |
| Pagamento | http://localhost:8003 | http://localhost:8003/docs |

### Parar os serviços

```bash
docker compose down
```

Para parar **e apagar os dados** (banco de dados zerado):

```bash
docker compose down -v
```

---

## Como rodar manualmente (sem Docker)

**Pré-requisitos:** Python 3.11+, PostgreSQL 15, Redis 7.

### 1. Iniciar infraestrutura (só os bancos)

```bash
docker compose up postgres-catalog postgres-payment redis
```

### 2. Instalar dependências e subir cada serviço em terminais separados

```bash
# Terminal 1 — Catálogo (porta 8001)
cd catalog-service && pip install -r requirements.txt && uvicorn main:app --port 8001 --reload

# Terminal 2 — Carrinho (porta 8002)
cd cart-service && pip install -r requirements.txt && uvicorn main:app --port 8002 --reload

# Terminal 3 — Pagamento (porta 8003)
cd payment-service && pip install -r requirements.txt && uvicorn main:app --port 8003 --reload

# Terminal 4 — API Gateway (porta 8000)
cd gateway-service && pip install -r requirements.txt && uvicorn main:app --port 8000 --reload

# Terminal 5 — Frontend
cd frontend && python -m http.server 3000
```

Acesse **http://localhost:3000**.

---

## Serviços

### gateway-service (porta 8000)

API Gateway com proxy reverso e logging centralizado de requests.

- Adiciona `X-Request-ID` em cada requisição para rastreamento
- Roteia `/produtos`, `/carrinho` e `/pagamento` para os serviços correspondentes

**Endpoints:**

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Health check do gateway |
| GET | `/services/health` | Health check de todos os serviços |

### catalog-service (porta 8001)

Gerencia o catálogo de produtos com controle de estoque.

- Banco: PostgreSQL (porta 5434)
- Consome eventos do Redis Stream `payments` para decrementar estoque após aprovação de pagamento

**Endpoints:**

| Método | Rota | Descrição |
|---|---|---|
| GET | `/products` | Lista produtos (paginação + filtro por categoria) |
| GET | `/products/{id}` | Detalhes de um produto |
| POST | `/products` | Cria produto |
| PUT | `/products/{id}/stock` | Atualiza estoque |
| GET | `/health` | Health check |

### cart-service (porta 8002)

Gerencia o carrinho de compras e orquestra o checkout.

- Armazenamento: Redis (TTL de 24h — carrinhos abandonados expiram automaticamente)
- Valida estoque no catalog-service antes de adicionar item
- Chama o payment-service no checkout

**Endpoints:**

| Método | Rota | Descrição |
|---|---|---|
| GET | `/cart/{user_id}` | Retorna o carrinho do usuário |
| POST | `/cart/{user_id}/items` | Adiciona item ao carrinho |
| DELETE | `/cart/{user_id}/items/{product_id}` | Remove item |
| POST | `/cart/{user_id}/checkout` | Finaliza compra |
| GET | `/health` | Health check |

### payment-service (porta 8003)

Processa pagamentos e persiste os pedidos.

- Banco: PostgreSQL (porta 5435)
- Métodos aceitos: `pix`, `card`, `boleto`
- Simula aprovação com 90% de taxa de sucesso (PIX/Cartão); boleto sempre gera status `pending`
- Publica evento `payment_approved` no Redis Stream após aprovação

**Endpoints:**

| Método | Rota | Descrição |
|---|---|---|
| POST | `/payments` | Processa pagamento |
| GET | `/payments/{payment_id}` | Detalhes do pagamento |
| GET | `/payments/user/{user_id}` | Histórico do usuário |
| GET | `/health` | Health check |

---

## Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | FastAPI (Python 3.12) |
| Banco relacional | PostgreSQL 15 |
| Cache / filas | Redis 7 (Redis Streams) |
| Frontend | Vanilla JS + Bootstrap 5.3 |
| Containerização | Docker + Docker Compose |
| Servidor HTTP | Uvicorn (APIs) / Nginx (frontend) |

## Padrões de projeto utilizados

- **Microsserviços** — cada serviço tem seu próprio banco e processo
- **API Gateway** — ponto de entrada único com logging e rastreamento por request ID
- **Event-driven** — Redis Streams para atualização assíncrona de estoque
- **Consistência eventual** — o estoque é atualizado após a confirmação do pagamento via evento
- **TTL automático** — carrinhos abandonados expiram em 24h sem necessidade de cron job
