/* ClearLot — front of house.
   One crate at a time. Nothing here decides a colour on its own: the server
   returns the model's suggestion and, when it is not sure, a review ticket. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  lot: null, specs: [], samples: [], reel: [], idx: 0, view: 'intake', busy: false,
  lastResult: null, closed: null,
};

const api = async (path, opts = {}) => {
  const res = await fetch(path, opts);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = { raw: text }; }
  if (!res.ok) { const err = new Error((body && body.detail) || res.statusText); err.status = res.status; err.body = body; throw err; }
  return body;
};

const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

/* ------------------------------------------------------------------ routing */
function go(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.remove('on'));
  const el = view === 'ledger' ? $('#ledger-view') : $('#' + view);
  if (el) el.classList.add('on');
  $$('.nav a').forEach((a) => a.classList.toggle('on', a.dataset.view === view));
  if (view === 'ledger') loadDashboard();
  if (view === 'review') renderReview();
  if (view === 'receipt') renderReceipt();
  window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
}
$$('.nav a').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); go(a.dataset.view); }));

/* --------------------------------------------------------------- boot / lot */
async function boot() {
  state.specs = await api('/api/specs');
  try { state.samples = await api('/api/samples'); } catch (e) { state.samples = []; }
  const params = new URLSearchParams(location.search);
  // optional capture reel: ?reel=a.jpg,b.jpg — used by the recorded demo, and handy
  // for a live walkthrough with a fixed set of bottles.
  const reel = params.get('reel');
  if (reel) state.reel = reel.split(',').map((x) => x.trim()).filter(Boolean);
  const wanted = params.get('lot') || localStorage.getItem('clearlot.lot');
  let lot = null;
  if (wanted) { try { lot = await api('/api/lots/' + wanted); } catch (e) { lot = null; } }
  if (!lot) lot = await api('/api/lots', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spec_id: state.specs[0].id, operator: 'M. Seo · night shift' }),
  });
  state.lot = lot;
  localStorage.setItem('clearlot.lot', lot.lot.id);
  const sel = $('#spec-select');
  sel.innerHTML = state.specs.map((s) => `<option value="${s.id}" ${s.id === lot.spec.id ? 'selected' : ''}>${esc(s.receiver.split('·')[1] || s.name)}</option>`).join('');
  sel.onchange = async () => {
    const newLot = await api('/api/lots', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_id: sel.value, operator: 'M. Seo · night shift' }),
    });
    state.lot = newLot; localStorage.setItem('clearlot.lot', newLot.lot.id);
    history.replaceState(null, '', '?lot=' + newLot.lot.id);
    renderAll();
  };
  renderAll();
  go('intake');
}

/* ------------------------------------------------------------------ capture */
async function capture() {
  if (state.busy || !state.lot) return;
  if (state.lot.lot.status === 'closed') { toast('This crate is closed and cannot take more bottles.'); return; }
  state.busy = true;
  setBusy(true);
  $('#scan').classList.add('on');
  const t0 = Date.now();
  try {
    let res;
    const pool = state.reel.length ? state.reel : state.samples.map((x) => x.name);
    if (pool.length) {
      const s = { name: pool[state.idx % pool.length], url: '/samples/' + pool[state.idx % pool.length] };
      state.idx += 1;
      $('#frame').src = s.url; $('#frame').classList.add('show');
      $('#frame-bg').src = s.url; $('#frame-bg').classList.add('show');
      $('#viewport-empty').classList.add('empty-hide');
      $('#sample-hint').textContent = `sample photo replay · ${state.idx} of ${pool.length}`;
      res = await api(`/api/lots/${state.lot.lot.id}/capture`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sample: s.name }),
      });
    }
    const wait = Math.max(0, 780 - (Date.now() - t0));
    await new Promise((r) => setTimeout(r, wait));
    state.lastResult = res.result;
    state.lot = res.lot;
    renderAll({ animateSeq: res.seq });
    showStamp(res.result);
    if (res.result.review_required) toast('Sent to the review queue — a guess never closes a crate.');
  } catch (e) {
    toast('Capture failed: ' + e.message);
  } finally {
    $('#scan').classList.remove('on');
    state.busy = false; setBusy(false);
  }
}

