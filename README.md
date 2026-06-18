# E-Commerce Microsserviços

Plataforma de e-commerce construída com arquitetura de microsserviços usando **FastAPI**, **PostgreSQL**, **Redis** e **Vanilla JS**.

## Arquitetura

```
Frontend nginx (porta 4000)
    │
    ├── auth-service     (porta 8004)  → PostgreSQL — cadastro e login
    ├── gateway-service  (porta 8000)  → Proxy reverso + validação JWT + logging
    │
    └── Traefik (load balancer)
            ├── catalog-service  (porta 8001)  → PostgreSQL + Redis Consumer
            ├── cart-service     (porta 8002)  → Redis (carrinho + checkout async)
            └── payment-service  (porta 8003)  → PostgreSQL + Redis Stream Publisher
```

### Fluxo do checkout (assíncrono)

```
Usuário clica "Finalizar compra"
    → cart-service publica pedido no stream 'checkout'
    → retorna order_id imediatamente (status: processing)
    → frontend faz polling em GET /orders/{order_id}

    (em paralelo, assincronamente)
    → payment-service consome 'checkout', processa, persiste no PostgreSQL
    → publica resultado em 'payment_results' → cart-service atualiza status
    → publica 'payment_approved' em 'payments' → catalog-service decrementa estoque
```

A validação de estoque antes de adicionar ao carrinho é **síncrona** (HTTP direto ao catalog-service); o checkout em si é **assíncrono** via Redis Streams.

## Estrutura de pastas

```
ecommerce-microservices/
├── docker-compose.yml
├── gateway-service/        # API Gateway com proxy reverso, JWT e logging
│   ├── main.py
│   └── requirements.txt
├── auth-service/           # Cadastro, login e emissão de tokens JWT
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── catalog-service/        # Catálogo de produtos e controle de estoque
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── cart-service/           # Carrinho (Redis) e orquestração do checkout
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
    ├── images/
    └── Dockerfile
```

## Como rodar

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado e em execução

### 1 comando para subir tudo

```bash
docker compose up --build
```

> Na primeira execução o Docker baixa as imagens base e instala dependências (~2–3 min). Nas próximas execuções será muito mais rápido.

### Acessar o site

**http://localhost:4000**

### Outros endpoints disponíveis

| Serviço | URL | Documentação interativa |
|---|---|---|
| Gateway | http://localhost:8000 | http://localhost:8000/docs |
| Auth | http://localhost:8004 | http://localhost:8004/docs |
| Catálogo | http://localhost:8001 | http://localhost:8001/docs |
| Carrinho | http://localhost:8002 | http://localhost:8002/docs |
| Pagamento | http://localhost:8003 | http://localhost:8003/docs |
| Traefik dashboard | http://localhost:9090 | — |

### Parar os serviços

```bash
docker compose down
```

Para parar **e apagar os dados** (bancos zerados):

```bash
docker compose down -v
```

### Escalar um serviço

```bash
docker compose up --scale catalog-service=3 --scale cart-service=2
```

O Traefik detecta automaticamente as novas réplicas e distribui as requisições entre elas.

---

## Como rodar manualmente

**Pré-requisitos:** Python 3.11+, PostgreSQL 15, Redis 7.

### 1. Iniciar infraestrutura

```bash
docker compose up postgres-catalog postgres-payment postgres-auth redis
```

### 2. Subir cada serviço em terminais separados

```bash
# Terminal 1 — Auth (porta 8004)
cd auth-service && pip install -r requirements.txt && uvicorn main:app --port 8004 --reload

# Terminal 2 — Catálogo (porta 8001)
cd catalog-service && pip install -r requirements.txt && uvicorn main:app --port 8001 --reload

# Terminal 3 — Carrinho (porta 8002)
cd cart-service && pip install -r requirements.txt && uvicorn main:app --port 8002 --reload

# Terminal 4 — Pagamento (porta 8003)
cd payment-service && pip install -r requirements.txt && uvicorn main:app --port 8003 --reload

# Terminal 5 — Gateway (porta 8000)
cd gateway-service && pip install -r requirements.txt && uvicorn main:app --port 8000 --reload

# Terminal 6 — Frontend
cd frontend && python -m http.server 4000
```

Acesse **http://localhost:4000**.

---

## Serviços

### auth-service (porta 8004)

