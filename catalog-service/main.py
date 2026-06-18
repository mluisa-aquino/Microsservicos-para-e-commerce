"""
Catalog Service — porta 8001

Gerencia o catálogo de produtos com persistência em PostgreSQL.
Operações de escrita exigem role 'admin', verificada via JWT.

O estoque é decrementado de forma assíncrona: após o payment-service
aprovar um pagamento, ele publica no stream 'payments'; este serviço
consome esse evento via Consumer Group do Redis e atualiza o banco.
Esse padrão event-driven elimina o acoplamento síncrono entre os serviços.
"""

import os
import json
import socket
import time
import threading
from contextlib import asynccontextmanager

import jwt
import psycopg
import redis
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Configuração via variáveis de ambiente ─────────────────────────────────────
PORT      = int(os.getenv("PORT",    "8001"))
DB_HOST   = os.getenv("DB_HOST",    "localhost")
DB_PORT   = int(os.getenv("DB_PORT", "5434"))
DB_NAME   = os.getenv("DB_NAME",    "catalog")
DB_USER   = os.getenv("DB_USER",    "postgres")
DB_PASS   = os.getenv("DB_PASS",    "postgres")
REDIS_URL = os.getenv("REDIS_URL",  "redis://localhost:6379")

# Mesma chave usada pelo auth-service para assinar os JWTs. A validação aqui
# é local (sem chamar o auth-service): cada serviço verifica a assinatura
# de forma independente, evitando um ponto único de falha na autenticação.
JWT_SECRET    = os.getenv("JWT_SECRET", "shopmicro-dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# Identificadores do Consumer Group no Redis Stream
# O Consumer Group garante que cada mensagem seja processada uma única vez,
# mesmo que múltiplas instâncias deste serviço estejam rodando
STREAM_NAME    = "payments"
CONSUMER_GROUP = "catalog-group"
# Hostname único por container — permite escalar sem conflito no Consumer Group
CONSUMER_NAME  = f"catalog-consumer-{socket.gethostname()}"


def get_db():
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        row_factory=psycopg.rows.dict_row,
    )