function setBusy(b) {
  $('#btn-capture').disabled = b;
  $('#btn-capture').innerHTML = b ? '<span class="dot"></span>Reading glass…' : '<span class="dot"></span>Capture bottle';
}

async function upload(file) {
  if (!file || state.busy) return;
  state.busy = true; setBusy(true); $('#scan').classList.add('on');
  const t0 = Date.now();
  const obj = URL.createObjectURL(file);
  $('#frame').src = obj; $('#frame').classList.add('show');
  $('#frame-bg').src = obj; $('#frame-bg').classList.add('show');
  $('#viewport-empty').classList.add('empty-hide');
  try {
    const fd = new FormData(); fd.append('image', file);
    const res = await api(`/api/lots/${state.lot.lot.id}/bottles`, { method: 'POST', body: fd });
    await new Promise((r) => setTimeout(r, Math.max(0, 700 - (Date.now() - t0))));
    state.lastResult = res.result; state.lot = res.lot;
    $('#sample-hint').textContent = 'uploaded photo';
    renderAll({ animateSeq: res.seq }); showStamp(res.result);
  } catch (e) { toast('Capture failed: ' + e.message); }
  finally { $('#scan').classList.remove('on'); state.busy = false; setBusy(false); }
}

function showStamp(r) {
  const el = $('#stamp');
  el.hidden = false;
  el.classList.toggle('review', r.review_required);
  el.querySelector('.stamp-color').innerHTML = r.review_required
    ? `review <small>${esc(r.suggested_color)} ${pct(r.confidence)}</small>`
    : `${esc(r.suggested_color)} <small>${pct(r.confidence)}</small>`;
  el.querySelector('.stamp-conf').innerHTML =
    `<span class="bar"><i style="width:0"></i></span>` +
    `<span>${r.review_required ? 'human decision required' : 'auto-tagged'}</span>`;
  const bar = el.querySelector('.stamp-conf .bar i');
  requestAnimationFrame(() => {
    bar.style.transition = 'width .55s cubic-bezier(.2,.8,.2,1)';
    bar.style.width = pct(r.confidence);
  });
}

/* ------------------------------------------------------------------- render */
function renderAll(opts = {}) {
  const lot = state.lot;
  if (!lot) return;
  $('#lot-id').textContent = 'LOT ' + lot.lot.id.replace('lot_', '').slice(-6).toUpperCase();
  $('#lot-purity').textContent = pct(lot.purity, 1);
  const status = $('#lot-status');
  status.className = 'pill' + (lot.lot.status === 'closed' ? ' closed' : lot.eligible ? '' : ' blocked');
  status.textContent = lot.lot.status === 'closed' ? 'closed' : lot.eligible ? 'eligible' : 'blocked';
  renderSpec(); renderGrade(); renderGate(); renderLedgerStrip(opts.animateSeq); renderNav();
  $('#count').textContent = `${lot.counted} counted · ${lot.excluded} excluded · ${lot.bottles.length} total`;
  const btn = $('#btn-close');
  if (lot.lot.status === 'closed') {
    btn.className = 'btn close off'; btn.textContent = 'Crate closed — view receipt'; btn.disabled = false;
  } else {
    btn.className = 'btn close' + (lot.eligible ? '' : ' off');
    btn.textContent = lot.eligible ? 'Close crate & issue receipt' : 'Blocked — see the gate';
    btn.disabled = false;
  }
  renderReview(); renderReceipt();
}

function renderNav() {
  const n = state.lot.bottles.filter((b) => b.review_state === 'pending').length;
  const el = $('#nav-review'); el.textContent = n; el.classList.toggle('hot', n > 0);
}

function renderSpec() {
  const s = state.lot.spec;
  $('#spec-name').textContent = s.name;
  $('#spec-receiver').textContent = `${s.receiver} · spec v${s.version}`;
  $('#spec-target').textContent = s.target_color;
  $('#spec-target').style.color = `var(--${s.target_color})`;
  $('#spec-purity').textContent = pct(s.min_purity);
  $('#spec-min').textContent = s.min_bottles + ' pcs';
  $('#spec-note').textContent = s.note || '';
}

