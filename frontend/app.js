/**
 * app.js — SPA em Vanilla JS que se comunica diretamente com os microsserviços.
 * Cada seção (Boot, Catalog, Cart, Checkout, Admin, Auth) consome um serviço distinto,
 * ilustrando o acoplamento fraco característico da arquitetura de microsserviços.
 */

/** URLs base dos microsserviços */
const API = {
    catalog: 'http://localhost:8001',
    cart:    'http://localhost:8002',
    payment: 'http://localhost:8003',
    auth:    'http://localhost:8004',
};

// Token JWT e perfil persistidos no localStorage para sobreviver a refresh.
// O user_id usado pelo cart-service e payment-service é o campo 'sub' do token.
let authToken   = localStorage.getItem('auth_token') || null;
let currentUser = JSON.parse(localStorage.getItem('auth_user') || 'null');

function authHeaders(extra = {}) {
    return authToken ? { ...extra, 'Authorization': `Bearer ${authToken}` } : extra;
}

let products      = [];
let cart          = { items: [], total: 0 };
let paymentMethod = null;
let checkoutKey   = null;  // chave de idempotência por tentativa de checkout
let currentSort   = 'default';
let detailQty     = 1;

const ICONS = {
    'Informática':   '💻',
    'Periféricos':   '🖱️',
    'Monitores':     '🖥️',
    'Áudio':         '🎧',
    'Celulares':     '📱',
    'Games':         '🎮',
    'Câmeras':       '📷',
    'Armazenamento': '💾',
};

const COLORS = {
    'Informática':   '#4361ee',
    'Periféricos':   '#e63946',
    'Monitores':     '#0096c7',
    'Áudio':         '#2dc653',
    'Celulares':     '#7c3aed',
    'Games':         '#db2777',
    'Câmeras':       '#0369a1',
    'Armazenamento': '#047857',
};

// Converte nome do produto em slug para localizar o arquivo em /images/
// Ex.: "Canon EOS R50 + 18-45mm" → "canon-eos-r50-18-45mm" → /images/canon-eos-r50-18-45mm.jpg
function productSlug(name) {
    const accents = {'á':'a','à':'a','â':'a','ã':'a','ä':'a','é':'e','è':'e','ê':'e','ë':'e',
                     'í':'i','ì':'i','î':'i','ï':'i','ó':'o','ò':'o','ô':'o','õ':'o','ö':'o',
                     'ú':'u','ù':'u','û':'u','ü':'u','ç':'c','ñ':'n'};
    return name.toLowerCase()
        .replace(/[áàâãäéèêëíìîïóòôõöúùûüçñ]/g, c => accents[c] || c)
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
}

function renderStars(id) {
    const n = [4, 5, 4, 5, 5, 4, 4, 5, 3, 4, 5, 4, 5, 5, 4, 4, 5, 4, 5, 5][id % 20];
    const count = 20 + (id * 7) % 180;
    return `<span style="color:#f59e0b;font-size:11px;letter-spacing:1px">${'★'.repeat(n)}${'☆'.repeat(5-n)}</span><span style="font-size:10px;color:#94a3b8;margin-left:3px">(${count})</span>`;
}


// ── Boot ──────────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', async () => {
    renderAuthArea();
    // Promise.all carrega produtos e carrinho em paralelo, reduzindo o tempo de boot
    await Promise.all([loadProducts(), loadCart()]);
    checkServices();
});

async function checkServices() {
    const el = document.getElementById('service-status');
    try {
        await fetch(`${API.catalog}/health`);
        el.innerHTML = '<i class="bi bi-circle-fill text-success" style="font-size:8px" title="Serviços operacionais"></i>';
    } catch {
        el.innerHTML = '<i class="bi bi-circle-fill text-danger" style="font-size:8px" title="Catálogo offline"></i>';
    }
}


// ── Catalog ───────────────────────────────────────────────────────────────────

async function loadProducts() {
    try {
        const res  = await fetch(`${API.catalog}/products?limit=50`);
        const data = await res.json();
        products = data.products;

        document.getElementById('product-info').textContent =
            `${data.total} produto${data.total !== 1 ? 's' : ''}`;

        renderCatNav();
        filterProducts();
    } catch {
        document.getElementById('product-sections').innerHTML = `
            <div class="col-12">
                <div class="alert alert-danger mb-0">
                    Não foi possível carregar os produtos. Verifique se os serviços estão rodando.
                </div>
            </div>`;
        document.getElementById('product-info').textContent = 'erro ao carregar';
    }
}

