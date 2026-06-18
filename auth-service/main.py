"""
Auth Service — porta 8004

Cadastro, login e emissão de tokens JWT (HS256).
O token é verificado localmente por cada microsserviço usando a mesma
JWT_SECRET — sem chamada de rede ao auth-service na hora de validar.
Isso elimina dependência em tempo de requisição e evita ponto único de falha.
"""

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
PORT       = int(os.getenv("PORT",    "8004"))
DB_HOST    = os.getenv("DB_HOST",    "localhost")
DB_PORT    = int(os.getenv("DB_PORT", "5436"))
DB_NAME    = os.getenv("DB_NAME",    "auth")
DB_USER    = os.getenv("DB_USER",    "postgres")
DB_PASS    = os.getenv("DB_PASS",    "postgres")

# Chave usada para assinar/verificar os JWTs. Em produção deve vir de um
# secret manager; aqui usamos uma env var compartilhada entre os serviços.
JWT_SECRET     = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM  = "HS256"
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "1440"))  # 24h, igual ao TTL do carrinho

ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@shopmicro.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


def get_db():
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,
    )


def init_db():
    # Cria a tabela de usuários e garante uma conta admin padrão para testes
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                email         VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role          VARCHAR(20)  NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = conn.execute(
            "SELECT id FROM users WHERE email = %s", (ADMIN_EMAIL,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (%s,%s,'admin')",
                (ADMIN_EMAIL, _hash_password(ADMIN_PASSWORD)),
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Auth Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Senhas e tokens ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _create_token(user_id: int, email: str, role: str) -> str:
    payload = {
        "sub":   str(user_id),
        "email": email,
        "role":  role,
        "exp":   datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN),
        "iat":   datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Modelos de entrada ────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one number")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "auth-service"}


@app.post("/auth/register", status_code=201)
def register(req: RegisterRequest):
    # Novas contas sempre têm role 'user'; promoção a admin é feita direto no banco
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = %s", (req.email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        row = conn.execute(
            """INSERT INTO users (email, password_hash, role)
               VALUES (%s,%s,'user') RETURNING id, email, role""",
            (req.email, _hash_password(req.password)),
        ).fetchone()

    token = _create_token(row["id"], row["email"], row["role"])
    return {"access_token": token, "token_type": "bearer", "user": row}


@app.post("/auth/login")
def login(req: LoginRequest):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, role FROM users WHERE email = %s",
            (req.email,),
        ).fetchone()

    if not row or not _verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = _create_token(row["id"], row["email"], row["role"])
    return {
        "access_token": token,
        "token_type":    "bearer",
        "user": {"id": row["id"], "email": row["email"], "role": row["role"]},
    }


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"id": user["sub"], "email": user["email"], "role": user["role"]}