Cadastro, login e emissão de tokens JWT (HS256, expiração em 24h).
O token é verificado localmente por cada microsserviço — sem chamada de rede ao auth-service na hora de validar.

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| POST | `/auth/register` | — | Cria conta de usuário |
| POST | `/auth/login` | — | Autentica e retorna JWT |
| GET | `/auth/me` | JWT | Dados do usuário autenticado |
| GET | `/health` | — | Health check |

### gateway-service (porta 8000)

Ponto de entrada único. Valida o JWT antes de rotear (1ª camada), propaga `X-Request-ID` para rastreamento nos logs e faz proxy reverso para os serviços internos.

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Health check do gateway |
| GET | `/services/health` | Health check de todos os serviços em paralelo |

### catalog-service (porta 8001)

Gerencia o catálogo de produtos. Operações de escrita exigem role `admin`.
Banco: PostgreSQL (porta 5434). Consome o stream `payments` para decrementar estoque após pagamento aprovado.

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| GET | `/products` | — | Lista produtos (paginação + filtro por categoria) |
| GET | `/products/{id}` | — | Detalhes de um produto |
| POST | `/products` | admin | Cadastra produto |
| PUT | `/products/{id}/stock` | admin | Atualiza estoque |
| GET | `/health` | — | Health check |

### cart-service (porta 8002)

Armazena o carrinho no Redis (TTL 24h) e orquestra o checkout de forma assíncrona via Redis Streams. Suporta chave de idempotência (`Idempotency-Key`) para evitar pedidos duplicados em retries.

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| GET | `/cart/{user_id}` | JWT | Retorna o carrinho |
| POST | `/cart/{user_id}/items` | JWT | Adiciona item (valida estoque no catalog-service) |
| DELETE | `/cart/{user_id}/items/{product_id}` | JWT | Remove item |
| POST | `/cart/{user_id}/checkout` | JWT | Inicia checkout assíncrono |
| GET | `/orders/{order_id}` | — | Consulta status do pedido (usado para polling) |
| GET | `/health` | — | Health check |

### payment-service (porta 8003)

Processa pagamentos consumindo o stream `checkout`, persiste no PostgreSQL e publica resultados.
Banco: PostgreSQL (porta 5435). Métodos aceitos: `pix`, `card`, `boleto`.
Aprovação simulada: 90% para PIX e cartão; boleto sempre retorna `pending`.
Desconto de 5% aplicado automaticamente para pagamentos via PIX.

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| POST | `/payments` | JWT | Processa pagamento (endpoint HTTP direto) |
| GET | `/payments/{payment_id}` | JWT | Detalhes do pagamento |
| GET | `/payments/user/{user_id}` | JWT | Histórico do usuário |
| GET | `/health` | — | Health check |

---

## Redis Streams

| Stream | Publicador | Consumidor | Evento |
|---|---|---|---|
| `checkout` | cart-service | payment-service | Pedido solicitado |
| `payment_results` | payment-service | cart-service | Resultado do pagamento |
| `payments` | payment-service | catalog-service | Pagamento aprovado (decrementa estoque) |

Consumer Groups garantem que cada mensagem seja processada uma única vez mesmo com múltiplas réplicas rodando. XACK confirma o processamento; mensagens não confirmadas são reenviadas automaticamente em caso de falha.

---

## Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | FastAPI (Python 3.12) |
| Banco relacional | PostgreSQL 15 |
| Cache / mensageria | Redis 7 (Redis Streams) |
| Frontend | Vanilla JS + Bootstrap 5.3 |
| Containerização | Docker + Docker Compose |
| Load balancer | Traefik v3 |
| Servidor HTTP | Uvicorn (APIs) / Nginx (frontend) |

## Padrões utilizados

- **Microsserviços** — cada serviço tem seu próprio banco e ciclo de deploy independente
- **API Gateway** — ponto de entrada único com JWT, logging e rastreamento por X-Request-ID
- **Event-driven** — checkout assíncrono via Redis Streams desacopla cart, payment e catalog
- **Consistência eventual** — estoque decrementado após confirmação de pagamento via evento
- **Consumer Group** — múltiplas réplicas compartilham o stream sem processar a mesma mensagem duas vezes
- **Idempotência** — chave por checkout evita cobrança duplicada em retries de rede
- **JWT stateless** — token verificado localmente em cada serviço, sem dependência do auth-service em tempo de requisição
- **TTL automático** — carrinhos abandonados expiram em 24h sem necessidade de cron job