function renderGrade() {
  const lot = state.lot;
  const p = lot.purity;
  $('#purity').innerHTML = `${(p * 100).toFixed(1)}<small>%</small>`;
  $('#threshold').innerHTML = `receiver floor ${pct(lot.spec.min_purity)}<br>${lot.counted} counted · ${lot.spec.min_bottles} needed`;
  const g = $('#gauge');
  g.style.width = pct(p);
  g.classList.toggle('low', p < lot.spec.min_purity);
  $('#gauge-mark').style.left = `calc(${pct(lot.spec.min_purity)} - 1px)`;
  const c = lot.composition;
  const tot = Math.max(1, lot.counted);
  ['flint', 'green', 'amber'].forEach((k) => {
    const seg = $$('.seg.' + k)[0];
    seg.style.width = `${(c[k] / tot) * 100}%`;
  });
  $('#legend').innerHTML = `${['flint', 'green', 'amber'].map((k) => `<span><i class="${k}"></i>${k} ${c[k]}</span>`).join('')}
    <span><i class="exclude"></i>excluded ${lot.excluded}</span>`;
}

function renderGate() {
  const box = $('#blockers');
  const hard = state.lot.blockers.filter((b) => !b.code.startsWith('check:'));
  const checks = state.lot.spec.checklist;
  const answered = {};
  (state.lot.checks || []).forEach((c) => { answered[c.item_key] = c.answer; });
  const allGood = hard.length === 0 && checks.every((c) => answered[c.key] === 'yes');
  let html = hard.map((b) => `<div class="bl"><span class="ic"></span><span><b>${esc(b.message)}</b><span class="d">${esc(b.detail)}</span></span></div>`).join('');
  if (!hard.length) html += `<div class="bl ok"><span class="ic"></span><span><b>Every bottle has a decision with a source</b><span class="d">Nothing is left to a guess.</span></span></div>`;
  html += `<div class="checklist">${checks.map((c) => `<label class="chk ${answered[c.key] === 'yes' ? 'checked' : ''}">
      <input type="checkbox" data-key="${c.key}" ${answered[c.key] === 'yes' ? 'checked' : ''}>
      <span>${esc(c.label)}</span></label>`).join('')}</div>`;
  box.innerHTML = html;
  $$('.chk input', box).forEach((inp) => {
    inp.addEventListener('change', async () => {
      const items = {}; items[inp.dataset.key] = inp.checked;
      try {
        state.lot = await api(`/api/lots/${state.lot.lot.id}/checks`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items, actor: 'M. Seo · night shift' }),
        });
        renderAll();
      } catch (e) { toast('Could not save the checklist'); }
    });
  });
}

function renderLedgerStrip(animateSeq) {
  const box = $('#ledger');
  const bottles = state.lot.bottles;
  if (!bottles.length) { box.innerHTML = '<div class="ledger-empty">No bottles yet — the first capture writes the first row.</div>'; return; }
  box.innerHTML = bottles.slice().reverse().map((b) => {
    const colour = b.final_color || 'pending';
    const cls = b.review_state === 'pending' ? 'pending' : (colour === 'pending' ? 'exclude' : colour);
    return `<div class="lg-item ${cls}" title="#${b.seq} · ${esc(b.suggested_color)} ${pct(b.confidence)} · ${b.review_state}">
      <img src="/media/${b.image_path}" alt="">
      <div class="lg-meta"><span class="lg-seq">${String(b.seq).padStart(2, '0')}</span><span class="lg-dot"></span></div>
    </div>`;
  }).join('');
  if (animateSeq) { const first = box.firstElementChild; if (first) first.style.animationDelay = '0ms'; }
  box.scrollLeft = 0;
}

/* ------------------------------------------------------------------- review */
let modelInfo = null;
async function renderModelCard() {
  if (!modelInfo) {
    try { modelInfo = await api('/api/model'); } catch (e) { return; }
  }
  const m = modelInfo.metrics.find((x) => x.split === 'real-photos-holdout') || modelInfo.metrics[0];
  const box = $('#model-card');
  if (!box) return;
  box.innerHTML = `<h4>why a queue exists</h4>
    <p>A linear colour model reads one frame in a few milliseconds. It is good at glass that is
    plainly flint, green or amber, and it is not a material detector: ceramics, heat-resistant glass
    and a bottle photographed against a warm wall are exactly where it should refuse.</p>
    <p>So the model never closes a crate on its own. Below a ${(modelInfo.margin_min * 100).toFixed(0)}% top-two margin,
    or on a frame it cannot read, the bottle is parked here until a person decides.</p>
    <div class="mrows">
      <div class="mrow"><span>model</span><b>v${esc(modelInfo.version)} · logistic regression</b></div>
      <div class="mrow"><span>held-out real photos</span><b>${m.n} · macro F1 ${m.macro_f1.toFixed(3)}</b></div>
      <div class="mrow"><span>trained on</span><b>${modelInfo.train.synthetic_frames} synthetic frames</b></div>
      <div class="mrow"><span>colour-threshold baseline</span><b>macro F1 0.331</b></div>
    </div>`;
}

