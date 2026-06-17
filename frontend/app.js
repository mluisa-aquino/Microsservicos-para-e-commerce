/**
 * app.js — Frontend da plataforma ShopMicro
 *
 * SPA (Single Page Application) em Vanilla JS que se comunica diretamente
 * com os microsserviços via fetch API.
 *
 * Módulos:
 *  - Boot: inicialização e verificação de serviços
 *  - Catalog: listagem, busca e filtro de produtos
 *  - Cart: carrinho de compras (adicionar, remover, exibir)
 *  - Checkout: resumo do pedido e seleção de forma de pagamento
 *  - Admin: painel administrativo (cadastrar produto, editar estoque)
 *  - Utils: formatação de moeda e toasts de notificação
 */

/** URLs base dos microsserviços */
const API = {
    catalog: 'http://localhost:8001',
    cart:    'http://localhost:8002',
    payment: 'http://localhost:8003',
    auth:    'http://localhost:8004',
};

// Sessão do usuário: token JWT e dados de perfil, persistidos no localStorage
// para sobreviver a um refresh da página. O user_id usado pelo cart-service
// e payment-service é o 'sub' do próprio token (currentUser.id).
let authToken   = localStorage.getItem('auth_token') || null;
let currentUser = JSON.parse(localStorage.getItem('auth_user') || 'null');

/** Monta os headers de autenticação a partir do token atual, se houver. */
function authHeaders(extra = {}) {
    return authToken ? { ...extra, 'Authorization': `Bearer ${authToken}` } : extra;
}

// Estado global da aplicação
let products         = [];   // lista completa de produtos carregados do catalog-service
let cart             = { items: [], total: 0 };  // estado atual do carrinho
let selectedCategory = 'Todos';  // categoria selecionada nos filtros
let paymentMethod    = null;     // método de pagamento selecionado no modal
let checkoutKey      = null;     // chave de idempotência gerada por checkout (evita cobrança dupla)

/** Mapeamento de categoria para emoji (imagem dos cards) */
const ICONS  = { 'Informática': '💻', 'Periféricos': '🖱️', 'Monitores': '🖥️', 'Áudio': '🎧' };

/** Mapeamento de categoria para cor do tema do card */
const COLORS = { 'Informática': '#4361ee', 'Periféricos': '#e63946', 'Monitores': '#0096c7', 'Áudio': '#2dc653' };


// ── Boot ──────────────────────────────────────────────────────────────────────

/** Inicializa a aplicação assim que o DOM estiver pronto */
window.addEventListener('DOMContentLoaded', async () => {
    renderAuthArea();
    // Carrega produtos e carrinho em paralelo para reduzir tempo de inicialização
    await Promise.all([loadProducts(), loadCart()]);
    checkServices();
});

/** Verifica se o catalog-service está acessível e atualiza o indicador na navbar */
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

/** Busca produtos do catalog-service e atualiza a interface */
async function loadProducts() {
    try {
        const res  = await fetch(`${API.catalog}/products?limit=50`);
        const data = await res.json();
        products   = data.products;

        document.getElementById('product-info').textContent =
            `${data.total} produto${data.total !== 1 ? 's' : ''} encontrado${data.total !== 1 ? 's' : ''}`;

        // Gera os botões de filtro dinamicamente com base nas categorias existentes
        const categories = ['Todos', ...new Set(products.map(p => p.category))];
        document.getElementById('category-filters').innerHTML = categories.map(c => `
            <button class="btn btn-sm rounded-pill ${c === selectedCategory ? 'btn-dark' : 'btn-outline-secondary'}"
                    data-cat="${c}" onclick="setCategory('${c}')">${c}</button>
        `).join('');

        // Aplica os filtros atuais (categoria e busca) após recarregar produtos
        filterProducts();
    } catch {
        document.getElementById('product-grid').innerHTML = `
            <div class="col-12">
                <div class="alert alert-danger mb-0">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    Não foi possível carregar os produtos. Verifique se os serviços estão rodando.
                </div>
            </div>`;
        document.getElementById('product-info').textContent = 'erro ao carregar';
    }
}

/** Atualiza a categoria selecionada e aplica os filtros */
function setCategory(cat) {
    selectedCategory = cat;
    document.querySelectorAll('#category-filters button').forEach(btn => {
        const active = btn.dataset.cat === cat;
        btn.className = `btn btn-sm rounded-pill ${active ? 'btn-dark' : 'btn-outline-secondary'}`;
    });
    filterProducts();
}

/**
 * Filtra os produtos pelo texto de busca e categoria selecionada.
 * A busca é feita localmente (sem nova requisição ao servidor)
 * sobre os dados já carregados em memória.
 */
function filterProducts() {
    const query = document.getElementById('search-input').value.toLowerCase().trim();
    let list    = selectedCategory === 'Todos' ? products : products.filter(p => p.category === selectedCategory);
    if (query)  list = list.filter(p => p.name.toLowerCase().includes(query) || (p.description || '').toLowerCase().includes(query));
    renderProducts(list);
}

