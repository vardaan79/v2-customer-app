/* V2 Retail — Customer App front end.
   Vanilla JS, no dependencies: the app has to run on a store tablet and a
   shopper's phone with nothing cached from a CDN. */
'use strict';

const $ = (id) => document.getElementById(id);
const el = (sel, root = document) => root.querySelector(sel);
const all = (sel, root = document) => [...root.querySelectorAll(sel)];

const TOKEN_KEY = 'v2_customer_token';
let STATE = { bills: [], summary: null, membership: null, profile: null, shown: 8 };

/* ------------------------------------------------------------------ utils */
const money = (n, dec = 0) => '₹' + Number(n || 0).toLocaleString('en-IN',
  { minimumFractionDigits: dec, maximumFractionDigits: dec });

function compactMoney(n) {
  const v = Number(n || 0);
  if (Math.abs(v) >= 1e7) return '₹' + (v / 1e7).toFixed(2) + ' Cr';
  if (Math.abs(v) >= 1e5) return '₹' + (v / 1e5).toFixed(2) + ' L';
  if (Math.abs(v) >= 1e3) return '₹' + Math.round(v / 1e3) + 'k';
  return '₹' + Math.round(v);
}

const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
};
const monthLabel = (ym) => {
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1)
    .toLocaleDateString('en-IN', { month: 'short' }) + (m === '01' ? ` '${y.slice(2)}` : '');
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

function token() { return localStorage.getItem(TOKEN_KEY) || ''; }

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
      ...(options.headers || {}),
    },
  });
  let body = {};
  try { body = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) throw Object.assign(new Error(body.error || `Request failed (${res.status})`),
    { status: res.status, body });
  return body;
}

/* ================================================================== LOGIN */
const loginError = $('loginError');
const otpInputs = all('.otp-row input');

function showLoginError(msg) {
  loginError.textContent = msg;
  loginError.classList.remove('hidden');
}
function clearLoginError() { loginError.classList.add('hidden'); }

async function requestOtp(resend = false) {
  const mobile = $('mobile').value.replace(/\D/g, '');
  if (!/^[6-9]\d{9}$/.test(mobile)) {
    showLoginError('Please enter a valid 10-digit Indian mobile number.');
    return;
  }
  clearLoginError();
  const btn = resend ? $('resendOtp') : $('sendOtp');
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Sending…';
  try {
    const r = await api('/api/auth/otp', { method: 'POST', body: JSON.stringify({ mobile }) });
    $('phoneStep').classList.add('hidden');
    $('otpStep').classList.remove('hidden');
    $('loginTitle').textContent = 'Verify it’s you.';
    $('loginSub').textContent = r.delivered
      ? `We’ve sent a 6-digit OTP to +91 ${r.masked}. It expires in 5 minutes.`
      : `We’ve sent a 6-digit OTP to +91 ${r.masked}.`;
    if (r.dev_otp) {
      $('devOtp').textContent =
        `SMS is not switched on for this server — your OTP is ${r.dev_otp}`;
      $('devOtp').classList.remove('hidden');
    } else {
      $('devOtp').classList.add('hidden');
    }
    otpInputs.forEach(i => { i.value = ''; });
    otpInputs[0].focus();
  } catch (e) {
    showLoginError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function verifyOtp() {
  const mobile = $('mobile').value.replace(/\D/g, '');
  const otp = otpInputs.map(i => i.value).join('');
  if (otp.length !== 6) { showLoginError('Please enter all 6 digits.'); return; }
  clearLoginError();
  const btn = $('verifyOtp');
  btn.disabled = true;
  btn.textContent = 'Verifying…';
  try {
    const r = await api('/api/auth/verify', {
      method: 'POST', body: JSON.stringify({ mobile, otp }),
    });
    localStorage.setItem(TOKEN_KEY, r.token);
    await enterApp();
  } catch (e) {
    showLoginError(e.message);
    otpInputs.forEach(i => { i.value = ''; });
    otpInputs[0].focus();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Verify & sign in →';
  }
}

$('phoneStep').addEventListener('submit', (e) => { e.preventDefault(); requestOtp(); });
$('otpStep').addEventListener('submit', (e) => { e.preventDefault(); verifyOtp(); });
$('resendOtp').addEventListener('click', () => requestOtp(true));
$('backToPhone').addEventListener('click', () => {
  $('otpStep').classList.add('hidden');
  $('phoneStep').classList.remove('hidden');
  $('devOtp').classList.add('hidden');
  clearLoginError();
  $('loginTitle').textContent = 'Your shopping, in your pocket.';
  $('loginSub').textContent = 'Enter the mobile number you give at the billing counter and '
    + 'we’ll send you a one-time password.';
});
$('mobile').addEventListener('input', (e) => {
  e.target.value = e.target.value.replace(/\D/g, '').slice(0, 10);
});
otpInputs.forEach((input, i) => {
  input.addEventListener('input', () => {
    input.value = input.value.replace(/\D/g, '').slice(-1);
    if (input.value && otpInputs[i + 1]) otpInputs[i + 1].focus();
    if (otpInputs.every(x => x.value)) verifyOtp();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && !input.value && otpInputs[i - 1]) otpInputs[i - 1].focus();
  });
  input.addEventListener('paste', (e) => {
    const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
    if (!digits) return;
    e.preventDefault();
    digits.split('').forEach((d, k) => { if (otpInputs[k]) otpInputs[k].value = d; });
    if (digits.length === 6) verifyOtp();
  });
});

$('logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* already gone */ }
  localStorage.removeItem(TOKEN_KEY);
  // an installed app keeps a cached copy of this shopper's API responses —
  // it must not survive into the next person's session on a shared handset
  if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage('clear-data-cache');
  }
  location.reload();
});

