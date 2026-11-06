/* V2 Customer App — admin panel.
   Vanilla JS. Everything it saves goes to Snowflake via /api/admin/*. */
'use strict';

const $ = (id) => document.getElementById(id);
const el = (s, r = document) => r.querySelector(s);
const all = (s, r = document) => [...r.querySelectorAll(s)];
const TOKEN_KEY = 'v2_admin_token';

let STATE = { offers: [], stores: [], nudges: [], storeShown: 25, editing: null };

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (n) => Number(n || 0).toLocaleString('en-IN');
const fmtDate = (iso) => iso
  ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN',
      { day: '2-digit', month: 'short', year: 'numeric' })
  : '—';

function toast(msg, ms = 2800) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

const token = () => localStorage.getItem(TOKEN_KEY) || '';

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
  try { body = await res.json(); } catch (_) { /* non-JSON */ }
  if (res.status === 401 && token()) { signOut(); }
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}

/* ================================================================== auth */
$('adminLoginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('adminLoginError');
  err.classList.add('hidden');
  try {
    const r = await api('/api/admin/login', {
      method: 'POST', body: JSON.stringify({ password: $('adminPass').value }),
    });
    localStorage.setItem(TOKEN_KEY, r.token);
    enter();
  } catch (e2) {
    err.textContent = e2.message;
    err.classList.remove('hidden');
  }
});

function signOut() {
  localStorage.removeItem(TOKEN_KEY);
  $('adminPanel').classList.add('hidden');
  $('adminLogin').classList.remove('hidden');
}

$('adminLogout').addEventListener('click', async () => {
  try { await api('/api/admin/logout', { method: 'POST' }); } catch (_) { /* gone */ }
  signOut();
});

async function enter() {
  $('adminLogin').classList.add('hidden');
  $('adminPanel').classList.remove('hidden');
  await Promise.all([loadOverview(), loadOffers(), loadStores(), loadNudges(),
                     loadSms()]);
}

/* ================================================================== overview */
async function loadOverview() {
  try {
    const o = await api('/api/admin/overview');
    const p = o.profiles || {};
    $('adminStats').innerHTML = [
      ['Offers live', `${o.offers_active}/${o.offers}`, 'active of total'],
      ['Stores listed', num(o.stores_listed),
        [o.stores_missing_geo ? `${o.stores_missing_geo} missing coordinates` : '',
         o.stores_suspect_geo ? `${o.stores_suspect_geo} need checking` : '']
          .filter(Boolean).join(' · ') || 'all have coordinates'],
      ['Nudges sent', num(o.nudges_sent), `${num(o.nudge_readers)} shoppers opened one`],
      ['Customer profiles', num(p.profiles || 0),
        `${num(p.opted_in || 0)} opted in to offers`],
    ].map(([l, v, s]) => `
      <div class="stat"><div class="label">${esc(l)}</div>
        <div class="value">${esc(v)}</div><div class="sub">${esc(s)}</div></div>`).join('');
  } catch (e) { toast(e.message); }
}

