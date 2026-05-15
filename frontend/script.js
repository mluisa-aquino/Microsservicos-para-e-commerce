const SID = 'sess-' + Math.random().toString(36).slice(2,8);
const C = 'http://localhost:8001';
const K = 'http://localhost:8002';
const P = 'http://localhost:8003';

const ICON = { 'Informática':'💻', 'Periféricos':'🖱️', 'Monitores':'🖥️', 'Áudio':'🎧' };
const GRAD = {
  'Informática':'linear-gradient(135deg,#667eea,#764ba2)',
  'Periféricos':'linear-gradient(135deg,#f093fb,#f5576c)',
  'Monitores':  'linear-gradient(135deg,#4facfe,#00f2fe)',
  'Áudio':      'linear-gradient(135deg,#43e97b,#38f9d7)',
};

let products = [], activeCat = 'Todos', method = null, cart = {itens:[],total:0};

// ── PRODUCTS ───────────────────────────────────────
async function loadProducts() {
  try {
    const r = await fetch(`${C}/produtos`);
    const d = await r.json();
    products = d.produtos;
    document.getElementById('sub').textContent = `${products.length} produtos disponíveis`;

    const cats = ['Todos', ...new Set(products.map(p => p.categoria))];
    document.getElementById('cats').innerHTML = cats.map(c =>
      `<button class="cat ${c==='Todos'?'on':''}" onclick="setCat('${c}')">${c}</button>`
    ).join('');

    render(products);
  } catch {
    document.getElementById('sub').textContent = 'Catálogo offline';
    document.getElementById('grid').innerHTML =
      `<p style="grid-column:1/-1;color:var(--error);padding:32px 0">⚠️ catalogo-service offline (porta 8001)</p>`;
  }
}

function setCat(c) {
  activeCat = c;
  document.querySelectorAll('.cat').forEach(b => b.classList.toggle('on', b.textContent === c));
  filter();
}

function filter() {
  const q = document.getElementById('q').value.toLowerCase();
  let list = activeCat === 'Todos' ? products : products.filter(p => p.categoria === activeCat);
  if (q) list = list.filter(p => p.nome.toLowerCase().includes(q));
  render(list);
}

function render(list) {
  if (!list.length) {
    document.getElementById('grid').innerHTML =
      `<p style="grid-column:1/-1;color:var(--gray-400);text-align:center;padding:48px 0">Nenhum produto encontrado.</p>`;
    return;
  }
  document.getElementById('grid').innerHTML = list.map(p => `
    <div class="card">
      <div class="card-img" style="background:${GRAD[p.categoria]||'#eee'}">${ICON[p.categoria]||'📦'}</div>
      <div class="card-body">
        <div class="card-cat">${p.categoria}</div>
        <div class="card-name">${p.nome}</div>
        <div class="card-stock ${p.estoque<=5?'low':''}">
          ${p.estoque>0?`✓ ${p.estoque} em estoque`:'✗ Esgotado'}
        </div>
        <div class="card-foot">
          <div class="card-price">${fmt(p.preco)}</div>
          <button class="add-btn" onclick="addItem(${p.id})" ${p.estoque===0?'disabled':''}>+ Adicionar</button>
        </div>
      </div>
    </div>
  `).join('');
}

// ── CART ───────────────────────────────────────────
async function addItem(id) {
  try {
    const r = await fetch(`${K}/carrinho/${SID}/adicionar`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({produto_id:id, quantidade:1}),
    });
    const d = await r.json();
    if (!r.ok) { toast(d.detail,'err'); return; }
    cart = d; syncBadge(); toast('Adicionado ao carrinho ✓','ok');
  } catch { toast('Carrinho offline','err'); }
}

async function loadCart() {
  try { const r = await fetch(`${K}/carrinho/${SID}`); cart = await r.json(); syncBadge(); } catch {}
}

function syncBadge() {
  const n = (cart.itens||[]).reduce((s,i)=>s+i.quantidade,0);
  document.getElementById('badge').textContent = n;
  document.getElementById('chk-btn').disabled = n===0;
}

function openCart() { renderCart(); document.getElementById('drawer').classList.add('on'); document.getElementById('ov').classList.add('on'); }
function closeCart() { document.getElementById('drawer').classList.remove('on'); document.getElementById('ov').classList.remove('on'); }