/* ================================================================== LOAD */
async function enterApp() {
  $('loginScreen').classList.add('hidden');
  $('customerApp').classList.remove('hidden');
  window.scrollTo(0, 0);
  await loadMe();
  loadStores();
  loadNudges();
  openDeepLinkTab();
}

// PWA shortcuts land on /?tab=offers etc.
function openDeepLinkTab() {
  const want = new URLSearchParams(location.search).get('tab');
  if (want && all('.tab').some(t => t.dataset.tab === want)) goTab(want);
}

async function loadMe(refresh = false) {
  try {
    const r = await api('/api/me' + (refresh ? '?refresh=1' : ''));
    STATE = { ...STATE, ...r, shown: 8 };
    render(r);
  } catch (e) {
    if (e.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      location.reload();
      return;
    }
    $('welcomeSub').textContent = e.message;
    $('statRow').innerHTML = `<div class="empty" style="grid-column:1/-1">${esc(e.message)}</div>`;
  }
}

function render(r) {
  const s = r.summary, m = r.membership;
  $('greetName').textContent = r.greeting;
  $('avatar').textContent = r.initials;
  $('liveChip').textContent = `Bills up to ${fmtDate(r.data_through)}`;
  $('profileMobile').value = '+91 ' + r.mobile;
  $('footMeta').textContent =
    `Purchases from ${fmtDate(r.cutoff)} · data through ${fmtDate(r.data_through)}`;

  const since = s.first_visit ? fmtDate(s.first_visit) : '—';
  $('welcomeSub').textContent = s.bills
    ? `${s.bills} bill${s.bills > 1 ? 's' : ''} with V2 since ${since}` +
      (s.days_since_last === 0 ? ' · you shopped today'
        : s.days_since_last != null ? ` · last visit ${s.days_since_last} day(s) ago` : '')
    : `We show purchases from ${fmtDate(r.cutoff)} onwards — we couldn’t find any `
      + `against your number in that period.`;

  renderStats(s);
  renderMemberChip(m);
  renderVip(s, m);
  renderStoreFilter(s);
  renderBills();
  renderInsights(s);
  renderMembership(s, m);
  fillProfile(r.profile);
}

function renderStats(s) {
  const tiles = [
    ['Lifetime spend', money(s.spend),
      `across ${s.purchases} bill${s.purchases === 1 ? '' : 's'}` +
      (s.returns ? ` · net of ${s.returns} return${s.returns === 1 ? '' : 's'}` : '')],
    ['Total savings', money(s.saved), s.spend > 0
      ? `${(100 * s.saved / (s.spend + s.saved)).toFixed(0)}% off your MRP value` : 'on V2 offers'],
    ['Items bought', String(s.items), `${s.stores_visited} store${s.stores_visited === 1 ? '' : 's'} visited`],
    ['Average bill', money(s.atv), s.last_visit ? `last visit ${fmtDate(s.last_visit)}` : ''],
  ];
  $('statRow').innerHTML = tiles.map(([l, v, sub]) => `
    <div class="stat"><div class="label">${esc(l)}</div>
      <div class="value">${esc(v)}</div><div class="sub">${esc(sub)}</div></div>`).join('');
}

function renderMemberChip(m) {
  const chip = $('memberChip');
  if (!m.is_member) { chip.classList.add('hidden'); return; }
  chip.classList.remove('hidden');
  chip.textContent = `✦ ${m.tier.toUpperCase()} MEMBER` + (m.active ? '' : ' · EXPIRED');
}

function renderVip(s, m) {
  const card = $('vipCard');
  card.classList.remove('hidden', 'join');
  if (m.is_member) {
    const saved = s.member_saved;
    const roi = s.card_roi;
    card.innerHTML = `
      <div>
        <div class="vip-top">✦ V2 VIP CLUB · YOUR MEMBERSHIP</div>
        <h2>${esc(m.tier)} Member</h2>
        <p>${(m.rate * 100).toFixed(0)}% member discount on eligible purchases.
           ${m.active
            ? `Valid till ${fmtDate(m.valid_till)} — ${m.days_left} day(s) left.`
            : `Expired on ${fmtDate(m.valid_till)}. Renew at any V2 store.`}</p>
      </div>
      <div class="vip-side">
        <div class="k">Saved with your card</div>
        <div class="v">${money(saved)}</div>
        <div class="k">Membership fee paid</div>
        <div class="v">${money(m.fees_paid)}${roi > 0
          ? ` <span style="color:#8FE3B4;font-size:13px">· ${money(roi)} ahead</span>` : ''}</div>
      </div>`;
  } else if (s.bills) {
    card.classList.add('join');
    card.innerHTML = `
      <div>
        <div class="vip-top">✦ V2 VIP CLUB · NOT A MEMBER YET</div>
        <h2>You could be saving more</h2>
        <p>A Gold membership (₹500 a year) gives 5% off every eligible purchase.
           On what you spent in the last 12 months, that is about
           <strong style="color:#fff">${money(s.card_opportunity)}</strong> back in your pocket.</p>
      </div>
      <div class="vip-side">
        <div class="k">Your last 12 months</div><div class="v">${money(s.spend_12m)}</div>
        <div class="k">Ask for a card at</div>
        <div class="v" style="font-size:15px">${esc(s.favourite_store ? s.favourite_store.store : 'any V2 store')}</div>
      </div>`;
  } else {
    card.classList.add('hidden');
  }
}