PRODUCTS = [
    # Informática
    ("Notebook Dell Inspiron 15", 3499.00,
     "Equipado com processador Intel Core i5 de 12ª geração e 8 GB de RAM DDR4, o Inspiron 15 "
     "entrega desempenho consistente para trabalho, estudo e multitarefa cotidiana. O SSD de 256 GB "
     "garante inicialização em segundos e carregamento ágil de aplicativos. A tela Full HD de 15,6 "
     "polegadas com painel IPS oferece ângulos de visão amplos e cores precisas, ideal para "
     "videoconferências e edição leve de documentos. Com bateria de até 8 horas e design slim de "
     "1,79 kg, é o companheiro perfeito para o dia a dia dentro e fora de casa.",
     10, "Informática"),

    ("MacBook Air M2", 9999.00,
     "O MacBook Air com chip M2 redefine o conceito de laptop fino e leve com desempenho que supera "
     "notebooks concorrentes duas vezes mais caros. O processador M2 de 8 núcleos entrega até 18 horas "
     "de bateria em uso real, sem ventilador — funcionando em silêncio absoluto, ideal para reuniões e "
     "ambientes tranquilos. A tela Liquid Retina de 13,6 polegadas exibe mais de um bilhão de cores com "
     "brilho de 500 nits. O SSD unificado de 256 GB e a RAM de 8 GB compartilham largura de banda para "
     "respostas instantâneas em qualquer aplicativo.",
     5, "Informática"),

    ("SSD Samsung 970 EVO 1TB", 449.00,
     "O 970 EVO utiliza tecnologia V-NAND de 3ª geração e interface NVMe PCIe 3.0 x4 para atingir "
     "velocidades de leitura sequencial de até 3.500 MB/s — transferindo um arquivo de 10 GB em menos "
     "de 3 segundos. Compatível com notebooks e desktops via slot M.2 2280, é a atualização mais "
     "impactante que você pode fazer no seu computador. O software Samsung Magician facilita monitoramento "
     "de saúde, benchmarks e otimizações. Ideal para edição de vídeo 4K, jogos e profissionais que não "
     "podem perder tempo esperando o sistema responder.",
     40, "Informática"),

    # Periféricos
    ("Mouse Logitech MX Master 3S", 599.00,
     "Considerado o melhor mouse de produtividade do mercado, o MX Master 3S possui sensor óptico de "
     "8.000 DPI que funciona em qualquer superfície, incluindo vidro. O scroll MagSpeed eletromagnético "
     "percorre 1.000 linhas por segundo e alterna automaticamente entre modo livre e com cliques. "
     "Conecta-se a até 3 dispositivos via receptor Bolt USB ou Bluetooth com troca instantânea por botão. "
     "O recurso Logitech Flow permite copiar e colar conteúdo entre dois computadores diferentes com um "
     "simples gesto. Ergonomia de referência com suporte para palma completo para sessões longas.",
     35, "Periféricos"),

    ("Teclado Mecânico Keychron K2", 699.00,
     "O Keychron K2 é um teclado mecânico no layout compacto 75%, mantendo as teclas de função e "
     "navegação em um formato 30% menor que os teclados full-size. Os switches Gateron Red lineares "
     "são suaves e silenciosos, ideais para digitação rápida e para não incomodar quem está por perto. "
     "A estrutura hot-swap permite trocar os switches sem solda, possibilitando personalizar a sensação "
     "de digitação a qualquer momento. Compatível com Mac e Windows, com retroiluminação RGB programável "
     "e conexão por cabo USB-C ou Bluetooth 5.1.",
     20, "Periféricos"),

    ("Mousepad Gamer XL Redragon", 89.00,
     "Com dimensões generosas de 900 x 400 mm, este mousepad XXL cobre toda a área da mesa, acomodando "
     "mouse e teclado sem bordas. A superfície de tecido de alta densidade oferece deslize preciso e "
     "controle consistente em qualquer velocidade de movimento, sem desgaste com o uso prolongado. "
     "A base de borracha antiderrapante com 3 mm de espessura mantém o pad fixo mesmo durante partidas "
     "intensas. A borda costurada em todo o perímetro evita descostura e garante durabilidade de longo prazo.",
     100, "Periféricos"),

    # Monitores
    ('Monitor LG 27" 4K UHD', 2499.00,
     "O painel IPS 4K de 27 polegadas entrega resolução de 3840 x 2160 pixels com cobertura de 99% do "
     "espaço de cor sRGB, tornando-o ideal para edição de fotos, design gráfico e produção de vídeo "
     "profissional. O suporte HDR10 expande a faixa de contraste para imagens mais vivas e realistas. "
     "A porta USB-C com fornecimento de energia de 96W carrega o notebook e transmite imagem 4K com um "
     "único cabo. O suporte ergonômico permite ajuste de altura, inclinação, giro e rotação para um "
     "setup perfeitamente adaptado ao seu corpo.",
     8, "Monitores"),

    ('Monitor Samsung Odyssey 27" 165Hz', 1799.00,
     "Projetado para gaming de alto desempenho, o Odyssey G5 tem curvatura de 1000R que envolve o campo "
     "de visão para imersão total. O painel VA QHD de 2560 x 1440 pixels com taxa de atualização de "
     "165 Hz e tempo de resposta de 1 ms elimina ghosting e tearing mesmo nos jogos mais frenéticos. "
     "AMD FreeSync Premium e compatibilidade com G-Sync garantem sincronização adaptativa com qualquer "
     "placa de vídeo. A resolução QHD oferece o equilíbrio perfeito entre imagem nítida e capacidade "
     "de rodar em alto FPS sem exigir uma GPU topo de linha.",
     12, "Monitores"),

    # Áudio
    ("Headset HyperX Cloud III", 699.00,
     "O Cloud III é o resultado de anos de feedback de jogadores profissionais e amadores, com drivers "
     "de 53 mm personalizados que entregam som espacial 7.1 preciso para localizar passos e tiros com "
     "exatidão. O microfone destacável com cancelamento de ruído por IA filtra sons de ambiente e "
     "mantém sua voz clara mesmo em ambientes barulhentos. Construído com arco de alumínio e almofadas "
     "de espuma de memória revestidas em couro sintético, proporciona conforto em sessões de mais de "
     "8 horas. Compatível com PC, PlayStation, Xbox e Nintendo Switch.",
     18, "Áudio"),

    ("Caixa de Som JBL Charge 5", 999.00,
     "A Charge 5 combina som potente e graves profundos com praticidade extrema para uso em qualquer lugar. "
     "Certificada com IP67, é completamente resistente a poeira e suporta imersão em água de até 1 metro "
     "por 30 minutos, perfeita para piscina, praia e trilhas na chuva. Com 20 horas de autonomia e "
     "função power bank integrada, carrega smartphones sem interromper a música. O JBL PartyBoost "
     "permite conectar múltiplas caixas compatíveis para amplificar o som em festas e eventos.",
     22, "Áudio"),

    ("Fone Sony WH-1000XM5", 1999.00,
     "O WH-1000XM5 possui 8 microfones e dois processadores dedicados para o cancelamento de ruído "
     "ativo mais eficiente do mercado, bloqueando ruído de avião, escritório aberto e transporte "
     "público com eficiência impressionante. O suporte a LDAC transmite áudio em alta resolução via "
     "Bluetooth com três vezes mais dados que o BT convencional, entregando qualidade próxima ao sem fio. "
     "Com 30 horas de bateria e carregamento rápido (3 minutos de carga = 3 horas de uso), raramente "
     "fica sem energia. O modo Falar com Voz pausa automaticamente a música ao detectar que você "
     "começou a falar.",
     14, "Áudio"),

    # Celulares
    ("iPhone 15 Pro 128GB", 6299.00,
     "O iPhone 15 Pro é o primeiro iPhone com estrutura de titânio grau 5, mais leve e resistente que "
     "o aço inoxidável das gerações anteriores. O chip A17 Pro com arquitetura de 3 nm entrega gráficos "
     "de nível console com suporte a ray tracing em tempo real, transformando o smartphone em uma "
     "plataforma de jogos de alta performance. A câmera tetraprismática com zoom óptico de 5x captura "
     "detalhes a distâncias antes impossíveis para um smartphone. A chegada do USB-C com velocidade "
     "USB 3 permite transferir vídeos ProRes 4K para o computador em questão de segundos.",
     8, "Celulares"),

    ("Samsung Galaxy S24 Ultra 256GB", 5299.00,
     "O S24 Ultra é o smartphone Android mais completo da Samsung, impulsionado pelo Snapdragon 8 Gen 3 "
     "em parceria exclusiva para a linha Galaxy nos principais mercados. A câmera principal de 200 MP "
     "com zoom óptico espacial de 5x e zoom digital de 100x preserva detalhes impressionantes mesmo "
     "a grandes distâncias. A S Pen integrada com latência de 2,8 ms transforma a tela em uma superfície "
     "de escrita e desenho natural. A tela Dynamic AMOLED 2X de 6,8 polegadas atinge 2.600 nits de "
     "brilho máximo, perfeitamente legível sob sol direto.",
     10, "Celulares"),

    ("Xiaomi Redmi Note 13 Pro 256GB", 1799.00,
     "O Redmi Note 13 Pro democratiza recursos premium com câmera principal de 200 MP com estabilização "
     "óptica de imagem (OIS), entregando fotos nítidas e detalhadas mesmo em baixa luminosidade. "
     "O processador Dimensity 7200 Ultra garante desempenho consistente para multitarefa e jogos com "
     "eficiência energética acima da média. O carregamento turbo de 67W recarrega a bateria de 5.100 mAh "
     "de 0 a 100% em menos de 50 minutos. A tela AMOLED de 6,67 polegadas com 120 Hz oferece rolagem "
     "fluida e cores vivas durante todo o uso diário.",
     30, "Celulares"),

    # Games
    ("Controle PS5 DualSense", 449.00,
     "O DualSense reinventa a experiência de jogo com feedback háptico que simula desde a textura da "
     "grama até o impacto de uma explosão com precisão surpreendente. Os gatilhos adaptáveis L2 e R2 "
     "mudam a resistência dinamicamente conforme a ação do jogo, fazendo você sentir a tensão de um "
     "arco ou a tração de um carro em estrada molhada. O microfone integrado permite comunicação sem "
     "headset em partidas casuais, enquanto o alto-falante interno emite sons do ambiente do jogo. "
     "Carregamento USB-C e bateria de 1.560 mAh garantem longas sessões sem interrupção.",
     25, "Games"),

    ("Nintendo Switch OLED", 3299.00,
     "A edição OLED do Nintendo Switch apresenta uma tela vibrante de 7 polegadas com contraste "
     "infinito e cores mais saturadas e precisas que a versão LCD original. O dock aprimorado agora "
     "conta com porta LAN integrada para conexão cabeada estável na televisão, sem precisar de "
     "adaptadores. Os 64 GB de armazenamento interno e a base ajustável mais larga oferecem mais "
     "espaço e conforto no modo portátil. Compatível com todo o catálogo Nintendo Switch, incluindo "
     "títulos exclusivos como The Legend of Zelda, Mario Kart e Pokemon.",
     7, "Games"),

    ("Headset Gamer Corsair HS80 RGB", 699.00,
     "O HS80 RGB entrega som surround Dolby Atmos com precisão direcional para identificar a posição "
     "exata de inimigos em jogos competitivos. As almofadas de espuma de memória com revestimento em "
     "tecido respirável garantem conforto mesmo após horas de jogo intenso sem abafar os ouvidos. "
     "O microfone omnidirecional destacável com cancelamento de ruído transmite sua voz com clareza "
     "sem captar sons do ambiente ao redor. A conectividade USB com áudio de 24 bits e 96 kHz reproduz "
     "trilhas sonoras originais com qualidade excepcional.",
     15, "Games"),

    # Câmeras
    ("GoPro HERO 12 Black", 2199.00,
     "A HERO 12 Black grava em resolução 5.3K a 60 fps com a estabilização HyperSmooth 6.0, "
     "entregando vídeos suaves mesmo nas atividades mais intensas como mountain bike, surf e paraquedas. "
     "O sensor maior e a nova lente com abertura f/2.5 capturam até duas vezes mais luz em ambientes "
     "com pouca iluminação. Resistente à água até 10 metros sem caixa protetora e com GPS integrado "
     "para mapear rotas e velocidades. Os modos TimeWarp 3.0 e câmera lenta em 4K/120 fps ampliam "
     "as possibilidades criativas para qualquer aventura.",
     10, "Câmeras"),

    ("Canon EOS R50 + 18-45mm", 4299.00,
     "A EOS R50 traz recursos profissionais em um corpo mirrorless compacto de apenas 375 g, ideal "
     "para quem quer dar um salto de qualidade em relação ao celular. O sensor APS-C de 24,2 MP com "
     "sistema AF Dual Pixel II rastreia rostos, olhos e animais automaticamente para fotos e vídeos "
     "sempre em foco, mesmo com o sujeito em movimento. A gravação em 4K com Servo AF contínuo e a "
     "tela Vari-Angle LCD permitem vlogar e filmar ângulos criativos com facilidade. A lente kit "
     "18-45 mm cobre do paisagismo ao retrato, sendo o ponto de partida perfeito para fotografia e "
     "criação de conteúdo.",
     4, "Câmeras"),

    # Armazenamento
    ("Pendrive Kingston 256GB USB 3.2", 129.00,
     "O DataTraveler Exodia atinge velocidades de leitura de até 200 MB/s com a interface USB 3.2 Gen 1, "
     "transferindo um filme em Full HD em menos de 10 segundos. O design compacto com tampa protetora "
     "encaixável na parte traseira do pendrive evita perder o acessório durante o transporte. "
     "Compatível com USB 3.0 e 2.0 para uso em qualquer computador, televisão ou console com entrada USB. "
     "Ideal para backup de documentos, transporte de apresentações e transferência de arquivos entre "
     "dispositivos no dia a dia.",
     80, "Armazenamento"),

    ("HD Externo Seagate Expansion 2TB", 379.00,
     "O Seagate Expansion entrega 2 TB de armazenamento portátil em um formato slim que cabe no bolso, "
     "sem necessidade de fonte de alimentação externa — alimentado diretamente pela porta USB. "
     "A interface USB 3.0 com retrocompatibilidade USB 2.0 permite velocidades de transferência de "
     "até 120 MB/s em computadores compatíveis. Plug-and-play universal: funciona imediatamente em "
     "Windows e macOS sem instalação de drivers ou softwares adicionais. Inclui 4 meses de Adobe "
     "Creative Cloud Photography para edição das fotos e vídeos que você vai armazenar.",
     28, "Armazenamento"),

    ("Cartão MicroSD SanDisk Extreme 256GB", 219.00,
     "O SanDisk Extreme foi projetado especialmente para câmeras de ação, drones e smartphones "
     "avançados, com velocidade de leitura de 190 MB/s e gravação de 130 MB/s para captura fluida "
     "em 4K UHD. As classificações A2, V30 e U3 garantem que aplicativos rodem diretamente do "
     "cartão e que vídeos de alta definição sejam gravados sem quedas de frames. Resistente à água, "
     "temperatura extrema, raios X e quedas, suporta as condições mais adversas em uso outdoor. "
     "Acompanha adaptador SD para compatibilidade com câmeras DSLR e leitores de cartão convencionais.",
     50, "Armazenamento"),
]