function renderReview() {
  if (!state.lot) return;
  const pending = state.lot.bottles.filter((b) => b.review_state === 'pending');
  $('#review-cards').innerHTML = pending.map((b) => {
    const reasons = JSON.parse(b.review_reason || '[]');
    return `<div class="card">
      <img src="/media/${b.image_path}" alt="">
      <div>
        <div class="k">bottle ${String(b.seq).padStart(2, '0')} · waiting</div>
        <div class="sug" style="margin-top:7px">${esc(b.suggested_color)}</div>
        <div class="conf">model score ${pct(b.confidence)} — not calibrated</div>
        <div class="why">${reasons.length ? esc(reasons[0]) : 'below the auto-tag margin'}</div>
        <div class="prob">${['flint', 'green', 'amber'].map((c) => `<div class="pr ${c}"><span>${c}</span>
          <span class="t"><i style="width:${((JSON.parse(b.probabilities)[c] || 0) * 100).toFixed(0)}%"></i></span>
          <span>${pct(JSON.parse(b.probabilities)[c] || 0)}</span></div>`).join('')}</div>
        <div class="choose">
          <button data-c="flint" class="flint">flint</button>
          <button data-c="green" class="green">green</button>
          <button data-c="amber" class="amber">amber</button>
          <button data-c="exclude" class="ex">rehome</button>
        </div>
      </div></div>`;
  }).join('');
  $('#review-empty').hidden = pending.length > 0;
  renderModelCard();
  $$('#review-cards .choose button').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      const card = btn.closest('.card');
      const seq = Number(card.querySelector('.k').textContent.match(/\d+/)[0]);
      const bottle = state.lot.bottles.find((b) => b.seq === seq);
      const reason = btn.dataset.c === 'exclude' ? 'not this crate — rehomed' : 'confirmed by operator';
      try {
        state.lot = await api(`/api/bottles/${bottle.id}/review`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ decision: btn.dataset.c, reason, actor: 'M. Seo · night shift' }),
        });
        renderAll();
        toast(`Bottle ${String(seq).padStart(2, '0')} → ${btn.dataset.c} (human)`);
        if (!state.lot.bottles.some((b) => b.review_state === 'pending')) go('intake');
      } catch (err) { toast('Could not record decision: ' + err.message); }
    });
  });
}

/* ------------------------------------------------------------------ receipt */
async function closeLot() {
  if (!state.lot) return;
  if (state.lot.lot.status === 'closed') { go('receipt'); return; }
  try {
    const res = await api(`/api/lots/${state.lot.lot.id}/close`, { method: 'POST' });
    state.lot = { ...res };
    state.closed = res.receipt;
    renderAll(); go('receipt');
    toast('Receipt issued · hash anchored to the previous receipt');
  } catch (e) {
    if (e.status === 409) {
      state.lot = e.body.lot || state.lot;
      renderAll();
      toast(e.body.blockers && e.body.blockers.length ? e.body.blockers[0].message : 'Crate is still blocked');
    } else { toast('Could not close: ' + e.message); }
  }
}