/* ================================================================== BILLS */
function renderStoreFilter(s) {
  const sel = $('billStore');
  sel.innerHTML = '<option value="">All stores</option>' +
    s.by_store.map(st => `<option value="${esc(st.code)}">${esc(st.store)}</option>`).join('');
}

function filteredBills() {
  const q = $('billSearch').value.trim().toLowerCase();
  const period = $('billPeriod').value;
  const store = $('billStore').value;
  const through = STATE.data_through ? new Date(STATE.data_through + 'T00:00:00') : new Date();

  let from = null;
  if (period === '90' || period === '365') {
    from = new Date(through);
    from.setDate(from.getDate() - Number(period));
  } else if (period === 'thisfy') {
    const y = through.getMonth() + 1 >= 4 ? through.getFullYear() : through.getFullYear() - 1;
    from = new Date(y, 3, 1);
  }

  return STATE.bills.filter(b => {
    if (store && b.store_code !== store) return false;
    if (from && new Date(b.date_iso + 'T00:00:00') < from) return false;
    if (q) {
      const hay = (b.receipt + ' ' + b.store + ' ' + b.city + ' ' +
        b.lines.map(l => l.item).join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderBills() {
  const list = filteredBills();
  const slice = list.slice(0, STATE.shown);
  const total = list.reduce((n, b) => n + b.total, 0);
  $('billCount').textContent = list.length
    ? `${list.length} bill${list.length === 1 ? '' : 's'} · ${money(total)}` : '';

  if (!list.length) {
    $('billList').innerHTML = STATE.bills.length
      ? `<div class="empty">No bills match these filters.</div>`
      : `<div class="empty">Your account shows purchases made from
         <strong>${fmtDate(STATE.cutoff)}</strong> onwards. Anything you bought
         before that is not shown here.<br><br>Shop at any V2 store with this
         mobile number and your bills will appear automatically.</div>`;
    $('showMore').classList.add('hidden');
    return;
  }

  $('billList').innerHTML = slice.map(b => `
    <button class="purchase${b.is_return ? ' is-return' : ''}" data-bill="${esc(b.receipt)}">
      <span class="purchase-left">
        <span class="store-icon">${b.is_return ? '↩' : '▤'}</span>
        <span>
          <h4>${esc(b.store)}${b.is_return ? ' <em class="tag">Return</em>' : ''}</h4>
          <small>${esc(b.date_label)} · ${esc(b.time_label)} · Bill ${esc(b.receipt)}</small>
          <small>${b.is_return
            ? `${Math.abs(b.items)} item${Math.abs(b.items) === 1 ? '' : 's'} returned`
            : `${b.items} item${b.items === 1 ? '' : 's'}`}${b.member_tier
            ? ` · ${esc(b.member_tier)} member discount applied` : ''}</small>
        </span>
      </span>
      <span class="purchase-right">
        <strong>${b.is_return ? '− ' + money(Math.abs(b.total), 2) : money(b.total, 2)}</strong>
        ${!b.is_return && b.discount > 0 ? `<span class="saved">saved ${money(b.discount)}</span>` : ''}
        ${b.is_return ? '<span class="saved refund">refunded to you</span>' : ''}
        <span class="link">VIEW BILL ↗</span>
      </span>
    </button>`).join('');

  $('showMore').classList.toggle('hidden', list.length <= STATE.shown);
  all('[data-bill]').forEach(btn => btn.addEventListener('click',
    () => showReceipt(btn.dataset.bill)));
}

['billSearch', 'billPeriod', 'billStore'].forEach(id =>
  $(id).addEventListener('input', () => { STATE.shown = 8; renderBills(); }));
$('showMore').addEventListener('click', () => { STATE.shown += 12; renderBills(); });

// the CSV endpoint is bearer-authenticated, so a plain href cannot fetch it
$('csvLink').addEventListener('click', async (e) => {
  e.preventDefault();
  try {
    const res = await fetch('/api/bills.csv', { headers: { Authorization: `Bearer ${token()}` } });
    if (!res.ok) throw new Error('Download failed');
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `V2_purchases_${STATE.data_through || 'export'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast(err.message);
  }
});

/* ================================================================== RECEIPT */
// "Wed, 04 Nov 2026" — built from parts because en-IN renders the long form as
// "Wed, 04 Nov, 2026", and the printed slip has no comma before the year
function slipDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  const wd = d.toLocaleDateString('en-GB', { weekday: 'short' });
  const dd = String(d.getDate()).padStart(2, '0');
  const mon = d.toLocaleDateString('en-GB', { month: 'short' });
  return `${wd}, ${dd} ${mon} ${d.getFullYear()}`;
}
// 9998753536 -> 99987 53536, as it appears on the till slip
const slipMobile = (m) => String(m || '').replace(/^(\d{5})(\d{5})$/, '$1&nbsp;$2');

function showReceipt(receipt) {
  const b = STATE.bills.find(x => x.receipt === receipt);
  if (!b) return;
  const abs = Math.abs;
  const lines = b.lines;
  const items = lines.filter(l => !l.complimentary).length;
  const gross = lines.reduce((n, l) => n + abs(l.mrp), 0);
  const disc = lines.reduce((n, l) => n + abs(l.discount), 0);
  const net = gross - disc;
  const paid = abs(b.total);
  // the till rounds; show the difference rather than let the maths look wrong
  const roundOff = b.itemised ? +(paid - net).toFixed(2) : 0;

  const rows = lines.map(l => {
    const d = abs(l.discount);
    const sub = l.complimentary ? 'Complimentary' : (l.sub || l.spec || '');
    return `<tr>
      <td class="name">${esc(l.item)}
        ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}
        ${l.art ? `<div class="art">${esc(l.art)}</div>` : ''}</td>
      <td class="num${b.itemised && d > 0 ? ' strike' : ''}">${
        b.itemised ? money(abs(l.mrp), 2) : '—'}</td>
      <td class="num">${b.itemised ? (d > 0 ? '−' + money(d, 2) : '—') : '—'}</td>
      <td class="num amt">${b.itemised ? money(abs(l.net), 2) : '—'}</td>
    </tr>`;
  }).join('');

  let totals = `<div>Items</div><div class="num">${items}</div>`;
  if (b.itemised) {
    totals += `<div>Subtotal (MRP)</div><div class="num">${money(gross, 2)}</div>`;
    if (disc > 0) {
      totals += `<div class="savings">Total Discount</div>`
        + `<div class="num savings">−${money(disc, 2)}</div>`;
    }
    if (b.member_discount > 0) {
      totals += `<div>of which ${esc(b.member_tier)} member</div>`
        + `<div class="num">${money(b.member_discount, 2)}</div>`;
    }
    if (Math.abs(roundOff) >= 0.005) {
      totals += `<div>Round Off</div><div class="num">`
        + `${roundOff >= 0 ? '+' : '−'}${money(Math.abs(roundOff), 2)}</div>`;
    }
  }
  totals += `<div class="grand">${b.is_return ? 'Amount Refunded' : 'Amount Paid'}</div>`
    + `<div class="num grand">${money(paid, 2)}</div>`;

  const storeLine = [b.city, b.state].filter(Boolean).join(', ');
  $('receiptContent').innerHTML = `
    <div class="slip-brand">
      <img src="/static/img/v2-logo.png" alt="V2 Retail">
      <div class="doc-type">${b.is_return ? 'Retail Refund Receipt' : 'Retail Cash Receipt'}</div>
      <div class="store">${esc((b.store_name || '').toUpperCase())}</div>
      <div class="addr">${esc(storeLine)} &nbsp;·&nbsp; Store Code ${esc(b.store_code)}</div>
    </div>
    <hr class="rule">
    <dl class="meta">
      <dt>Bill No.</dt><dd class="bill-no">${esc(b.receipt)}</dd>
      <dt>Store Code</dt><dd>${esc(b.store_code)}</dd>
      <dt>Date</dt><dd>${slipDate(b.date_iso)}</dd>
      <dt>Time</dt><dd>${esc(b.time_label)}</dd>
      ${b.cashier ? `<dt>Served by</dt><dd>${esc(b.cashier)}</dd>` : ''}
      <dt>Mobile</dt><dd>${slipMobile(STATE.mobile)}</dd>
      ${b.member_tier ? `<dt>Membership</dt><dd>${esc(b.member_tier)}</dd>` : ''}
    </dl>
    <hr class="rule">
    <table class="slip-items">
      <thead><tr>
        <th>Item</th><th class="num">MRP</th><th class="num">Disc</th><th class="num">Amount</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <hr class="rule">
    <div class="slip-totals">${totals}</div>
    ${b.itemised ? '' : `<div class="notice">This bill predates item-level pricing
      in our records, so only the items and the amount paid are shown.</div>`}
    <div class="barcode"></div>
    <div class="barcode-label">${esc(b.receipt)}</div>
    <div class="thanks">${b.is_return
      ? 'Your return has been recorded.' : 'Thank you for shopping with V2!'}</div>
    <div class="footnote">
      Digital copy regenerated from internal CRM sales records for personal reference.<br>
      Not a tax invoice. Original billing terms &amp; return policy apply.
    </div>`;
  $('receiptModal').classList.remove('hidden');
}

const closeReceipt = () => $('receiptModal').classList.add('hidden');
$('closeReceipt').addEventListener('click', closeReceipt);
$('printReceipt').addEventListener('click', () => window.print());
$('receiptModal').addEventListener('click', e => { if (e.target === $('receiptModal')) closeReceipt(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeReceipt(); });

/* ================================================================== CHARTS
   Both charts are single-series magnitude, so they use one brand hue and carry
   identity in the labels — no categorical palette, no legend. */
function tooltip(host, x, y, html) {
  let tip = el('.tip', host);
  if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; host.appendChild(tip); }
  tip.innerHTML = html;
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
  tip.style.display = 'block';
}
function hideTip(host) { const t = el('.tip', host); if (t) t.style.display = 'none'; }

function monthBars(host, months) {
  if (!months.length) { host.innerHTML = '<div class="empty">No purchases yet.</div>'; return; }
  const W = 720, H = 220, padL = 44, padR = 10, padT = 14, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = Math.max(...months.map(m => m.spend), 1);
  const step = iw / months.length;
  const bw = Math.min(38, step - 8);
  const ticks = [0, max / 2, max];

  const bars = months.map((m, i) => {
    const h = Math.max((m.spend / max) * ih, m.spend > 0 ? 3 : 0);
    const x = padL + i * step + (step - bw) / 2;
    const y = padT + ih - h;
    return `<rect class="bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}"
      height="${h.toFixed(1)}" rx="4"></rect>
      <rect class="bar-hit" x="${(padL + i * step).toFixed(1)}" y="${padT}"
        width="${step.toFixed(1)}" height="${ih}"
        data-i="${i}" data-x="${(padL + i * step + step / 2).toFixed(1)}"
        data-y="${y.toFixed(1)}"></rect>`;
  }).join('');

  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Monthly amount paid at V2 stores">
    ${ticks.map(t => {
      const y = padT + ih - (t / max) * ih;
      return `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"></line>
              <text class="axis-text" x="${padL - 8}" y="${(y + 3).toFixed(1)}" text-anchor="end">${compactMoney(t)}</text>`;
    }).join('')}
    ${bars}
    ${months.map((m, i) => {
      if (months.length > 8 && i % 2) return '';
      const x = padL + i * step + step / 2;
      return `<text class="axis-text" x="${x.toFixed(1)}" y="${H - 8}" text-anchor="middle">${monthLabel(m.month)}</text>`;
    }).join('')}
  </svg>`;

  const svg = el('svg', host);
  all('.bar-hit', svg).forEach(hit => {
    hit.addEventListener('mouseenter', () => {
      const m = months[Number(hit.dataset.i)];
      const r = svg.getBoundingClientRect();
      const sx = r.width / W;
      tooltip(host, Number(hit.dataset.x) * sx, Number(hit.dataset.y) * (r.height / H),
        `<b>${money(m.spend)}</b> paid<br>${m.bills} bill(s) · saved ${money(m.saved)}`);
    });
    hit.addEventListener('mouseleave', () => hideTip(host));
  });
}

function categoryBars(host, cats) {
  if (!cats.length) { host.innerHTML = '<div class="empty">Nothing to show yet.</div>'; return; }
  const rows = cats.slice(0, 6);
  const max = Math.max(...rows.map(c => c.spend), 1);
  const rowH = 38, labelW = 112, W = 460, padR = 62;
  const H = rows.length * rowH;
  const bw = W - labelW - padR;

  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Spend by product category">
    ${rows.map((c, i) => {
      const y = i * rowH + 9;
      const w = Math.max((c.spend / max) * bw, 3);
      return `
        <text class="cat-label" x="0" y="${y + 14}">${esc(c.category)}</text>
        <rect class="bar-track" x="${labelW}" y="${y}" width="${bw}" height="19" rx="4"></rect>
        <rect class="bar" x="${labelW}" y="${y}" width="${w.toFixed(1)}" height="19" rx="4"></rect>
        <text class="val-text" x="${W - 4}" y="${y + 14}" text-anchor="end">${compactMoney(c.spend)}</text>`;
    }).join('')}
  </svg>`;
}

/* ================================================================== INSIGHTS */
// The API only returns months the shopper actually bought in. A time axis has to
// show the quiet months too, so pad the window out to 12 calendar months.
function last12(byMonth, through) {
  const have = new Map(byMonth.map(m => [m.month, m]));
  const end = new Date((through || new Date().toISOString().slice(0, 10)) + 'T00:00:00');
  const out = [];
  for (let i = 11; i >= 0; i--) {
    const d = new Date(end.getFullYear(), end.getMonth() - i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    out.push(have.get(key) || { month: key, spend: 0, saved: 0, bills: 0 });
  }
  return out;
}

function renderInsights(s) {
  const months = last12(s.by_month, STATE.data_through);
  $('monthRange').textContent = months.length
    ? `${monthLabel(months[0].month)} — ${monthLabel(months[months.length - 1].month)}` : '';
  monthBars($('monthChart'), months);
  categoryBars($('catChart'), s.by_category);

  const shown = s.by_category.slice(0, 6).length;
  $('catNote').textContent = s.itemised_bills < s.bills
    ? `Based on ${s.itemised_bills} of your ${s.bills} bills — item detail is on record from ${fmtDate(s.itemised_from)}.`
    : (shown < s.by_category.length ? `Top ${shown} of ${s.by_category.length} categories.` : '');

  $('storeList').innerHTML = s.by_store.slice(0, 8).map(st => `
    <div class="store-row">
      <span><span class="n">${esc(st.store)}</span>
        <span class="s">${st.bills} bill${st.bills === 1 ? '' : 's'}</span></span>
      <b>${money(st.spend)}</b>
    </div>`).join('') || '<div class="empty">No stores yet.</div>';

  const monthsActive = new Set(STATE.bills.map(b => b.date_iso.slice(0, 7))).size;
  const perYear = s.first_visit
    ? (s.bills / Math.max(1, (new Date(STATE.data_through) - new Date(s.first_visit))
      / (365.25 * 864e5))) : 0;
  $('rhythmRow').innerHTML = [
    ['Shopping with V2 since', fmtDate(s.first_visit), `${monthsActive} month(s) with a purchase`],
    ['Visits per year', perYear.toFixed(1), 'on average'],
    ['Favourite store', s.favourite_store ? s.favourite_store.store.replace('V2 Retail · ', '') : '—',
      s.favourite_store ? `${money(s.favourite_store.spend)} spent here` : ''],
    ['Biggest bill', money(Math.max(0, ...STATE.bills.map(b => b.total))), 'single visit'],
  ].map(([l, v, sub]) => `
    <div class="stat"><div class="label">${esc(l)}</div>
      <div class="value" style="font-size:19px">${esc(v)}</div>
      <div class="sub">${esc(sub)}</div></div>`).join('');
}

/* ================================================================== MEMBERSHIP */
function renderMembership(s, m) {
  const body = $('membershipBody');
  if (!m.is_member) {
    body.innerHTML = `
      <div class="panel">
        <div class="eyebrow">V2 VIP CLUB</div>
        <h2 style="margin:8px 0 6px">You’re not a member yet</h2>
        <p class="muted" style="margin:0">Members get a flat discount on eligible merchandise
          at the billing counter — no coupons, no minimum bill. Based on your last 12 months
          (${money(s.spend_12m)}), a Gold card would have returned about
          <strong>${money(s.card_opportunity)}</strong> against its ₹500 fee.</p>
      </div>`;
    all('.tier').forEach(t => t.classList.remove('current'));
    return;
  }
  body.innerHTML = `
    <div class="panel" style="margin-bottom:12px">
      <div class="eyebrow">V2 VIP CLUB</div>
      <h2 style="margin:8px 0 6px">${esc(m.tier)} · ${(m.rate * 100).toFixed(0)}% member discount</h2>
      <p class="muted" style="margin:0 0 16px">
        ${m.active ? `Active — valid through ${fmtDate(m.valid_till)}.`
          : `Expired on ${fmtDate(m.valid_till)}. Renew at any V2 store to start saving again.`}
        Present your registered mobile number at the counter.</p>
      <div class="kv-grid">
        <div class="kv"><small>Member since</small><strong>${fmtDate(m.since)}</strong></div>
        <div class="kv"><small>Current tier</small><strong>${esc(m.tier)}</strong></div>
        <div class="kv"><small>Valid till</small><strong>${fmtDate(m.valid_till)}</strong></div>
        <div class="kv"><small>Cards purchased</small><strong>${m.cards.length}</strong></div>
        <div class="kv"><small>Saved with the card</small><strong>${money(s.member_saved)}</strong></div>
        <div class="kv"><small>Fees paid</small><strong>${money(m.fees_paid)}</strong></div>
      </div>
    </div>
    <div class="panel">
      <div class="section-head tight"><h3>Your card history</h3></div>
      <div class="store-list">
        ${m.cards.map(c => `<div class="store-row">
          <span><span class="n">${esc(c.tier)} card</span>
            <span class="s">${esc(c.store)}</span></span>
          <b>${fmtDate(c.date)} · ${money(c.fee)}</b></div>`).join('')}
      </div>
    </div>`;
  all('.tier').forEach(t => t.classList.toggle('current', t.dataset.tier === m.tier));
}

/* ================================================================== PROFILE */
async function loadStores() {
  try {
    const r = await api('/api/stores');
    const opts = '<option value="">Select a store</option>' + r.stores.map(st =>
      `<option value="${esc(st.code)}">${esc(st.name)}${st.state ? ' — ' + esc(st.state) : ''}</option>`).join('');
    const sel = el('[name=preferred_store]', $('profileForm'));
    const keep = sel.value;
    sel.innerHTML = opts;
    sel.value = keep || (STATE.profile && STATE.profile.preferred_store) || '';
  } catch (_) { /* picker stays empty; the rest of the form still works */ }
}

const PROFILE_TEXT = ['name', 'email', 'dob', 'anniversary', 'preferred_store'];

function fillProfile(p) {
  if (!p) return;
  const f = $('profileForm');
  PROFILE_TEXT.forEach(k => {
    const input = el(`[name=${k}]`, f);
    if (input) input.value = p[k] || '';
  });
  const opt = el('[name=marketing_opt_in]', f);
  if (opt) opt.checked = !!p.marketing_opt_in;
}

$('profileForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const payload = { marketing_opt_in: el('[name=marketing_opt_in]', f).checked };
  PROFILE_TEXT.forEach(k => {
    payload[k] = el(`[name=${k}]`, f).value.trim() || null;
  });
  const btn = el('button[type=submit]', f);
  btn.disabled = true;
  $('profileMsg').textContent = 'Saving…';
  try {
    const r = await api('/api/profile', { method: 'POST', body: JSON.stringify(payload) });
    STATE.profile = r.profile;
    $('greetName').textContent = r.greeting;
    $('avatar').textContent = r.initials;
    // `synced` is false only while the warehouse is unreachable — the details are
    // already safe locally and the background worker keeps retrying.
    $('profileMsg').textContent = r.synced
      ? 'Saved to your V2 account.'
      : 'Saved — syncing to V2’s records shortly.';
    toast(r.synced ? 'Your details have been saved to your V2 account.'
                   : 'Saved. We’ll add this to your V2 records shortly.');
    setTimeout(() => { $('profileMsg').textContent = ''; }, 4000);
  } catch (err) {
    $('profileMsg').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

/* ================================================================== OFFERS */
async function loadOffers() {
  if (STATE.offers) return STATE.offers;
  const grid = $('offerGrid');
  grid.innerHTML = '<div class="offer skeleton"></div>'.repeat(4);
  try {
    const r = await api('/api/offers');
    STATE.offers = r.offers;
  } catch (e) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">${esc(e.message)}</div>`;
    return [];
  }
  renderOffers();
  return STATE.offers;
}

function renderOffers() {
  const list = STATE.offers || [];
  const grid = $('offerGrid');
  $('offerCount').textContent = list.length
    ? `${list.length} offer${list.length === 1 ? '' : 's'} running` : '';
  if (!list.length) {
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1">No offers are '
      + 'running right now. Check back soon.</div>';
    return;
  }
  grid.innerHTML = list.map(o => `
    <button class="offer" data-offer="${esc(o.offer_id)}">
      <span class="offer-top">
        <span class="offer-icon" aria-hidden="true">${esc(o.icon || '🎁')}</span>
        ${o.badge ? `<span class="offer-badge">${esc(o.badge)}</span>` : ''}
      </span>
      <strong>${esc(o.title)}</strong>
      <p>${esc(o.subtitle || '')}</p>
      <span class="offer-foot">
        ${o.scope === 'STORES' && o.store_count
          ? `At ${o.store_count} store${o.store_count === 1 ? '' : 's'}`
          : 'At all V2 stores'}
        ${o.valid_to ? ` · till ${fmtDate(o.valid_to)}` : ''}
        <em>View details →</em>
      </span>
    </button>`).join('');
  all('[data-offer]').forEach(b => b.addEventListener('click',
    () => showOffer(b.dataset.offer)));
}

function showOffer(id) {
  const o = (STATE.offers || []).find(x => x.offer_id === id);
  if (!o) return;
  $('offerModalTitle').textContent = o.title;
  $('offerContent').innerHTML = `
    <div class="offer-hero">
      <span class="offer-icon big" aria-hidden="true">${esc(o.icon || '🎁')}</span>
      <div>
        ${o.badge ? `<span class="offer-badge">${esc(o.badge)}</span>` : ''}
        <h2>${esc(o.title)}</h2>
        <p class="muted" style="margin:4px 0 0">${esc(o.subtitle || '')}</p>
      </div>
    </div>
    ${o.description ? `<p class="offer-copy">${esc(o.description)}</p>` : ''}
    ${o.mechanic ? `<div class="offer-block"><h4>How it works</h4>
        <ul>${o.mechanic.split('\n').filter(Boolean)
          .map(l => `<li>${esc(l.trim())}</li>`).join('')}</ul></div>` : ''}
    <div class="kv-grid" style="margin-top:14px">
      <div class="kv"><small>Where</small><strong>${o.scope === 'STORES' && o.store_count
        ? `${o.store_count} selected store${o.store_count === 1 ? '' : 's'}`
        : 'All V2 stores'}</strong></div>
      <div class="kv"><small>Valid</small><strong>${o.valid_to
        ? `till ${fmtDate(o.valid_to)}` : 'Until further notice'}</strong></div>
      ${o.min_bill ? `<div class="kv"><small>Minimum bill</small>
        <strong>${money(o.min_bill)}</strong></div>` : ''}
    </div>
    ${o.terms ? `<div class="offer-block terms-block"><h4>Terms</h4>
        <p>${esc(o.terms)}</p></div>` : ''}`;
  $('offerModal').classList.remove('hidden');
}

const closeOffer = () => $('offerModal').classList.add('hidden');
$('closeOffer').addEventListener('click', closeOffer);
$('offerDone').addEventListener('click', closeOffer);
$('offerFindStore').addEventListener('click', () => { closeOffer(); goTab('stores'); });
$('offerModal').addEventListener('click', e => { if (e.target === $('offerModal')) closeOffer(); });

/* ================================================================== STORES */
async function loadStoreFinder() {
  if (STATE.stores) return STATE.stores;
  $('storeGrid').innerHTML = '<div class="store-card skeleton"></div>'.repeat(4);
  try {
    const r = await api('/api/store-finder');
    STATE.stores = r.stores;
    STATE.storeShown = 12;
    const states = [...new Set(r.stores.map(s => s.state).filter(Boolean))].sort();
    $('storeState').innerHTML = '<option value="">All states</option>'
      + states.map(s => `<option>${esc(s)}</option>`).join('');
    $('storeMeta').textContent = r.stores.length
      ? `${r.stores.length} stores · ${r.with_directions} with directions` : '';
    $('nearMe').classList.toggle('hidden', !r.with_directions);
  } catch (e) {
    $('storeGrid').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return [];
  }
  renderStores();
  return STATE.stores;
}

function filteredStores() {
  const q = $('storeSearch').value.trim().toLowerCase();
  const st = $('storeState').value;
  return (STATE.stores || []).filter(s => {
    if (st && s.state !== st) return false;
    if (!q) return true;
    return [s.name, s.city, s.state, s.code, s.pincode, s.address]
      .filter(Boolean).join(' ').toLowerCase().includes(q);
  });
}

function renderStores() {
  const list = filteredStores();
  const slice = list.slice(0, STATE.storeShown || 12);
  if (!list.length) {
    // distinguish "nothing published" from "your search found nothing"
    $('storeGrid').innerHTML = (STATE.stores || []).length
      ? '<div class="empty">No stores match that search.</div>'
      : '<div class="empty">Our store list is being updated. Please check back '
        + 'shortly — in the meantime, your bills show the store you shopped at.</div>';
    $('moreStores').classList.add('hidden');
    return;
  }
  $('storeGrid').innerHTML = slice.map(s => `
    <article class="store-card">
      <div class="store-card-top">
        <div>
          <h4>${esc(s.name)}</h4>
          <small>${esc([s.city, s.state].filter(Boolean).join(', '))}${
            s.pincode ? ' · ' + esc(s.pincode) : ''}</small>
        </div>
        ${s.distance_km != null
          ? `<span class="dist">${s.distance_km} km</span>`
          : `<span class="store-code">${esc(s.code)}</span>`}
      </div>
      ${s.address ? `<p class="store-addr">${esc(s.address)}</p>` : ''}
      ${s.open_time ? `<p class="store-hours">Open ${esc(s.open_time)} – ${esc(s.close_time || '')}</p>` : ''}
      <div class="store-actions">
        <a class="ghost-btn" href="${esc(s.directions)}" target="_blank" rel="noopener">
          ${s.has_geo ? '🧭 Directions' : '🔎 Find on Maps'}</a>
        ${s.phone ? `<a class="ghost-btn" href="tel:${esc(s.phone)}">📞 Call</a>` : ''}
      </div>
      ${s.has_geo ? '' : '<p class="fine" style="margin:8px 0 0">Exact pin not on '
        + 'record yet — the link searches Maps by store name.</p>'}
    </article>`).join('');
  $('moreStores').classList.toggle('hidden', list.length <= (STATE.storeShown || 12));
}

['storeSearch', 'storeState'].forEach(id =>
  $(id).addEventListener('input', () => { STATE.storeShown = 12; renderStores(); }));
$('moreStores').addEventListener('click', () => {
  STATE.storeShown = (STATE.storeShown || 12) + 18;
  renderStores();
});

$('nearMe').addEventListener('click', () => {
  const note = $('geoNote');
  if (!navigator.geolocation) {
    note.textContent = 'Your browser cannot share a location.';
    note.classList.remove('hidden');
    return;
  }
  note.textContent = 'Finding your location…';
  note.classList.remove('hidden');
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const { latitude, longitude } = pos.coords;
    try {
      const r = await api(`/api/store-finder?lat=${latitude}&lng=${longitude}&limit=200`);
      STATE.stores = r.stores;
      STATE.storeShown = 12;
      $('storeSearch').value = '';
      $('storeState').value = '';
      note.textContent = `Showing your nearest V2 stores. Closest: `
        + `${r.stores[0] ? r.stores[0].name + ' (' + r.stores[0].distance_km + ' km)' : '—'}`;
      renderStores();
    } catch (e) {
      note.textContent = e.message;
    }
  }, (err) => {
    note.textContent = err.code === err.PERMISSION_DENIED
      ? 'Location permission was declined — you can still search by city or PIN code.'
      : 'Could not get your location. Search by city or PIN code instead.';
  }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
});

/* ================================================================== NUDGES */
async function loadNudges() {
  try {
    const r = await api('/api/nudges');
    STATE.nudges = r.nudges;
    $('bellDot').classList.toggle('hidden', !r.unread);
    renderNudgeTray(r.nudges.filter(n => !n.read).slice(0, 1));
  } catch (_) { /* nudges are a bonus; never block the app on them */ }
}

function renderNudgeTray(items) {
  const tray = $('nudgeTray');
  if (!items.length) { tray.classList.add('hidden'); return; }
  tray.classList.remove('hidden');
  tray.innerHTML = items.map(n => `
    <div class="nudge" data-nudge="${esc(n.nudge_id)}">
      <div class="nudge-body">
        <strong>${esc(n.title)}</strong>
        <p>${esc(n.body)}</p>
      </div>
      <div class="nudge-actions">
        ${n.cta_label ? `<button class="nudge-cta" data-go="${esc(n.cta_tab || 'offers')}">${esc(n.cta_label)}</button>` : ''}
        <button class="nudge-x" data-dismiss aria-label="Dismiss">×</button>
      </div>
    </div>`).join('');

  all('[data-nudge]').forEach(el => {
    const id = el.dataset.nudge;
    const cta = el.querySelector('[data-go]');
    if (cta) cta.addEventListener('click', () => {
      markNudge(id, false);
      goTab(cta.dataset.go);
      el.remove();
      if (!$('nudgeTray').children.length) $('nudgeTray').classList.add('hidden');
    });
    el.querySelector('[data-dismiss]').addEventListener('click', () => {
      markNudge(id, true);
      el.remove();
      if (!$('nudgeTray').children.length) $('nudgeTray').classList.add('hidden');
    });
  });
}

function markNudge(id, dismiss) {
  api(`/api/nudges/${encodeURIComponent(id)}/read`, {
    method: 'POST', body: JSON.stringify({ dismiss }),
  }).catch(() => { /* best effort */ });
  $('bellDot').classList.add('hidden');
}

$('bellBtn').addEventListener('click', () => {
  const unread = (STATE.nudges || []).filter(n => !n.read);
  if (!unread.length && !(STATE.nudges || []).length) {
    toast('No updates for you right now.');
    return;
  }
  renderNudgeTray((STATE.nudges || []).slice(0, 3));
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

/* ================================================================== TABS */
function goTab(name) {
  const tab = all('.tab').find(t => t.dataset.tab === name);
  if (tab) tab.click();
}

all('.tab').forEach(tab => tab.addEventListener('click', () => {
  all('.tab').forEach(t => t.classList.toggle('active', t === tab));
  all('.tab-panel').forEach(p => p.classList.add('hidden'));
  $(`${tab.dataset.tab}Panel`).classList.remove('hidden');
  const name = tab.dataset.tab;
  if (name === 'insights' && STATE.summary) renderInsights(STATE.summary);
  if (name === 'offers') loadOffers();
  if (name === 'stores') loadStoreFinder();
}));

/* ================================================================== PWA */
let installPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  installPrompt = e;
  $('installBtn').classList.remove('hidden');
});
$('installBtn').addEventListener('click', async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  const { outcome } = await installPrompt.userChoice;
  if (outcome === 'accepted') {
    $('installBtn').classList.add('hidden');
    toast('V2 Retail has been added to your home screen.');
  }
  installPrompt = null;
});
window.addEventListener('appinstalled', () => $('installBtn').classList.add('hidden'));

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* http:// or blocked */ });
  });
}

window.addEventListener('resize', () => {
  if (STATE.summary && !$('insightsPanel').classList.contains('hidden')) renderInsights(STATE.summary);
});

/* ================================================================== BOOT */
async function tryAdminHandoff() {
  // An admin opened this account via "view as customer" — the URL carries a
  // one-time code, never the real session token. Scrub it from the address
  // bar before even awaiting the exchange, so it never lingers visibly.
  const params = new URLSearchParams(location.search);
  const code = params.get('admin_access');
  if (!code) return;
  params.delete('admin_access');
  const qs = params.toString();
  history.replaceState({}, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
  try {
    const r = await api('/api/auth/exchange', { method: 'POST', body: JSON.stringify({ code }) });
    localStorage.setItem(TOKEN_KEY, r.token);
  } catch (e) {
    toast('That admin access link has expired.');
  }
}

(async function boot() {
  await tryAdminHandoff();
  if (!token()) return;
  $('loginScreen').classList.add('hidden');
  $('customerApp').classList.remove('hidden');
  await loadMe();
  loadStores();
  loadNudges();
  openDeepLinkTab();
})();