def init_db():
    """
    Cria a tabela de produtos se não existir e popula com dados iniciais.
    O UPDATE ao final roda sempre, permitindo atualizar descrições sem recriar o banco.
    """
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id          SERIAL PRIMARY KEY,
                name        VARCHAR(255)  NOT NULL,
                price       NUMERIC(10,2) NOT NULL,
                description TEXT,
                stock       INTEGER       NOT NULL DEFAULT 0,
                category    VARCHAR(100)
            )
        """)
        row = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()
        if row["n"] == 0:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO products (name, price, description, stock, category) VALUES (%s,%s,%s,%s,%s)",
                    PRODUCTS,
                )
        # Sempre atualiza as descrições — permite melhorá-las sem recriar o banco
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE products SET description = %s WHERE name = %s",
                [(p[2], p[0]) for p in PRODUCTS],
            )


def stream_consumer():
    """
    Worker em background que consome eventos 'payment_approved' do Redis Stream.
    Decrementa o estoque de cada item e confirma com XACK para que a mensagem
    não seja reenviada em caso de falha — garantia de processamento exactly-once
    dentro do Consumer Group.
    """
    r = redis.from_url(REDIS_URL, decode_responses=True)

    # Cria o Consumer Group se não existir; ignora erro caso já exista
    try:
        r.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

    while True:
        try:
            # Lê até 10 mensagens, bloqueando por até 2s se não houver nenhuma
            messages = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_NAME: ">"},  # ">" significa: apenas mensagens ainda não entregues
                count=10, block=2000,
            )
            if not messages:
                continue

            for _, msgs in messages:
                for msg_id, data in msgs:
                    if data.get("type") == "payment_approved":
                        items = json.loads(data["items"])
                        with get_db() as conn:
                            for item in items:
                                # Decrementa estoque apenas se houver quantidade suficiente,
                                # evitando estoque negativo mesmo em condições de corrida
                                conn.execute(
                                    """UPDATE products
                                          SET stock = stock - %s
                                        WHERE id = %s AND stock >= %s""",
                                    (item["quantity"], item["product_id"], item["quantity"]),
                                )
                    # Confirma processamento para que a mensagem não seja reentregue
                    r.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)

        except Exception as e:
            print(f"[stream-consumer] erro: {e}")
            time.sleep(1)  # pausa antes de tentar novamente em caso de falha


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    threading.Thread(target=stream_consumer, daemon=True).start()
    yield


app = FastAPI(title="Catalog Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Autenticação ──────────────────────────────────────────────────────────────

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


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


# ── Modelos de entrada ────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    price: float
    description: str
    stock: int
    category: str


class StockUpdate(BaseModel):
    stock: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "catalog-service"}


@app.get("/products")
def list_products(skip: int = 0, limit: int = 20, category: str = None):
    with get_db() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM products WHERE LOWER(category) = LOWER(%s) LIMIT %s OFFSET %s",
                (category, limit, skip),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM products WHERE LOWER(category) = LOWER(%s)", (category,)
            ).fetchone()["n"]
        else:
            rows = conn.execute(
                "SELECT * FROM products LIMIT %s OFFSET %s", (limit, skip)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    return {"total": total, "skip": skip, "limit": limit, "products": rows}


@app.get("/products/{product_id}")
def get_product(product_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return row


@app.post("/products", status_code=201)
def create_product(product: ProductCreate, _: dict = Depends(require_admin)):
    with get_db() as conn:
        row = conn.execute(
            """INSERT INTO products (name, price, description, stock, category)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (product.name, product.price, product.description, product.stock, product.category),
        ).fetchone()
    return row


@app.put("/products/{product_id}/stock")
def update_stock(product_id: int, body: StockUpdate, _: dict = Depends(require_admin)):
    if body.stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot be negative")
    with get_db() as conn:
        row = conn.execute(
            "UPDATE products SET stock = %s WHERE id = %s RETURNING *",
            (body.stock, product_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return row