function renderCart() {
  const itens = cart.itens||[];
  const el = document.getElementById('cart-body');
  if (!itens.length) {
    el.innerHTML = `<div class="empty"><span>🛒</span><p>Carrinho vazio</p></div>`;
    document.getElementById('total').textContent = 'R$ 0,00'; return;
  }
  el.innerHTML = itens.map(i => `
    <div class="c-item">
      <div class="c-icon">${ICON[getCat(i.produto_id)]||'📦'}</div>
      <div class="c-info">
        <div class="c-name">${i.nome}</div>
        <div class="c-price">${fmt(i.preco_unitario)} × ${i.quantidade} = <strong>${fmt(i.preco_unitario*i.quantidade)}</strong></div>
      </div>
      <button class="del-btn" onclick="removeItem(${i.produto_id})">🗑️</button>
    </div>
  `).join('');
  document.getElementById('total').textContent = fmt(cart.total||0);
}

async function removeItem(id) {
  try {
    const r = await fetch(`${K}/carrinho/${SID}/remover/${id}`,{method:'DELETE'});
    cart = await r.json(); syncBadge(); renderCart();
  } catch { toast('Erro ao remover','err'); }
}

function getCat(id) { return (products.find(p=>p.id===id)||{}).categoria; }

// ── CHECKOUT ───────────────────────────────────────
function openCheckout() {
  closeCart(); method = null;
  document.querySelectorAll('.method').forEach(b=>b.classList.remove('on'));
  document.getElementById('pay-btn').disabled = true;

  const itens = cart.itens||[];
  document.getElementById('summary').innerHTML =
    itens.map(i=>`<div class="s-row"><span>${i.nome} ×${i.quantidade}</span><span>${fmt(i.preco_unitario*i.quantidade)}</span></div>`).join('')
    + `<div class="s-row"><span>Total</span><span>${fmt(cart.total||0)}</span></div>`;

  document.getElementById('checkout-wrap').classList.add('on');
}
function closeCheckout() { document.getElementById('checkout-wrap').classList.remove('on'); }

function pick(m) {
  method = m;
  document.querySelectorAll('.method').forEach(b=>b.classList.remove('on'));
  document.getElementById(`m-${m}`).classList.add('on');
  document.getElementById('pay-btn').disabled = false;
}

async function pay() {
  const btn = document.getElementById('pay-btn');
  btn.innerHTML = '<div class="spin"></div> Processando...';
  btn.disabled = true;
  try {
    const r = await fetch(`${P}/pagamento/checkout`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id:SID, itens:cart.itens, metodo_pagamento:method}),
    });
    const pedido = await r.json();
    closeCheckout();
    showResult(pedido);
    if (pedido.status==='aprovado') {
      await fetch(`${K}/carrinho/${SID}`,{method:'DELETE'});
      cart={itens:[],total:0}; syncBadge();
    }
  } catch {
    toast('Pagamento offline','err');
    btn.innerHTML='Confirmar Pagamento'; btn.disabled=false;
  }
}

function showResult(p) {
  const cfg = {
    aprovado:['✅','Pedido Aprovado!','var(--success)'],
    recusado:['❌','Pagamento Recusado','var(--error)'],
    pendente:['⏳','Pagamento Pendente','var(--accent)'],
  }[p.status];
  document.getElementById('result-body').innerHTML = `
    <div class="result-ico">${cfg[0]}</div>
    <div class="result-ttl" style="color:${cfg[2]}">${cfg[1]}</div>
    <div class="result-sub">${p.mensagem}</div>
    <div class="result-sub">Total: <strong>${fmt(p.total)}</strong></div>
    <div class="result-id">Pedido #${p.pedido_id}</div>
    <button class="result-close" onclick="closeResult()">Continuar comprando</button>
  `;
  document.getElementById('result-wrap').classList.add('on');
}
function closeResult() { document.getElementById('result-wrap').classList.remove('on'); }

// ── UTILS ──────────────────────────────────────────
function fmt(v) { return (+v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'}); }
function toast(msg, type='') {
  const el = document.createElement('div');
  el.className = `toast ${type}`; el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(()=>{ el.style.animation='tOut .3s ease forwards'; setTimeout(()=>el.remove(),300); },3000);
}

loadProducts();
loadCart();