/** Renderiza os cards de produto no grid principal */
function renderProducts(list) {
    if (!list.length) {
        document.getElementById('product-grid').innerHTML = `
            <div class="col-12 text-center text-muted py-5">
                <i class="bi bi-search" style="font-size:2rem;opacity:.3"></i>
                <p class="mt-2 mb-0">Nenhum produto encontrado.</p>
            </div>`;
        return;
    }

    document.getElementById('product-grid').innerHTML = list.map(p => {
        const color = COLORS[p.category] || '#6c757d';
        const icon  = ICONS[p.category]  || '📦';
        const out   = p.stock === 0;         // sem estoque
        const low   = p.stock > 0 && p.stock <= 3;  // estoque crítico

        return `
        <div class="col-6 col-md-4 col-lg-3">
            <div class="card h-100 product-card border-0 shadow-sm">
                <div class="product-img rounded-top" style="background:${color}15">
                    <span style="filter:${out ? 'grayscale(1) opacity(.35)' : 'none'}">${icon}</span>
                </div>
                <div class="card-body d-flex flex-column p-3">
                    <span class="badge mb-2" style="background:${color};font-size:10px;width:fit-content">${p.category}</span>
                    <p class="fw-semibold mb-1 lh-sm" style="font-size:13px">${p.name}</p>
                    <p class="text-muted mb-3 flex-grow-1" style="font-size:11px">${p.description || ''}</p>
                    <div class="mt-auto">
                        <p class="mb-2 small ${out ? 'text-muted' : low ? 'text-warning fw-semibold' : 'text-success'}">
                            <i class="bi ${out ? 'bi-x-circle' : 'bi-check-circle'} me-1"></i>
                            ${out ? 'Esgotado' : low ? `Só ${p.stock} restante${p.stock > 1 ? 's' : ''}` : `${p.stock} em estoque`}
                        </p>
                        <div class="d-flex justify-content-between align-items-center">
                            <strong style="font-size:15px">${fmt(p.price)}</strong>
                            <button class="btn btn-sm btn-dark" onclick="addItem(${p.id})" ${out ? 'disabled' : ''}>
                                <i class="bi bi-cart-plus me-1"></i>Adicionar
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
}


// ── Cart ──────────────────────────────────────────────────────────────────────

/** Carrega o estado atual do carrinho do cart-service (requer login) */
async function loadCart() {
    if (!authToken) { cart = { items: [], total: 0 }; syncBadge(); return; }
    try {
        const res = await fetch(`${API.cart}/cart/${currentUser.id}`, { headers: authHeaders() });
        if (res.status === 401) { logout(); return; }
        cart = await res.json();
        syncBadge();
    } catch { /* carrinho offline: ignora silenciosamente */ }
}

/** Envia requisição para adicionar um produto ao carrinho (requer login) */
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

/** Remove um produto do carrinho */
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

/** Atualiza o badge de quantidade no ícone do carrinho na navbar */
function syncBadge() {
    const count = (cart.items || []).reduce((s, i) => s + i.quantity, 0);
    const el    = document.getElementById('badge-count');
    el.textContent = count;
    el.classList.toggle('d-none', count === 0);
}

/** Abre o painel lateral do carrinho */
function openCart() {
    renderCart();
    new bootstrap.Offcanvas(document.getElementById('offcanvasCart')).show();
}

/** Renderiza os itens do carrinho no offcanvas lateral */
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

/** Busca a categoria de um produto pelo ID na lista em memória */
function getCategory(productId) {
    return (products.find(p => p.id === productId) || {}).category;
}


// ── Checkout ──────────────────────────────────────────────────────────────────

/** Abre o modal de checkout com o resumo do pedido */
function openCheckout() {
    checkoutKey = crypto.randomUUID();  // nova chave por tentativa de checkout
    bootstrap.Offcanvas.getInstance(document.getElementById('offcanvasCart'))?.hide();

    // Reseta seleção de método de pagamento
    paymentMethod = null;
    document.querySelectorAll('.method-btn').forEach(b => {
        b.className = 'btn btn-outline-secondary btn-sm method-btn';
    });
    document.getElementById('btn-confirm-payment').disabled = true;

    // Monta tabela com resumo dos itens do pedido
    const items = cart.items || [];
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
                <tr>
                    <td class="ps-0 fw-bold">Total</td>
                    <td class="text-end pe-0 fw-bold">${fmt(cart.total || 0)}</td>
                </tr>
            </tfoot>
        </table>`;

    new bootstrap.Modal(document.getElementById('modalPayment')).show();
}

/** Marca o método de pagamento selecionado e habilita o botão de confirmar */
function selectMethod(method) {
    paymentMethod = method;
    document.querySelectorAll('.method-btn').forEach(b => {
        b.className = 'btn btn-outline-secondary btn-sm method-btn';
    });
    document.getElementById(`m-${method}`).className = 'btn btn-dark btn-sm method-btn';
    document.getElementById('btn-confirm-payment').disabled = false;
}

/**
 * Envia o checkout de forma assíncrona via mensageria.
 *
 * Fluxo:
 * 1. POST /checkout → cart-service publica no stream e retorna order_id imediatamente
 * 2. Fecha o modal e exibe spinner de "Aguardando..."
 * 3. Faz polling em GET /orders/{order_id} até o status sair de "processing"
 * 4. Exibe resultado no modal de resultado
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

        // Polling até o payment-service processar e publicar o resultado
        const order = await pollOrderStatus(order_id);

        if (order.status === 'approved') {
            cart = { items: [], total: 0 };
            syncBadge();
            // Aguarda 2.5s para o consumer do Redis processar o evento de estoque
            // antes de recarregar os produtos (consistência eventual)
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

/**
 * Faz polling em GET /orders/{orderId} até o status sair de "processing".
 * Tenta até maxAttempts vezes com intervalo de intervalMs ms entre tentativas.
 * Se o tempo esgotar, retorna um pedido com status 'pending' para o usuário
 * verificar o histórico de pedidos.
 */
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

/** Exibe o modal com o resultado do pagamento */
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

/** Abre o painel de pedidos e carrega o histórico do usuário logado */
function openOrders() {
    new bootstrap.Offcanvas(document.getElementById('offcanvasOrders')).show();
    loadOrders();
}

/** Busca o histórico de pagamentos do usuário no payment-service */
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

/** Renderiza a lista de pedidos do usuário, do mais recente para o mais antigo */
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

/** Abre o painel administrativo na aba de cadastro de produto (requer role admin) */
function openAdmin() {
    if (!currentUser || currentUser.role !== 'admin') {
        toast('Acesso restrito a administradores', 'danger');
        return;
    }
    showAdminTab('add');
    new bootstrap.Offcanvas(document.getElementById('offcanvasAdmin')).show();
}

/** Alterna entre as abas do painel admin */
function showAdminTab(tab) {
    document.getElementById('admin-add').classList.toggle('d-none',   tab !== 'add');
    document.getElementById('admin-stock').classList.toggle('d-none', tab !== 'stock');
    document.getElementById('tab-add').classList.toggle('active',   tab === 'add');
    document.getElementById('tab-stock').classList.toggle('active', tab === 'stock');
    if (tab === 'stock') renderAdminStock();
}

/** Envia formulário de cadastro de novo produto para o catalog-service */
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
        await loadProducts();  // atualiza o grid com o novo produto
    } catch {
        toast('Erro ao conectar ao catálogo', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Adicionar Produto';
    }
}

/** Carrega e renderiza a lista de produtos com campos de edição de estoque */
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

/** Envia a atualização de estoque de um produto para o catalog-service */
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
        await loadProducts();  // reflete a mudança no grid
    } catch {
        toast('Erro ao conectar ao catálogo', 'danger');
    }
}