function renderCatNav() {
    const catOrder = [...new Set(products.map(p => p.category))];
    const nav = document.getElementById('cat-nav');
    if (!nav) return;
    nav.innerHTML = `<div class="container"><div class="cat-nav-inner">
        <a class="cat-link active" href="#" onclick="goHome();return false">Todos</a>
        ${catOrder.map(cat =>
            `<a class="cat-link" href="#cat-${productSlug(cat)}" onclick="setCatActive(this)">${cat}</a>`
        ).join('')}
    </div></div>`;
}

function setCatActive(el) {
    document.querySelectorAll('.cat-link').forEach(l => l.classList.remove('active'));
    el.classList.add('active');
    if (!document.getElementById('product-detail-section').classList.contains('d-none')) {
        closeProduct();
    }
}

function applySort(val, label) {
    currentSort = val;
    const btn = document.getElementById('sort-label');
    if (btn) btn.textContent = label;
    document.querySelectorAll('.sort-item').forEach(el => {
        el.classList.toggle('active', el.textContent.trim() === label);
    });
    filterProducts();
}

function goHome() {
    if (!document.getElementById('product-detail-section').classList.contains('d-none')) {
        closeProduct();
    }
    document.querySelectorAll('.cat-link').forEach(l => l.classList.remove('active'));
    document.querySelector('.cat-link')?.classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function filterProducts() {
    const query = document.getElementById('search-input').value.toLowerCase().trim();
    const list = query
        ? products.filter(p => p.name.toLowerCase().includes(query) || (p.description || '').toLowerCase().includes(query))
        : [...products];
    renderProducts(list);
}

function renderCard(p) {
    const color = COLORS[p.category] || '#6c757d';
    const icon  = ICONS[p.category]  || '📦';
    const out   = p.stock === 0;
    const low   = p.stock > 0 && p.stock <= 3;
    const pix   = (p.price * 0.95).toFixed(2).replace('.', ',');
    const slug  = productSlug(p.name);
    const flt   = out ? 'grayscale(1) opacity(.4)' : 'none';

    const stockBadge = out
        ? `<span class="stock-out small">Esgotado</span>`
        : low
        ? `<span class="stock-low small fw-semibold">Apenas ${p.stock} unid.</span>`
        : `<span class="stock-ok small">${p.stock} em estoque</span>`;

    return `
    <div class="col-6 col-md-4 col-lg-3">
        <div class="card h-100 product-card border-0 shadow-sm bg-white" onclick="openProduct(${p.id})" style="cursor:pointer">
            <div class="product-img" style="background:#fff">
                <img src="/images/${slug}.jpg" alt="${p.name}"
                     style="width:100%;height:100%;object-fit:contain;padding:10px;filter:${flt}"
                     onerror="this.parentElement.style.background='linear-gradient(135deg,${color}18,${color}08)';this.style.display='none';this.nextElementSibling.removeAttribute('hidden')">
                <span hidden style="filter:${flt};font-size:3.8rem">${icon}</span>
            </div>
            <div class="card-body d-flex flex-column p-3">
                <div class="d-flex align-items-center justify-content-between mb-1">
                    <span class="badge rounded-pill" style="background:${color}20;color:${color};font-size:10px;font-weight:600">${p.category}</span>
                    <div>${renderStars(p.id)}</div>
                </div>
                <p class="fw-semibold mb-1 lh-sm" style="font-size:13px;color:#0f172a">${p.name}</p>
                <p class="text-muted flex-grow-1" style="font-size:11px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${p.description || ''}</p>
                <div class="mt-2">
                    <div class="mb-1">${stockBadge}</div>
                    <div class="d-flex justify-content-between align-items-end mt-2">
                        <div>
                            <div class="price-tag">${fmt(p.price)}</div>
                            ${!out ? `<div class="price-pix">R$ ${pix} no PIX</div>` : ''}
                        </div>
                        <button class="btn btn-dark btn-add" onclick="event.stopPropagation(); addItem(${p.id})" ${out ? 'disabled' : ''}>
                            Adicionar
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>`;
}

function renderProducts(list) {
    const container = document.getElementById('product-sections');

    if (!list.length) {
        container.innerHTML = `
            <div class="text-center py-5">
                <p class="text-muted fw-semibold mb-1">Nenhum produto encontrado.</p>
                <small class="text-muted">Tente outra busca.</small>
            </div>`;
        return;
    }

    // Aplica ordenação
    const sortVal = currentSort;
    const sorted  = [...list];
    if (sortVal === 'price-asc')  sorted.sort((a, b) => a.price - b.price);
    if (sortVal === 'price-desc') sorted.sort((a, b) => b.price - a.price);
    if (sortVal === 'name-asc')   sorted.sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
    if (sortVal === 'name-desc')  sorted.sort((a, b) => b.name.localeCompare(a.name, 'pt-BR'));

    // Ordem das categorias vem do array completo (não da lista filtrada) para ser estável
    const catOrder   = [...new Set(products.map(p => p.category))];
    const activeCats = catOrder.filter(cat => sorted.some(p => p.category === cat));

    container.innerHTML = activeCats.map(cat => {
        const catProducts = sorted.filter(p => p.category === cat);
        const catId = productSlug(cat);
        return `
        <section id="cat-${catId}" class="product-section mb-5">
            <h5 class="section-title">
                ${ICONS[cat] ? `<span class="me-1">${ICONS[cat]}</span>` : ''}${cat}
                <span class="text-muted fw-normal ms-2" style="font-size:13px">${catProducts.length} produto${catProducts.length !== 1 ? 's' : ''}</span>
            </h5>
            <div class="row g-3">${catProducts.map(renderCard).join('')}</div>
        </section>`;
    }).join('');
}


function openProduct(id) {
    const p = products.find(p => p.id === id);
    if (!p) return;

    detailQty = 1;
    const color       = COLORS[p.category] || '#6c757d';
    const icon        = ICONS[p.category]  || '📦';
    const out         = p.stock === 0;
    const low         = p.stock > 0 && p.stock <= 5;
    const pix         = (p.price * 0.95).toFixed(2).replace('.', ',');
    const installment = (p.price / 10).toFixed(2).replace('.', ',');

    const stockInfo = out
        ? `<span class="text-muted" style="font-size:13px">Produto esgotado</span>`
        : low
        ? `<span style="color:#d97706;font-size:13px">Apenas ${p.stock} unidade${p.stock > 1 ? 's' : ''} disponível${p.stock > 1 ? 'is' : ''}</span>`
        : `<span class="text-success" style="font-size:13px">${p.stock} unidades em estoque</span>`;

    const slug      = productSlug(p.name);
    const detFilter = out ? 'grayscale(1) opacity(.4)' : 'none';

    document.getElementById('product-detail-section').innerHTML = `
        <div class="container py-5">
            <nav aria-label="breadcrumb" class="mb-4">
                <ol class="breadcrumb" style="font-size:13px">
                    <li class="breadcrumb-item"><a href="#" onclick="closeProduct();return false;">Produtos</a></li>
                    <li class="breadcrumb-item text-muted">${p.category}</li>
                    <li class="breadcrumb-item active">${p.name}</li>
                </ol>
            </nav>
            <div class="row g-5 align-items-start">
                <div class="col-md-5">
                    <div class="detail-img" style="background:#fff">
                        <img src="/images/${slug}.jpg" alt="${p.name}"
                             style="width:100%;height:100%;object-fit:contain;padding:24px;filter:${detFilter}"
                             onerror="this.parentElement.style.background='linear-gradient(135deg,${color}18,${color}06)';this.style.display='none';this.nextElementSibling.removeAttribute('hidden')">
                        <span hidden style="filter:${detFilter}">${icon}</span>
                    </div>
                </div>
                <div class="col-md-7">
                    <span class="badge rounded-pill mb-2 d-inline-block"
                          style="background:${color}18;color:${color};font-weight:600;font-size:12px;padding:5px 12px">${p.category}</span>
                    <h1 class="fw-bold mb-2" style="font-size:1.6rem;color:#0f172a;line-height:1.3">${p.name}</h1>
                    <div class="mb-3">${renderStars(p.id)}</div>
                    <div class="mb-1">
                        <span style="font-size:1.9rem;font-weight:700;color:#0f172a">${fmt(p.price)}</span>
                    </div>
                    ${!out ? `
                    <p class="mb-1" style="color:#16a34a;font-size:13px">R$ ${pix} no PIX — 5% de desconto</p>
                    <p class="mb-3" style="color:#64748b;font-size:13px">ou 10x de R$ ${installment} sem juros no cartão</p>` : ''}
                    <div class="mb-4">${stockInfo}</div>
                    <div class="d-flex align-items-center gap-3 mb-4"${out ? ' style="opacity:.5;pointer-events:none"' : ''}>
                        <div class="d-flex align-items-center border rounded-pill px-3" style="height:40px;gap:14px">
                            <button class="btn btn-link p-0 text-dark fw-bold" onclick="changeDetailQty(-1)"
                                    style="font-size:20px;line-height:1;text-decoration:none">−</button>
                            <span id="detail-qty" style="min-width:22px;text-align:center;font-weight:600;font-size:15px">1</span>
                            <button class="btn btn-link p-0 text-dark fw-bold" onclick="changeDetailQty(1)"
                                    style="font-size:20px;line-height:1;text-decoration:none">+</button>
                        </div>
                        <button class="btn btn-dark px-4 py-2" onclick="addItemDetail(${p.id})">
                            Adicionar ao carrinho
                        </button>
                    </div>
                    <div class="row g-2 mb-4">
                        <div class="col-6">
                            <div class="d-flex align-items-start gap-2 p-2 rounded" style="background:#f8fafc">
                                <span style="font-size:16px">🚚</span>
                                <div>
                                    <p class="mb-0 fw-semibold" style="font-size:11px">Frete grátis</p>
                                    <p class="mb-0 text-muted" style="font-size:10px">para todo o Brasil</p>
                                </div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="d-flex align-items-start gap-2 p-2 rounded" style="background:#f8fafc">
                                <span style="font-size:16px">🔄</span>
                                <div>
                                    <p class="mb-0 fw-semibold" style="font-size:11px">Devolução grátis</p>
                                    <p class="mb-0 text-muted" style="font-size:10px">até 30 dias</p>
                                </div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="d-flex align-items-start gap-2 p-2 rounded" style="background:#f8fafc">
                                <span style="font-size:16px">💳</span>
                                <div>
                                    <p class="mb-0 fw-semibold" style="font-size:11px">Parcele em 10x</p>
                                    <p class="mb-0 text-muted" style="font-size:10px">sem juros no cartão</p>
                                </div>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="d-flex align-items-start gap-2 p-2 rounded" style="background:#f8fafc">
                                <span style="font-size:16px">🛡️</span>
                                <div>
                                    <p class="mb-0 fw-semibold" style="font-size:11px">Garantia</p>
                                    <p class="mb-0 text-muted" style="font-size:10px">12 meses no fabricante</p>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="border-top pt-4">
                        <h6 class="fw-bold mb-2" style="font-size:14px;color:#0f172a">Sobre o produto</h6>
                        <p style="color:#475569;line-height:1.75;font-size:14px">${p.description || ''}</p>
                    </div>
                    <button class="btn btn-link p-0 text-muted" onclick="closeProduct()"
                            style="font-size:13px;text-decoration:none">← Voltar para produtos</button>
                </div>
            </div>
        </div>`;

    document.getElementById('product-grid-section').classList.add('d-none');
    document.getElementById('product-detail-section').classList.remove('d-none');
    window.scrollTo(0, 0);
}

function closeProduct() {
    document.getElementById('product-detail-section').classList.add('d-none');
    document.getElementById('product-grid-section').classList.remove('d-none');
}

function changeDetailQty(delta) {
    detailQty = Math.max(1, detailQty + delta);
    const el = document.getElementById('detail-qty');
    if (el) el.textContent = detailQty;
}

async function addItemDetail(productId) {
    if (!authToken) { toast('Faça login para adicionar itens ao carrinho', 'warning'); openAuth('login'); return; }
    try {
        const res = await fetch(`${API.cart}/cart/${currentUser.id}/items`, {
            method:  'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body:    JSON.stringify({ product_id: productId, quantity: detailQty }),
        });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        if (!res.ok) { toast(data.detail, 'danger'); return; }
        cart = data;
        syncBadge();
        toast(`${detailQty} item${detailQty > 1 ? 'ns' : ''} adicionado${detailQty > 1 ? 's' : ''} ao carrinho`, 'success');
    } catch {
        toast('Carrinho indisponível', 'danger');
    }
}


// ── Cart ──────────────────────────────────────────────────────────────────────

async function loadCart() {
    if (!authToken) { cart = { items: [], total: 0 }; syncBadge(); return; }
    try {
        const res = await fetch(`${API.cart}/cart/${currentUser.id}`, { headers: authHeaders() });
        if (res.status === 401) { logout(); return; }
        cart = await res.json();
        syncBadge();
    } catch { /* carrinho offline: ignora silenciosamente */ }
}

async function addItem(productId) {
    if (!authToken) { toast('Faça login para adicionar itens ao carrinho', 'warning'); openAuth('login'); return; }
    try {
        const res  = await fetch(`${API.cart}/cart/${currentUser.id}/items`, {
            method:  'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body:    JSON.stringify({ product_id: productId, quantity: 1 }),
        });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        if (!res.ok) { toast(data.detail, 'danger'); return; }
        cart = data;
        syncBadge();
        toast('Item adicionado ao carrinho', 'success');
    } catch {
        toast('Carrinho indisponível', 'danger');
    }
}

async function removeItem(productId) {
    try {
        const res  = await fetch(`${API.cart}/cart/${currentUser.id}/items/${productId}`, {
            method:  'DELETE',
            headers: authHeaders(),
        });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        if (res.ok) { cart = data; syncBadge(); renderCart(); }
    } catch {
        toast('Erro ao remover item', 'danger');
    }
}

function syncBadge() {
    const count = (cart.items || []).reduce((s, i) => s + i.quantity, 0);
    const el    = document.getElementById('badge-count');
    el.textContent = count;
    el.classList.toggle('d-none', count === 0);
}

function openCart() {
    renderCart();
    new bootstrap.Offcanvas(document.getElementById('offcanvasCart')).show();
}

function renderCart() {
    const items = cart.items || [];
    document.getElementById('cart-total').textContent = fmt(cart.total || 0);
    document.getElementById('btn-checkout').disabled  = items.length === 0;

    if (!items.length) {
        document.getElementById('cart-list').innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-cart3" style="font-size:2.5rem;opacity:.3"></i>
                <p class="mt-2 small mb-0">Seu carrinho está vazio</p>
            </div>`;
        return;
    }

    document.getElementById('cart-list').innerHTML = items.map(item => {
        const color = COLORS[getCategory(item.product_id)] || '#6c757d';
        const icon  = ICONS[getCategory(item.product_id)]  || '📦';
        return `
        <div class="d-flex align-items-center gap-2 py-2 border-bottom">
            <div class="rounded d-flex align-items-center justify-content-center flex-shrink-0"
                 style="width:40px;height:40px;background:${color}15;font-size:1.3rem">${icon}</div>
            <div class="flex-grow-1 overflow-hidden">
                <p class="mb-0 fw-semibold text-truncate" style="font-size:13px">${item.name}</p>
                <p class="mb-0 text-muted" style="font-size:11px">${fmt(item.unit_price)} × ${item.quantity}</p>
            </div>
            <div class="text-end flex-shrink-0">
                <p class="mb-0 fw-bold" style="font-size:13px">${fmt(item.unit_price * item.quantity)}</p>
                <button class="btn btn-link btn-sm p-0 text-danger" onclick="removeItem(${item.product_id})">
                    <i class="bi bi-trash" style="font-size:12px"></i>
                </button>
            </div>
        </div>`;
    }).join('');
}

function getCategory(productId) {
    return (products.find(p => p.id === productId) || {}).category;
}


// ── Checkout ──────────────────────────────────────────────────────────────────

function openCheckout() {
    checkoutKey = crypto.randomUUID();
    bootstrap.Offcanvas.getInstance(document.getElementById('offcanvasCart'))?.hide();

    // Reseta seleção de método de pagamento
    paymentMethod = null;
    document.querySelectorAll('.method-btn').forEach(b => {
        b.className = 'btn btn-outline-secondary btn-sm method-btn';
    });
    document.getElementById('btn-confirm-payment').disabled = true;

    // Monta tabela com resumo dos itens do pedido
    const items    = cart.items || [];
    const subtotal = cart.total || 0;
    document.getElementById('order-summary').innerHTML = `
        <table class="table table-sm table-borderless mb-0">
            <tbody>${items.map(i => `
                <tr>
                    <td class="text-muted ps-0" style="font-size:13px">${i.name}
                        <span class="badge bg-light text-dark border">×${i.quantity}</span>
                    </td>
                    <td class="text-end pe-0 fw-semibold" style="font-size:13px">${fmt(i.unit_price * i.quantity)}</td>
                </tr>`).join('')}
            </tbody>
            <tfoot class="border-top">
                <tr id="discount-row" class="text-success" style="display:none">
                    <td class="ps-0" style="font-size:13px">Desconto PIX (5%)</td>
                    <td class="text-end pe-0" style="font-size:13px">− ${fmt(subtotal * 0.05)}</td>
                </tr>
                <tr>
                    <td class="ps-0 fw-bold">Total</td>
                    <td class="text-end pe-0 fw-bold" id="order-total-display">${fmt(subtotal)}</td>
                </tr>
            </tfoot>
        </table>`;

    new bootstrap.Modal(document.getElementById('modalPayment')).show();
}

function selectMethod(method) {
    paymentMethod = method;
    document.querySelectorAll('.method-btn').forEach(b => {
        b.className = 'btn btn-outline-secondary btn-sm method-btn';
    });
    document.getElementById(`m-${method}`).className = 'btn btn-dark btn-sm method-btn';
    document.getElementById('btn-confirm-payment').disabled = false;

    const subtotal     = cart.total || 0;
    const discountRow  = document.getElementById('discount-row');
    const totalDisplay = document.getElementById('order-total-display');
    if (discountRow && totalDisplay) {
        const isPix = method === 'pix';
        discountRow.style.display = isPix ? '' : 'none';
        totalDisplay.textContent  = fmt(isPix ? subtotal * 0.95 : subtotal);
    }
}

/**
 * Checkout assíncrono: POST retorna imediatamente com order_id; depois faz polling
 * em GET /orders/{order_id} até o payment-service processar e publicar o resultado.
 */
async function confirmPayment() {
    const btn    = document.getElementById('btn-confirm-payment');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Aguardando...';

    try {
        const res = await fetch(`${API.cart}/cart/${currentUser.id}/checkout`, {
            method:  'POST',
            headers: authHeaders({ 'Content-Type': 'application/json', 'Idempotency-Key': checkoutKey }),
            body:    JSON.stringify({ payment_method: paymentMethod }),
        });
        if (res.status === 401) { logout(); return; }
        if (!res.ok) {
            const err = await res.json();
            toast(err.detail || 'Erro ao iniciar checkout', 'danger');
            return;
        }

        const { order_id } = await res.json();

        bootstrap.Modal.getInstance(document.getElementById('modalPayment'))?.hide();
        toast('Processando pagamento...', 'info');

        const order = await pollOrderStatus(order_id);

        if (order.status === 'approved') {
            cart = { items: [], total: 0 };
            syncBadge();
            // Aguarda 2,5s para o consumer do Redis decrementar o estoque antes de recarregar
            // — consistência eventual entre payment-service e catalog-service
            setTimeout(loadProducts, 2500);
        }

        showResult(order);
    } catch {
        toast('Erro ao processar pagamento', 'danger');
    } finally {
        btn.disabled  = false;
        btn.innerHTML = 'Confirmar pagamento';
    }
}

// Tenta até 15 vezes (30s total). Se esgotar, devolve status 'pending' para o
// usuário verificar o histórico — evita deixar a tela travada indefinidamente.
async function pollOrderStatus(orderId, maxAttempts = 15, intervalMs = 2000) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, intervalMs));
        try {
            const res = await fetch(`${API.cart}/orders/${orderId}`);
            if (!res.ok) continue;
            const order = await res.json();
            if (order.status !== 'processing') return order;
        } catch { /* rede instável — tenta de novo */ }
    }
    return {
        order_id:   orderId,
        status:     'pending',
        message:    'Tempo de resposta excedido. Verifique seu histórico de pedidos.',
        payment_id: null,
        total:      0,
    };
}

function showResult(order) {
    const cfg = {
        approved: { icon: '✅', title: 'Pedido confirmado!',  color: 'text-success' },
        declined: { icon: '❌', title: 'Pagamento recusado',   color: 'text-danger'  },
        pending:  { icon: '⏳', title: 'Aguardando pagamento', color: 'text-warning' },
    }[order.status] || { icon: '❓', title: order.status, color: '' };

    document.getElementById('payment-result').innerHTML = `
        <div style="font-size:2.8rem;line-height:1">${cfg.icon}</div>
        <h6 class="mt-2 mb-1 ${cfg.color}">${cfg.title}</h6>
        <p class="text-muted small mb-1">${order.message}</p>
        <p class="fw-bold mb-1">${fmt(order.total)}</p>
        <small class="text-muted">Pedido #${order.payment_id || order.order_id}</small>`;

    new bootstrap.Modal(document.getElementById('modalResult')).show();
}


// ── Orders ────────────────────────────────────────────────────────────────────

function openOrders() {
    new bootstrap.Offcanvas(document.getElementById('offcanvasOrders')).show();
    loadOrders();
}

async function loadOrders() {
    const container = document.getElementById('orders-list');
    container.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm"></div></div>';
    try {
        const res = await fetch(`${API.payment}/payments/user/${currentUser.id}`, { headers: authHeaders() });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        renderOrders(data.payments || []);
    } catch {
        container.innerHTML = '<p class="text-danger small text-center py-4">Erro ao carregar pedidos.</p>';
    }
}

function renderOrders(orders) {
    const container = document.getElementById('orders-list');

    if (!orders.length) {
        container.innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-receipt" style="font-size:2.5rem;opacity:.3"></i>
                <p class="mt-2 small mb-0">Você ainda não tem pedidos.</p>
            </div>`;
        return;
    }

    const STATUS = {
        approved: { label: 'Aprovado', badge: 'success' },
        declined: { label: 'Recusado', badge: 'danger'  },
        pending:  { label: 'Pendente', badge: 'warning' },
    };

    container.innerHTML = orders.map(order => {
        const status = STATUS[order.status] || { label: order.status, badge: 'secondary' };
        const date   = new Date(order.created_at).toLocaleString('pt-BR');

        return `
        <div class="border rounded p-2 mb-2">
            <div class="d-flex justify-content-between align-items-start mb-1">
                <span class="fw-semibold" style="font-size:13px">Pedido #${order.payment_id}</span>
                <span class="badge bg-${status.badge}">${status.label}</span>
            </div>
            <p class="text-muted mb-2" style="font-size:11px">${date} &middot; ${order.payment_method.toUpperCase()}</p>
            <ul class="list-unstyled mb-2" style="font-size:12px">
                ${order.items.map(i => `<li>${i.quantity}&times; ${i.name}</li>`).join('')}
            </ul>
            <p class="fw-bold mb-0 text-end" style="font-size:13px">${fmt(order.total)}</p>
        </div>`;
    }).join('');
}


// ── Admin ─────────────────────────────────────────────────────────────────────

function openAdmin() {
    if (!currentUser || currentUser.role !== 'admin') {
        toast('Acesso restrito a administradores', 'danger');
        return;
    }
    showAdminTab('add');
    new bootstrap.Offcanvas(document.getElementById('offcanvasAdmin')).show();
}

function showAdminTab(tab) {
    document.getElementById('admin-add').classList.toggle('d-none',   tab !== 'add');
    document.getElementById('admin-stock').classList.toggle('d-none', tab !== 'stock');
    document.getElementById('tab-add').classList.toggle('active',   tab === 'add');
    document.getElementById('tab-stock').classList.toggle('active', tab === 'stock');
    if (tab === 'stock') renderAdminStock();
}

async function adminAddProduct(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-add-product');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Adicionando...';

    try {
        const res = await fetch(`${API.catalog}/products`, {
            method:  'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                name:        document.getElementById('a-name').value.trim(),
                price:       parseFloat(document.getElementById('a-price').value),
                stock:       parseInt(document.getElementById('a-stock').value),
                category:    document.getElementById('a-category').value,
                description: document.getElementById('a-description').value.trim(),
            }),
        });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Erro ao adicionar', 'danger'); return; }
        toast(`"${data.name}" adicionado com sucesso!`, 'success');
        e.target.reset();
        await loadProducts();
    } catch {
        toast('Erro ao conectar ao catálogo', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Adicionar Produto';
    }
}

async function renderAdminStock() {
    const container = document.getElementById('admin-stock-list');
    container.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm"></div></div>';
    try {
        const res  = await fetch(`${API.catalog}/products?limit=100`);
        const data = await res.json();

        if (!data.products.length) {
            container.innerHTML = '<p class="text-muted small text-center py-4">Nenhum produto cadastrado.</p>';
            return;
        }

        container.innerHTML = data.products.map(p => `
            <div class="d-flex align-items-center gap-2 py-2 border-bottom">
                <div class="flex-grow-1 overflow-hidden">
                    <p class="mb-0 fw-semibold text-truncate" style="font-size:13px">${p.name}</p>
                    <span class="badge bg-secondary" style="font-size:10px">${p.category}</span>
                </div>
                <div class="d-flex align-items-center gap-1">
                    <input type="number" min="0" value="${p.stock}"
                           class="form-control form-control-sm text-center"
                           style="width:65px" id="stock-${p.id}">
                    <button class="btn btn-sm btn-dark" onclick="adminUpdateStock(${p.id})" title="Salvar">
                        <i class="bi bi-check-lg"></i>
                    </button>
                </div>
            </div>`).join('');
    } catch {
        container.innerHTML = '<p class="text-danger small text-center py-4">Erro ao carregar produtos.</p>';
    }
}

async function adminUpdateStock(productId) {
    const input    = document.getElementById(`stock-${productId}`);
    const newStock = parseInt(input.value);
    if (isNaN(newStock) || newStock < 0) { toast('Estoque inválido', 'danger'); return; }

    try {
        const res = await fetch(`${API.catalog}/products/${productId}/stock`, {
            method:  'PUT',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ stock: newStock }),
        });
        if (res.status === 401) { logout(); return; }
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Erro ao atualizar', 'danger'); return; }
        toast(`${data.name}: estoque atualizado para ${data.stock} un.`, 'success');
        await loadProducts();
    } catch {
        toast('Erro ao conectar ao catálogo', 'danger');
    }
}


// ── Auth ──────────────────────────────────────────────────────────────────────

function renderAuthArea() {
    const el = document.getElementById('auth-area');
    if (currentUser) {
        el.innerHTML = `
            <div class="dropdown">
                <button class="btn btn-outline-light btn-sm dropdown-toggle" data-bs-toggle="dropdown">
                    <i class="bi bi-person-fill me-1"></i>${currentUser.email.split('@')[0]}
                </button>
                <ul class="dropdown-menu dropdown-menu-end">
                    <li><span class="dropdown-item-text small text-muted">${currentUser.role === 'admin' ? 'Administrador' : 'Cliente'}</span></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><button class="dropdown-item" onclick="openOrders()">Meus Pedidos</button></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><button class="dropdown-item" onclick="logout()">Sair</button></li>
                </ul>
            </div>`;
    } else {
        el.innerHTML = `<button class="btn btn-outline-light btn-sm" onclick="openAuth('login')">Entrar</button>`;
    }
}

function openAuth(tab) {
    showAuthTab(tab);
    new bootstrap.Modal(document.getElementById('modalAuth')).show();
}

function showAuthTab(tab) {
    document.getElementById('auth-form-login').classList.toggle('d-none', tab !== 'login');
    document.getElementById('auth-form-register').classList.toggle('d-none', tab !== 'register');
    document.getElementById('auth-tab-login').classList.toggle('active', tab === 'login');
    document.getElementById('auth-tab-register').classList.toggle('active', tab === 'register');
}

function setSession(token, user) {
    authToken   = token;
    currentUser = user;
    localStorage.setItem('auth_token', token);
    localStorage.setItem('auth_user', JSON.stringify(user));
    renderAuthArea();
}

function logout() {
    authToken   = null;
    currentUser = null;
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    cart = { items: [], total: 0 };
    syncBadge();
    renderAuthArea();
}

async function doLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-login');
    btn.disabled = true;
    try {
        const res = await fetch(`${API.auth}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email:    document.getElementById('login-email').value.trim(),
                password: document.getElementById('login-password').value,
            }),
        });
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Falha no login', 'danger'); return; }
        setSession(data.access_token, data.user);
        bootstrap.Modal.getInstance(document.getElementById('modalAuth'))?.hide();
        e.target.reset();
        toast(`Bem-vindo, ${data.user.email}!`, 'success');
        await loadCart();
    } catch {
        toast('Erro ao conectar ao serviço de autenticação', 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function doRegister(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-register');
    btn.disabled = true;
    try {
        const res = await fetch(`${API.auth}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email:    document.getElementById('register-email').value.trim(),
                password: document.getElementById('register-password').value,
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            const msg = Array.isArray(data.detail) ? data.detail[0].msg : data.detail;
            toast(msg || 'Falha ao criar conta', 'danger');
            return;
        }
        setSession(data.access_token, data.user);
        bootstrap.Modal.getInstance(document.getElementById('modalAuth'))?.hide();
        e.target.reset();
        toast('Conta criada com sucesso!', 'success');
        await loadCart();
    } catch {
        toast('Erro ao conectar ao serviço de autenticação', 'danger');
    } finally {
        btn.disabled = false;
    }
}


// ── Utils ─────────────────────────────────────────────────────────────────────

function fmt(val) {
    return Number(val).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function toast(msg, type = 'secondary') {
    const el = document.createElement('div');
    el.className = `toast align-items-center text-bg-${type} border-0 show mb-2`;
    el.innerHTML = `<div class="d-flex">
        <div class="toast-body small">${msg}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>`;
    document.getElementById('toasts-area').appendChild(el);
    setTimeout(() => el.remove(), 3200);
}