/* ================================================================== offers */
async function loadOffers() {
  try {
    const r = await api('/api/admin/offers');
    STATE.offers = r.offers;
    STATE.offerTypes = r.types;
    renderOffers();
  } catch (e) { $('offerRows').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function renderOffers() {
  if (!STATE.offers.length) {
    $('offerRows').innerHTML = '<div class="empty">No offers yet. Add the first one.</div>';
    return;
  }
  $('offerRows').innerHTML = STATE.offers.map(o => `
    <div class="row">
      <div class="row-main">
        <div class="row-ico">${esc(o.icon || '🎁')}</div>
        <div>
          <h4>${esc(o.title)}
            <span class="pill ${o.is_active ? 'on' : 'off'}">${o.is_active ? 'Live' : 'Off'}</span>
            ${o.badge ? `<span class="pill info">${esc(o.badge)}</span>` : ''}</h4>
          <small>${esc(o.subtitle || '')}</small>
          <small>${o.scope === 'STORES'
            ? `${o.stores.length} store(s)` : 'All stores'}
            · ${esc(o.segment || 'ALL')} customers
            · ${o.valid_from || o.valid_to
              ? `${fmtDate(o.valid_from)} → ${fmtDate(o.valid_to)}` : 'no end date'}
            · priority ${o.priority}</small>
        </div>
      </div>
      <div class="row-side">
        <button class="mini-btn" data-edit-offer="${esc(o.offer_id)}">Edit</button>
        <button class="mini-btn danger" data-del-offer="${esc(o.offer_id)}">Delete</button>
      </div>
    </div>`).join('');

  all('[data-edit-offer]').forEach(b =>
    b.addEventListener('click', () => editOffer(
      STATE.offers.find(o => o.offer_id === b.dataset.editOffer))));
  all('[data-del-offer]').forEach(b =>
    b.addEventListener('click', async () => {
      const o = STATE.offers.find(x => x.offer_id === b.dataset.delOffer);
      if (!confirm(`Delete "${o.title}"? Shoppers will stop seeing it immediately.`)) return;
      await api(`/api/admin/offers/${encodeURIComponent(o.offer_id)}`, { method: 'DELETE' });
      toast('Offer deleted.');
      loadOffers(); loadOverview();
    }));
}

$('newOffer').addEventListener('click', () => editOffer(null));

function editOffer(o) {
  const types = STATE.offerTypes || ['OTHER'];
  const v = o || { is_active: true, scope: 'ALL', segment: 'ALL', priority: 100, icon: '🎁' };
  STATE.editing = { kind: 'offer', id: o ? o.offer_id : '' };
  $('editTitle').textContent = o ? `Edit — ${o.title}` : 'New offer';
  $('editBody').innerHTML = `
    <label class="wide">Title<input name="title" value="${esc(v.title || '')}"
      placeholder="Bill Buster"></label>
    <label class="wide">One-line subtitle<input name="subtitle" value="${esc(v.subtitle || '')}"
      placeholder="The more you shop, the more you save"></label>
    <label>Icon (emoji)<input name="icon" value="${esc(v.icon || '🎁')}" maxlength="4"></label>
    <label>Badge<input name="badge" value="${esc(v.badge || '')}" placeholder="UP TO ₹500 OFF"></label>
    <label>Type<select name="offer_type">${types.map(t =>
      `<option ${v.offer_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label>Priority<input name="priority" type="number" value="${v.priority || 100}">
      <span class="hint">Lower shows first.</span></label>
    <label class="wide">Description<textarea name="description"
      placeholder="What the shopper gets, in plain words.">${esc(v.description || '')}</textarea></label>
    <label class="wide">How it works<textarea name="mechanic"
      placeholder="One rule per line — each line becomes a bullet in the app.">${esc(v.mechanic || '')}</textarea></label>
    <label class="wide">Terms<textarea name="terms">${esc(v.terms || '')}</textarea></label>
    <label>Minimum bill (₹)<input name="min_bill" type="number" value="${v.min_bill ?? ''}"></label>
    <label>Who sees it<select name="segment">
      ${['ALL', 'MEMBER', 'NON_MEMBER', 'SILVER', 'GOLD', 'PLATINUM'].map(s =>
        `<option ${v.segment === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
    <label>Valid from<input name="valid_from" type="date" value="${v.valid_from || ''}"></label>
    <label>Valid to<input name="valid_to" type="date" value="${v.valid_to || ''}">
      <span class="hint">Leave empty to run until you switch it off.</span></label>
    <label>Where it runs<select name="scope" id="scopeSel">
      <option value="ALL" ${v.scope !== 'STORES' ? 'selected' : ''}>All stores</option>
      <option value="STORES" ${v.scope === 'STORES' ? 'selected' : ''}>Selected stores</option>
    </select></label>
    <label class="wide ${v.scope === 'STORES' ? '' : 'hidden'}" id="storeCodesWrap">
      Store codes<textarea name="stores" placeholder="HO21, HK12, HW42">${(v.stores || []).join(', ')}</textarea>
      <span class="hint">Comma or space separated. A store-scoped offer still
        shows to everyone, marked with the number of stores.</span></label>
    <label class="check wide"><input type="checkbox" name="is_active"
      ${v.is_active ? 'checked' : ''}><span>Live — visible in the app right now</span></label>`;

  el('#scopeSel').addEventListener('change', (e) =>
    $('storeCodesWrap').classList.toggle('hidden', e.target.value !== 'STORES'));
  openEditor(async () => {
    const f = collect();
    f.offer_id = STATE.editing.id;
    f.stores = String(f.stores || '').split(/[,\s]+/).filter(Boolean);
    if (!f.title.trim()) { toast('An offer needs a title.'); return false; }
    await api('/api/admin/offers', { method: 'POST', body: JSON.stringify(f) });
    toast('Offer saved — shoppers see it now.');
    loadOffers(); loadOverview();
    return true;
  });
}

/* ================================================================== stores */
async function loadStores() {
  try {
    const r = await api('/api/admin/stores');
    STATE.stores = r.stores;
    const missing = r.stores.filter(s => !s.has_geo).length;
    const suspect = r.stores.filter(s => s.suspect_km).length;
    $('geoSummary').textContent = r.stores.length
      ? `${r.stores.length} published · ${missing} without coordinates · ${suspect} to check`
      : 'No stores published yet';
    $('notListedNote').textContent = r.not_listed_count
      ? `${r.not_listed_count} store(s) that took bills in the last 90 days are not in `
        + `your sheet, so they do not appear in the app: `
        + `${r.not_listed.slice(0, 12).join(', ')}`
        + `${r.not_listed_count > 12 ? ` and ${r.not_listed_count - 12} more` : ''}.`
      : '';
    renderStores();
  } catch (e) { $('storeRows').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function filteredAdminStores() {
  const q = $('adminStoreSearch').value.trim().toLowerCase();
  const mode = $('adminStoreFilter').value;
  return STATE.stores.filter(s => {
    if (mode === 'missing' && (s.has_geo || !s.operational)) return false;
    if (mode === 'admin' && s.source !== 'sheet') return false;
    if (mode === 'suspect' && !s.suspect_km) return false;
    if (!q) return true;
    return [s.code, s.name, s.city, s.state, s.pincode].filter(Boolean)
      .join(' ').toLowerCase().includes(q);
  });
}

function renderStores() {
  const list = filteredAdminStores();
  const slice = list.slice(0, STATE.storeShown);
  if (!list.length) {
    $('storeRows').innerHTML = STATE.stores.length
      ? '<div class="empty">No stores match.</div>'
      : '<div class="empty">No stores published yet. Download the sheet, fill it '
        + 'in and upload it — the app shows exactly what this sheet contains.</div>';
    $('moreAdminStores').classList.add('hidden');
    return;
  }
  $('storeRows').innerHTML = slice.map(s => `
    <div class="row">
      <div class="row-main">
        <div class="row-ico">${s.has_geo ? '📍' : '❓'}</div>
        <div>
          <h4>${esc(s.name)} <span class="pill off">${esc(s.code)}</span>
            ${s.has_geo ? '' : '<span class="pill warn">No coordinates</span>'}
            ${s.is_active ? '' : '<span class="pill off">Hidden in app</span>'}
            ${s.suspect_km ? '<span class="pill warn">Check location</span>' : ''}</h4>
          <small>${esc([s.city, s.state, s.pincode].filter(Boolean).join(' · '))}</small>
          <small>${s.has_geo ? `${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}`
            : 'Customers get a Maps name-search instead of a pin'}${
            s.phone ? ' · ☎ ' + esc(s.phone) : ''}</small>
          ${s.suspect_km ? `<small style="color:var(--gold)">Pin sits ${Math.round(s.suspect_km)}
            km from the middle of ${esc(s.state)} — either the pin or the state is
            wrong. Open "Check pin" to see where it actually lands.</small>` : ''}
        </div>
      </div>
      <div class="row-side">
        ${s.has_geo ? `<a class="mini-btn" href="${esc(s.maps)}" target="_blank"
          rel="noopener">Check pin</a>` : ''}
        <button class="mini-btn go" data-edit-store="${esc(s.code)}">
          ${s.has_geo ? 'Edit' : 'Add location'}</button>
        <button class="mini-btn danger" data-del-store="${esc(s.code)}">Remove</button>
      </div>
    </div>`).join('');
  $('moreAdminStores').classList.toggle('hidden', list.length <= STATE.storeShown);
  all('[data-edit-store]').forEach(b => b.addEventListener('click',
    () => editStore(STATE.stores.find(s => s.code === b.dataset.editStore))));
  all('[data-del-store]').forEach(b => b.addEventListener('click', async () => {
    const s = STATE.stores.find(x => x.code === b.dataset.delStore);
    if (!confirm(`Remove ${s.name} (${s.code}) from the app's store list?\n\n`
      + `It disappears from Find My Store straight away. Upload it again in the `
      + `sheet to bring it back.`)) return;
    await api(`/api/admin/stores/${encodeURIComponent(s.code)}`, { method: 'DELETE' });
    toast(`${s.code} removed from the store list.`);
    loadStores(); loadOverview();
  }));
}

['adminStoreSearch', 'adminStoreFilter'].forEach(id =>
  $(id).addEventListener('input', () => { STATE.storeShown = 25; renderStores(); }));
$('moreAdminStores').addEventListener('click', () => {
  STATE.storeShown += 40; renderStores();
});

/* ---------------------------------------------- store sheet upload */
$('downloadTemplate').addEventListener('click', async () => {
  const btn = $('downloadTemplate');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Building…';
  try {
    const res = await fetch('/api/admin/stores/template',
      { headers: { Authorization: `Bearer ${token()}` } });
    if (!res.ok) throw new Error('Could not build the template.');
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `V2_store_locations_${new Date().toISOString().slice(0, 10)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
    toast('Template downloaded — fill in the coordinates and upload it back.');
  } catch (e) { toast(e.message); } finally {
    btn.disabled = false; btn.textContent = was;
  }
});

$('sheetFile').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';                       // allow re-picking the same file
  await previewSheet(file);
});

async function postSheet(file, preview) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`/api/admin/stores/upload${preview ? '?preview=1' : ''}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token()}` },
    body: fd,                                 // no Content-Type: the browser sets the boundary
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Upload failed (${res.status})`);
  return body;
}

async function previewSheet(file) {
  const box = $('uploadResult');
  box.classList.remove('hidden');
  box.innerHTML = `<div class="panel"><p class="muted" style="margin:0">
    Reading <strong>${esc(file.name)}</strong>…</p></div>`;
  let r;
  try {
    r = await postSheet(file, true);
  } catch (err) {
    box.innerHTML = `<div class="alert">${esc(err.message)}</div>`;
    return;
  }
  const rep = r.report, ch = r.changes;
  const problems = rep.problems || [];
  box.innerHTML = `
    <div class="panel">
      <div class="section-head tight">
        <h3>Preview — nothing saved yet</h3>
        <span>${esc(file.name)} · sheet "${esc(rep.sheet)}"</span>
      </div>
      <div class="stats" style="margin-bottom:14px">
        <div class="stat"><div class="label">Rows read</div>
          <div class="value">${num(rep.parsed)}</div>
          <div class="sub">${num(rep.with_geo)} with coordinates</div></div>
        <div class="stat"><div class="label">New stores</div>
          <div class="value">${num(ch.new_stores)}</div>
          <div class="sub">${num(ch.new_pins)} getting a pin for the first time</div></div>
        <div class="stat"><div class="label">Will change</div>
          <div class="value">${num(ch.changed)}</div>
          <div class="sub">${num(ch.unchanged)} already match</div></div>
        <div class="stat"><div class="label">Skipped</div>
          <div class="value">${num(rep.problem_count)}</div>
          <div class="sub">rows we could not use</div></div>
      </div>
      ${ch.not_in_sheet_count ? `<div class="notice" style="margin:0 0 12px">
        <strong>${num(ch.not_in_sheet_count)} store(s) already published are not in
        this sheet</strong> — ${ch.not_in_sheet.slice(0, 15).map(esc).join(', ')}${
          ch.not_in_sheet_count > 15 ? ` and ${ch.not_in_sheet_count - 15} more` : ''}.
        They stay in the app. An upload adds and updates; use Remove on a row to
        take a store down.</div>` : ''}
      ${rep.new_code_count ? `<p class="fine" style="margin:0 0 6px">
        ${num(rep.new_code_count)} code(s) are not in our store master —
        ${rep.new_codes.slice(0, 10).map(esc).join(', ')}. Accepted anyway
        (a new store may not have reached the warehouse yet), but check for typos.</p>` : ''}
      <p class="fine" style="margin:0 0 6px">Columns matched:
        ${(rep.mapped_columns || []).map(c => `<code>${esc(c)}</code>`).join(' ')}
        ${(rep.unmapped_columns || []).length
          ? `<br>Ignored columns: ${rep.unmapped_columns.map(c =>
              `<code>${esc(c)}</code>`).join(' ')}` : ''}</p>
      ${problems.length ? `
        <details class="problems"><summary>${num(rep.problem_count)} row(s) skipped — see why</summary>
          <ul>${problems.slice(0, 50).map(p =>
            `<li>Row ${p.row}${p.code ? ` (${esc(p.code)})` : ''}: ${esc(p.why)}</li>`).join('')}
          </ul>${rep.problem_count > 50 ? `<p class="fine">…and ${rep.problem_count - 50} more.</p>` : ''}
        </details>` : ''}
      <div class="form-actions" style="margin-top:16px">
        <button class="primary-btn" id="confirmUpload"
          ${rep.parsed ? '' : 'disabled'}>Publish ${num(rep.parsed)} store(s)</button>
        <button class="ghost-btn" id="cancelUpload">Cancel</button>
      </div>
    </div>`;

  $('cancelUpload').addEventListener('click', () => box.classList.add('hidden'));
  $('confirmUpload').addEventListener('click', async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = 'Saving…';
    try {
      const done = await postSheet(file, false);
      box.innerHTML = `<div class="panel"><div class="section-head tight">
          <h3>Saved</h3></div>
        <p class="muted" style="margin:0">${num(done.saved)} store(s) published.
          The app is showing them now.
          ${done.failed.length ? `<br><strong>${done.failed.length} failed:</strong> `
            + done.failed.slice(0, 10).map(f => `${esc(f.code)} (${esc(f.why)})`).join(', ')
            : ''}</p></div>`;
      toast(`${done.saved} store locations saved.`);
      loadStores(); loadOverview();
    } catch (err) {
      box.innerHTML = `<div class="alert">${esc(err.message)}</div>`;
    }
  });
}

function editStore(s) {
  STATE.editing = { kind: 'store', id: s.code };
  $('editTitle').textContent = `${s.name} · ${s.code}`;
  $('editBody').innerHTML = `
    <label>Store name<input name="name" value="${esc(s.name || '')}"></label>
    <label>City<input name="city" value="${esc(s.city || '')}"></label>
    <label class="wide">State<input name="state" value="${esc(s.state || '')}"></label>
    <label class="wide">Paste a Google Maps link or "lat, lng"
      <input name="paste" placeholder="https://maps.google.com/...@28.6139,77.2090  or  28.6139, 77.2090">
      <span class="hint">Easiest way: open the store in Google Maps, right-click
        the pin, copy the coordinates, paste here. This overrides the two fields below.</span></label>
    <label>Latitude<input name="lat" type="number" step="0.000001" value="${s.lat ?? ''}"></label>
    <label>Longitude<input name="lng" type="number" step="0.000001" value="${s.lng ?? ''}"></label>
    <label class="wide">Address<textarea name="address">${esc(s.address || '')}</textarea></label>
    <label>PIN code<input name="pincode" value="${esc(s.pincode || '')}" maxlength="10"></label>
    <label>Phone<input name="phone" value="${esc(s.phone || '')}"></label>
    <label>Opens at<input name="open_time" value="${esc(s.open_time || '')}" placeholder="10:00 AM"></label>
    <label>Closes at<input name="close_time" value="${esc(s.close_time || '')}" placeholder="9:30 PM"></label>
    <label class="check wide"><input type="checkbox" name="is_active"
      ${s.is_active === false ? '' : 'checked'}>
      <span>Show this store in the app's store finder</span></label>`;
  openEditor(async () => {
    const f = collect();
    await api(`/api/admin/stores/${encodeURIComponent(s.code)}`,
      { method: 'POST', body: JSON.stringify(f) });
    toast(`${s.code} location saved.`);
    loadStores(); loadOverview();
    return true;
  });
}

/* ================================================================== nudges */
async function loadNudges() {
  try {
    const r = await api('/api/admin/nudges');
    STATE.nudges = r.nudges;
    STATE.audiences = r.audiences;
    renderNudges();
  } catch (e) { $('nudgeRows').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function renderNudges() {
  if (!STATE.nudges.length) {
    $('nudgeRows').innerHTML = '<div class="empty">No nudges yet.</div>';
    return;
  }
  $('nudgeRows').innerHTML = STATE.nudges.map(n => `
    <div class="row">
      <div class="row-main">
        <div class="row-ico">${n.status === 'SENT' ? '📣' : '📝'}</div>
        <div>
          <h4>${esc(n.title)}
            <span class="pill ${n.status === 'SENT' ? 'on' : 'off'}">${esc(n.status)}</span></h4>
          <small>${esc(n.body || '')}</small>
          <small>To ${esc(n.audience_type)}${n.audience_value
            ? ' · ' + esc(String(n.audience_value).slice(0, 40)) : ''}
            ${n.status === 'SENT'
              ? `· ~${num(n.est_recipients)} shoppers · ${num(n.opened)} opened`
                + ` · ${num(n.dismissed)} dismissed · sent ${esc(n.sent_at || '')}`
              : '· not sent yet'}</small>
        </div>
      </div>
      <div class="row-side">
        <button class="mini-btn" data-edit-nudge="${esc(n.nudge_id)}">
          ${n.status === 'SENT' ? 'View' : 'Edit'}</button>
        <button class="mini-btn danger" data-del-nudge="${esc(n.nudge_id)}">Delete</button>
      </div>
    </div>`).join('');

  all('[data-edit-nudge]').forEach(b => b.addEventListener('click',
    () => editNudge(STATE.nudges.find(n => n.nudge_id === b.dataset.editNudge))));
  all('[data-del-nudge]').forEach(b => b.addEventListener('click', async () => {
    const n = STATE.nudges.find(x => x.nudge_id === b.dataset.delNudge);
    if (!confirm(`Delete "${n.title}"? Shoppers will stop seeing it.`)) return;
    await api(`/api/admin/nudges/${encodeURIComponent(n.nudge_id)}`, { method: 'DELETE' });
    toast('Nudge deleted.');
    loadNudges(); loadOverview();
  }));
}

$('newNudge').addEventListener('click', () => editNudge(null));

const AUDIENCE_HELP = {
  ALL: 'Every customer with a V2 bill.',
  MEMBER: 'V2 VIP Club cardholders only.',
  NON_MEMBER: 'Customers who have never bought a card.',
  TIER: 'One card tier — put Silver, Gold or Platinum in the value box.',
  STORE: 'Customers who shop at one store — put the store code in the value box.',
  ACTIVE: 'Shopped within N days — put the number of days in the value box.',
  LAPSED: 'Has not shopped for N days — put the number of days in the value box.',
  MOBILES: 'A specific list — paste comma-separated 10-digit mobile numbers.',
};

function editNudge(n) {
  const sent = n && n.status === 'SENT';
  const v = n || { audience_type: 'ALL', cta_tab: 'offers', cta_label: 'See offers' };
  STATE.editing = { kind: 'nudge', id: n ? n.nudge_id : '' };
  $('editTitle').textContent = n ? (sent ? `Sent — ${n.title}` : `Edit — ${n.title}`) : 'New nudge';
  $('editBody').innerHTML = `
    ${sent ? `<div class="audience-box"><div><small>This nudge has been sent.</small>
      <strong>${num(n.opened)} opened</strong></div>
      <div><small>Audience</small><strong>~${num(n.est_recipients)}</strong></div></div>` : ''}
    <label class="wide">Title<input name="title" value="${esc(v.title || '')}"
      placeholder="Your Gold card saves you 5% — use it this weekend"></label>
    <label class="wide">Message<textarea name="body"
      placeholder="Keep it short — this appears as a banner at the top of their account.">${esc(v.body || '')}</textarea></label>
    <label>Button label<input name="cta_label" value="${esc(v.cta_label || '')}"
      placeholder="See offers"></label>
    <label>Button opens<select name="cta_tab">
      ${['offers', 'stores', 'purchases', 'membership', 'insights', 'profile'].map(t =>
        `<option ${v.cta_tab === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label>Send to<select name="audience_type" id="audSel">
      ${(STATE.audiences || ['ALL']).map(a =>
        `<option ${v.audience_type === a ? 'selected' : ''}>${a}</option>`).join('')}</select>
      <span class="hint" id="audHelp"></span></label>
    <label>Audience value<input name="audience_value" id="audVal"
      value="${esc(v.audience_value || '')}" placeholder="e.g. HO21, Gold, 180"></label>
    <label>Stop showing after<input name="valid_to" type="date" value="${v.valid_to || ''}">
      <span class="hint">Leave empty to keep it until you delete it.</span></label>
    <label>Link an offer<input name="offer_id" value="${esc(v.offer_id || '')}"
      placeholder="BILL-BUSTER"><span class="hint">Optional offer id.</span></label>
    <div class="audience-box">
      <div><small style="color:var(--text-3)">Estimated audience</small>
        <strong id="audCount">—</strong></div>
      <button class="mini-btn" type="button" id="estimateBtn">Check audience size</button>
    </div>`;

  const help = () => { $('audHelp').textContent = AUDIENCE_HELP[$('audSel').value] || ''; };
  help();
  $('audSel').addEventListener('change', help);
  $('estimateBtn').addEventListener('click', async () => {
    $('audCount').textContent = 'counting…';
    try {
      const r = await api('/api/admin/nudges/estimate', {
        method: 'POST',
        body: JSON.stringify({ audience_type: $('audSel').value,
                               audience_value: $('audVal').value }),
      });
      $('audCount').textContent = num(r.recipients) + ' shoppers';
      STATE.lastEstimate = r.recipients;
    } catch (e) { $('audCount').textContent = e.message; }
  });

  openEditor(async (sendIt) => {
    const f = collect();
    f.nudge_id = STATE.editing.id;
    f.est_recipients = STATE.lastEstimate || 0;
    f.send = !!sendIt;
    if (!f.title.trim() || !f.body.trim()) {
      toast('A nudge needs a title and a message.');
      return false;
    }
    if (sendIt && !confirm(
      `Send "${f.title}" to ${f.audience_type}${f.audience_value ? ' = ' + f.audience_value : ''}?`
      + `\n\nIt appears in the app for every matching shopper straight away.`)) return false;
    await api('/api/admin/nudges', { method: 'POST', body: JSON.stringify(f) });
    toast(sendIt ? 'Nudge is live for matching shoppers.' : 'Saved as a draft.');
    loadNudges(); loadOverview();
    return true;
  }, { extraLabel: sent ? null : 'Save & send', saveLabel: 'Save draft' });
}

/* ================================================================== editor */
function collect() {
  const out = {};
  all('#editBody [name]').forEach(i => {
    out[i.name] = i.type === 'checkbox' ? i.checked : i.value;
  });
  return out;
}

function openEditor(onSave, opts = {}) {
  const actions = el('#editModal .receipt-actions');
  actions.innerHTML = `
    <button class="ghost-btn" id="editCancel">Cancel</button>
    ${opts.extraLabel ? `<button class="ghost-btn" id="editExtra">${esc(opts.saveLabel || 'Save')}</button>
      <button class="primary-btn" id="editSave">${esc(opts.extraLabel)}</button>`
      : `<button class="primary-btn" id="editSave">Save</button>`}`;

  const run = async (btn, arg) => {
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Saving…';
    try {
      if (await onSave(arg)) closeEditor();
    } catch (e) { toast(e.message, 5000); } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
  };

  $('editCancel').addEventListener('click', closeEditor);
  if (opts.extraLabel) {
    $('editExtra').addEventListener('click', (e) => run(e.target, false));
    $('editSave').addEventListener('click', (e) => run(e.target, true));
  } else {
    $('editSave').addEventListener('click', (e) => run(e.target, false));
  }
  $('editModal').classList.remove('hidden');
}

function closeEditor() { $('editModal').classList.add('hidden'); }
$('closeEdit').addEventListener('click', closeEditor);
$('editModal').addEventListener('click', e => { if (e.target === $('editModal')) closeEditor(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeEditor(); });

/* ================================================================== SMS */
async function loadSms() {
  try {
    const r = await api('/api/admin/sms');
    STATE.sms = r;
    const f = $('smsForm');
    ['sender', 'entity_id', 'template_id', 'otp_template'].forEach(k => {
      f[k].value = r.settings[k] || '';
    });
    f.key.value = '';
    f.enabled.checked = r.settings.enabled;
    f.dev_mode.checked = r.dev_mode;
    $('keyHint').textContent = r.settings.key_set
      ? `Stored: ${r.settings.key}. Leave blank to keep it.`
      : 'No key stored yet.';
    renderSmsStatus(r);
    renderSmsLog(r.log);
  } catch (e) { $('smsMsg').textContent = e.message; }
}

function renderSmsStatus(r) {
  const live = r.configured && r.settings.enabled;
  $('smsStatus').innerHTML = live
    ? '<span class="pill on">Live — customers get real OTPs</span>'
    : `<span class="pill warn">Not sending</span>`;
  const bits = [];
  if (r.missing && r.missing.length) {
    bits.push(`<strong>Still needed:</strong> ${r.missing.map(esc).join(', ')}.`);
  } else if (!r.settings.enabled) {
    bits.push('Settings are complete. Send yourself a test message, then switch '
      + 'sending on.');
  }
  if (!r.settings.enabled) {
    bits.push('Sending is switched off, so no customer receives an SMS.');
  }
  if (r.dev_mode) {
    bits.push('Developer mode is on — a failed send shows the OTP on screen '
      + 'instead of blocking login. Turn it off before customers use this.');
  }
  (r.warnings || []).forEach(w => bits.push(esc(w)));
  $('smsAlert').className = bits.length ? 'notice' : 'hidden';
  $('smsAlert').innerHTML = bits.join('<br>');
  $('smsPreview').innerHTML = r.preview
    ? `<div class="sms-preview"><small>What the customer receives</small>
         <p>${esc(r.preview)}</p>
         <small>${r.preview.length} characters · ${Math.ceil(r.preview.length / 160) || 1} SMS</small>
       </div>` : '';
}

function renderSmsLog(log) {
  $('smsCounts').textContent = log.last_24h
    ? `${num(log.delivered_24h)} of ${num(log.last_24h)} accepted in the last 24h` : '';
  if (!log.sends.length) {
    $('smsLog').innerHTML = '<div class="empty">Nothing sent yet.</div>';
    return;
  }
  $('smsLog').innerHTML = log.sends.map(s => `
    <div class="row">
      <div class="row-main">
        <div class="row-ico">${s.ok ? '✅' : '⚠️'}</div>
        <div>
          <h4>+91 ${esc(s.mobile)}
            <span class="pill ${s.ok ? 'on' : 'warn'}">${s.ok ? 'Accepted' : 'Failed'}</span>
            <span class="pill off">${esc(s.purpose)}</span></h4>
          <small>${esc(s.at)}${s.uid ? ` · id ${esc(s.uid)}` : ''}${
            s.code ? ` · code ${s.code}` : ''}</small>
          <small>${esc(s.detail || '')}</small>
        </div>
      </div>
      <div class="row-side">
        ${s.uid ? `<button class="mini-btn" data-dlr="${esc(s.uid)}">Delivery status</button>` : ''}
      </div>
    </div>`).join('');
  all('[data-dlr]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      const r = await api(`/api/admin/sms/report/${encodeURIComponent(b.dataset.dlr)}`);
      toast(r.description || JSON.stringify(r.body).slice(0, 160), 6000);
    } catch (e) { toast(e.message, 6000); } finally { b.disabled = false; }
  }));
}

$('smsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const payload = {
    key: f.key.value.trim(),
    sender: f.sender.value.trim(),
    entity_id: f.entity_id.value.trim(),
    template_id: f.template_id.value.trim(),
    otp_template: f.otp_template.value,
    enabled: f.enabled.checked,
    dev_mode: f.dev_mode.checked,
  };
  if (payload.enabled && !payload.key && !(STATE.sms && STATE.sms.settings.key_set)) {
    $('smsMsg').textContent = 'Add the API key before switching sending on.';
    return;
  }
  const btn = el('button[type=submit]', f);
  btn.disabled = true;
  try {
    await api('/api/admin/sms', { method: 'POST', body: JSON.stringify(payload) });
    $('smsMsg').textContent = 'Saved.';
    toast('SMS settings saved.');
    await loadSms();
    setTimeout(() => { $('smsMsg').textContent = ''; }, 4000);
  } catch (e2) { $('smsMsg').textContent = e2.message; } finally { btn.disabled = false; }
});

$('verifyKey').addEventListener('click', async () => {
  const btn = $('verifyKey');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Checking…';
  try {
    const r = await api('/api/admin/sms/verify', { method: 'POST', body: '{}' });
    $('smsMsg').textContent = r.message;
    toast(r.message, 6000);
  } catch (e) { $('smsMsg').textContent = e.message; } finally {
    btn.disabled = false; btn.textContent = was;
  }
});

$('testMobile').addEventListener('input', (e) => {
  e.target.value = e.target.value.replace(/\D/g, '').slice(0, 10);
});

$('sendTest').addEventListener('click', async () => {
  const mobile = $('testMobile').value.trim();
  if (!/^[6-9]\d{9}$/.test(mobile)) { toast('Enter a valid 10-digit mobile.'); return; }
  if (!confirm(`Send a real test SMS to +91 ${mobile}? It costs one credit.`)) return;
  const btn = $('sendTest');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Sending…';
  try {
    const r = await api('/api/admin/sms/test', {
      method: 'POST', body: JSON.stringify({ mobile }),
    });
    toast(r.message, 7000);
    loadSms();
  } catch (e) { toast(e.message, 7000); } finally {
    btn.disabled = false; btn.textContent = was;
  }
});

/* ================================================================== settings */
$('passwordForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api('/api/admin/password', {
      method: 'POST',
      body: JSON.stringify({ current: f.current.value, new: f.new.value }),
    });
    $('passwordMsg').textContent = 'Password changed.';
    f.reset();
  } catch (e2) { $('passwordMsg').textContent = e2.message; }
});

/* ================================================================== tabs */
all('[data-atab]').forEach(tab => tab.addEventListener('click', () => {
  all('[data-atab]').forEach(t => t.classList.toggle('active', t === tab));
  all('.atab').forEach(p => p.classList.add('hidden'));
  $(`${tab.dataset.atab}ATab`).classList.remove('hidden');
}));

/* ================================================================== boot */
if (token()) enter();