let receiptData = null;
async function renderReceipt() {
  const box = $('#receipt-body');
  if (!state.lot) return;
  const rid = state.lot.receipt && state.lot.receipt.id;
  if (!rid) {
    box.innerHTML = `<div class="rcpt"><div class="rcpt-main">
      <h2>No receipt yet</h2>
      <div class="sub2">A receipt is only issued from a crate where every bottle has a decision and the receiver's floor is met.</div>
      <div class="kv">
        <div><div class="k">counted</div><div class="v">${state.lot.counted}</div></div>
        <div><div class="k">purity</div><div class="v">${pct(state.lot.purity, 1)}</div></div>
        <div><div class="k">needs</div><div class="v">${state.lot.spec.min_bottles}</div></div>
        <div><div class="k">open blockers</div><div class="v">${state.lot.blockers.length}</div></div>
      </div>
      <button class="btn primary" id="back-intake">Back to the capture station</button>
      </div></div>`;
    const back = $('#back-intake'); if (back) back.onclick = () => go('intake');
    return;
  }
  if (!receiptData || receiptData.id !== rid) receiptData = await api('/api/receipts/' + rid);
  const r = receiptData; const b = r.body; const acc = r.acceptances || [];
  const tiers = ['projected', 'recorded', 'verified'];
  const done = { projected: true };
  acc.forEach((a) => { done[a.tier] = true; });
  box.innerHTML = `<div class="rcpt">
    <div class="rcpt-main">
      <h2>${esc(b.spec.name)} → ${esc(b.spec.receiver)}</h2>
      <div class="sub2">receipt ${esc(r.id)} · issued ${esc(b.issued_at)} · operator ${esc(b.lot.operator)} · spec v${b.spec.version}</div>
      <div class="kv">
        <div><div class="k">bottles counted</div><div class="v">${b.result.bottles_counted}</div></div>
        <div><div class="k">purity · ${b.spec.target_color}</div><div class="v">${pct(b.result.target_purity, 1)}</div></div>
        <div><div class="k">excluded</div><div class="v">${b.result.bottles_excluded}</div></div>
        <div><div class="k">meets spec</div><div class="v">${b.result.meets_spec ? 'yes' : 'no'}</div></div>
      </div>
      <div class="k" style="margin-bottom:6px">bottle ledger</div>
      <table><tr><th>#</th><th>decision</th><th>source</th><th>model score</th><th>image sha256</th></tr>
      ${b.bottles.map((x) => `<tr><td>${String(x.seq).padStart(2, '0')}</td>
        <td><span class="chip ${x.decision.final_color || 'exclude'}">${x.decision.final_color || 'excluded'}</span></td>
        <td>${x.decision.source}</td><td>${x.model.confidence.toFixed(2)}</td><td>${x.image_sha256.slice(0, 18)}…</td></tr>`).join('')}
      </table>
      <div class="k" style="margin:18px 0 6px">handover checklist</div>
      <div class="checklist">${b.checks.map((c) => `<div class="chk checked"><span style="color:var(--mint)">✓</span><span>${esc(c.key)} — ${esc(c.actor)}</span></div>`).join('')}</div>
    </div>
    <div class="rcpt-side">
      <div class="integrity">
        <div class="k" style="margin-bottom:8px">receipt integrity</div>
        <div class="row"><span>body sha256</span><b class="${r.integrity.hash_matches ? '' : 'no'}">${r.integrity.hash_matches ? 'matches' : 'changed'}</b></div>
        <div class="row"><span>HMAC (key off-db)</span><b class="${r.integrity.hmac_matches ? '' : 'no'}">${r.integrity.hmac_matches ? 'valid' : 'invalid'}</b></div>
        <div class="row"><span>chain · seq ${r.seq}</span><b class="${r.integrity.chain_linked ? '' : 'no'}">${r.integrity.chain_linked ? 'linked' : 'broken'}</b></div>
        <div class="hashline">${r.sha256}</div>
      </div>
      <div class="qrbox"><img src="/r/${r.id}/qr.png" alt="QR to the public receipt"></div>
      <a class="btn ghost" href="/r/${r.id}" target="_blank" rel="noopener">Open the receiver's page ↗</a>
      <div class="k">evidence tier</div>
      <div class="tiers">${tiers.map((t) => `<div class="tier ${done[t] ? 'done' : ''}">${t}</div>`).join('')}</div>
      <div class="acts">
        <button class="btn ghost" id="acc-recorded">Mark operator-recorded</button>
        <button class="btn ghost" id="acc-verified">Attach receiver reference</button>
      </div>
    </div></div>`;
  const post = async (tier, evidence) => {
    try {
      await api(`/api/receipts/${r.id}/acceptances`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier, actor: tier === 'verified' ? 'Busan Glass Co-op' : 'M. Seo · night shift', evidence }),
      });
      receiptData = await api('/api/receipts/' + r.id);
      renderReceipt(); toast(tier === 'verified' ? 'Receiver reference attached' : 'Recorded as received by the operator');
    } catch (e) { toast('Could not record: ' + e.message); }
  };
  const rBtn = $('#acc-recorded'); if (rBtn) rBtn.onclick = () => post('recorded', '');
  const vBtn = $('#acc-verified'); if (vBtn) vBtn.onclick = () => {
    const ev = prompt('Receiver weighbridge / ticket reference:', 'WC-2411-0038');
    if (ev) post('verified', ev);
  };
}