// ── Auth ──────────────────────────────────────────────────────────────────────

/** Atualiza o botão/área de sessão na navbar (login ou perfil + sair) */
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
                    <li><button class="dropdown-item" onclick="openOrders()"><i class="bi bi-receipt me-2"></i>Meus Pedidos</button></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><button class="dropdown-item" onclick="logout()">Sair</button></li>
                </ul>
            </div>`;
    } else {
        el.innerHTML = `<button class="btn btn-outline-light btn-sm" onclick="openAuth('login')">Entrar</button>`;
    }
}

/** Abre o modal de autenticação na aba indicada ('login' ou 'register') */
function openAuth(tab) {
    showAuthTab(tab);
    new bootstrap.Modal(document.getElementById('modalAuth')).show();
}

/** Alterna entre as abas de login e registro do modal de autenticação */
function showAuthTab(tab) {
    document.getElementById('auth-form-login').classList.toggle('d-none', tab !== 'login');
    document.getElementById('auth-form-register').classList.toggle('d-none', tab !== 'register');
    document.getElementById('auth-tab-login').classList.toggle('active', tab === 'login');
    document.getElementById('auth-tab-register').classList.toggle('active', tab === 'register');
}

/** Persiste o token e os dados do usuário autenticado */
function setSession(token, user) {
    authToken   = token;
    currentUser = user;
    localStorage.setItem('auth_token', token);
    localStorage.setItem('auth_user', JSON.stringify(user));
    renderAuthArea();
}

/** Encerra a sessão atual e limpa o carrinho exibido */
function logout() {
    authToken   = null;
    currentUser = null;
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    cart = { items: [], total: 0 };
    syncBadge();
    renderAuthArea();
}

/** Autentica no auth-service e inicia a sessão */
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

/** Cria uma nova conta no auth-service e já inicia a sessão */
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

/** Formata um valor numérico como moeda brasileira (R$) */
function fmt(val) {
    return Number(val).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

/**
 * Exibe uma notificação temporária (toast) no canto inferior direito.
 * Remove-se automaticamente após 3,2 segundos.
 */
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