/* ---------------------------------------------------------------- dashboard */
async function loadDashboard() {
  const d = await api('/api/dashboard');
  const t = d.totals;
  const kg = (t.counted * d.assumptions.kg_per_bottle);
  const co2 = (kg / 1000) * d.assumptions.co2e_kg_per_tonne_cullet;
  $('#dash-totals').innerHTML = `
    <div class="tot"><div class="k">crates</div><div class="v">${t.lots}<small>/${t.closed} closed</small></div>
      <div class="n">${t.bottles} bottles graded in this station</div></div>
    <div class="tot"><div class="k">receiver-eligible glass</div><div class="v">${t.eligible_bottles}<small>bottles</small></div>
      <div class="n">${t.counted ? Math.round((t.eligible_bottles / t.counted) * 100) : 0}% of everything counted</div></div>
    <div class="tot"><div class="k">waiting on a human</div><div class="v">${t.pending}</div>
      <div class="n">never auto-tagged into a crate</div></div>
    <div class="tot"><div class="k">modelled cullet</div><div class="v">${kg.toFixed(1)}<small>kg</small></div>
      <div class="n">≈ ${co2.toFixed(1)} kg CO₂e avoided vs virgin batch</div></div>`;
  const tierCount = (t) => d.lots.filter((l) => l.tier === t).length;
  $('#tierstrip').innerHTML = `
    <div class="tiercard p"><div class="th"><span class="k">projected</span><span class="tv">${tierCount('projected')}</span></div>
      <div class="td">This app graded the crate against the receiver's spec. Nothing outside has confirmed it yet.</div></div>
    <div class="tiercard r"><div class="th"><span class="k">recorded</span><span class="tv">${tierCount('recorded')}</span></div>
      <div class="td">The operator logged that the crate was handed over. Still our word, now with a timestamp.</div></div>
    <div class="tiercard v"><div class="th"><span class="k">verified</span><span class="tv">${tierCount('verified')}</span></div>
      <div class="td">A receiver reference exists against the receipt — the only tier that is someone else's word.</div></div>`;
  $('#dash-lots').innerHTML = d.lots.map((l) => `<div class="row">
      <div><div class="nm">${esc(l.spec)}</div><div class="rc">${esc(l.receiver)} · lot ${esc(l.id.replace('lot_', '#'))}</div></div>
      <div class="cell">${l.counted} btl<Br><b>${pct(l.purity, 1)}</b></div>
      <div class="cell"><span class="chip ${l.target}">${l.target}</span></div>
      <div class="cell">${l.status === 'closed' ? 'closed' : l.eligible ? 'eligible' : 'blocked'}</div>
      <div class="cell"><span class="tier ${l.tier === 'none' ? '' : 'done'}" style="display:inline-block;padding:6px 8px">${l.tier}</span></div>
    </div>`).join('') || '<div class="empty-state"><div class="kh">no crates yet</div><p>Capture a bottle to open the first crate.</p></div>';
  $('#assume').innerHTML = `<b>How these numbers are derived.</b> Cullet mass assumes ${d.assumptions.kg_per_bottle} kg per bottle
    (a 500 ml container-glass bottle) and CO₂e assumes ${d.assumptions.co2e_kg_per_tonne_cullet} kg avoided per tonne of glass returned to remelt —
    ${esc(d.assumptions.source)}. The eligible figure counts only bottles a receiver's spec was actually checked against;
    it is not a claim about total waste diverted.`;
}

/* -------------------------------------------------------------------- wiring */
$('#btn-capture').addEventListener('click', capture);
$('#file').addEventListener('change', (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ''; });
$('#btn-close').addEventListener('click', closeLot);
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'c' || e.key === ' ') { e.preventDefault(); capture(); }
});
boot();
