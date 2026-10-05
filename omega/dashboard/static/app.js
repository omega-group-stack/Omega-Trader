/* Omega-Trader dashboard — zero dependencies, plain canvas charts. */
'use strict';

const COLORS = {
  green: '#22c98a', red: '#ff5370', amber: '#ffb648',
  blue: '#4da3ff', purple: '#a07dff', muted: '#8494b0',
  grid: '#1b2435', text: '#e6ecf7',
};

const state = {
  snapshot: null,
  candles: [],
  equity: [],
  symbol: null,
  refreshMs: 2000,
  timer: null,
  entryThreshold: 0.28,
};

/* ------------------------------------------------------------------ utils */
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) =>
  (v === null || v === undefined || Number.isNaN(v)) ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const signClass = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
const timeStr = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toISOString().slice(5, 16).replace('T', ' ');
};

function toast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.style.borderColor = bad ? '#5c2030' : '#2b3a5c';
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2800);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function control(action, extra = {}) {
  try {
    const out = await api('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...extra }),
    });
    toast(out.message || 'done');
    refresh();
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

/* ------------------------------------------------------------------- data */
async function refresh() {
  try {
    const snap = await api('/api/state');
    state.snapshot = snap;
    state.refreshMs = snap.refresh_ms || 2000;
    if (!state.symbol && snap.symbols && snap.symbols.length) state.symbol = snap.symbols[0];
    setConnection(true, snap.error);
    render(snap);

    const [candles, equity] = await Promise.all([
      api(`/api/candles?symbol=${encodeURIComponent(state.symbol || '')}&limit=180`).catch(() => null),
      api('/api/equity').catch(() => null),
    ]);
    if (candles) { state.candles = candles.candles || []; $('chart-symbol').textContent = candles.symbol; }
    if (equity) state.equity = equity.points || [];
    drawPrice();
    drawScore();
    drawEquity();
  } catch (err) {
    setConnection(false, String(err.message || err));
  }
}

function setConnection(ok, error) {
  const el = $('conn');
  el.className = 'conn ' + (ok ? (error ? 'err' : 'on') : 'err');
  $('conn-text').textContent = ok ? (error ? 'degraded' : 'connected') : 'offline';
  if (error) el.title = error;
}

/* ----------------------------------------------------------------- render */
function render(s) {
  if (!s.ready) { $('subtitle').textContent = 'waiting for the trader to start…'; return; }

  const acct = s.account, risk = s.risk, perf = s.performance;

  $('subtitle').textContent =
    `${s.symbols.join(', ')} · ${s.timeframe} (bias ${s.htf}) · feed ${s.feed} · broker ${s.broker}`;
  const badge = $('mode-badge');
  badge.textContent = s.mode;
  badge.className = 'badge ' + (s.mode === 'live' ? 'live' : 'paper');
  $('foot-mode').textContent = `${s.mode} mode · ${s.bars_processed} bars processed`;

  const runBtn = $('btn-run');
  runBtn.textContent = s.running ? 'Stop' : 'Start';
  runBtn.classList.toggle('stop', !!s.running);

  const haltBtn = $('btn-halt');
  haltBtn.textContent = risk.halted ? 'Resume' : 'Halt';
  haltBtn.classList.toggle('resume', !!risk.halted);

  const riskInput = $('risk-input');
  if (document.activeElement !== riskInput) riskInput.value = risk.risk_per_trade_pct;

  renderKpis(acct, risk, perf);
  renderSignal(s.signals[state.symbol] || Object.values(s.signals)[0]);
  renderRisk(risk, acct);
  renderPositions(s.positions, acct);
  renderTrades(s.trades);
  renderEvents(s.events);
}

function renderKpis(acct, risk, perf) {
  const pnl = acct.equity - (perf.initial_balance || acct.balance);
  const items = [
    { label: 'Equity', value: fmt(acct.equity), sub: acct.currency },
    { label: 'Balance', value: fmt(acct.balance), sub: `free margin ${fmt(acct.free_margin, 0)}` },
    { label: 'Net P/L', value: (pnl >= 0 ? '+' : '') + fmt(pnl), cls: signClass(pnl),
      sub: `${perf.return_pct >= 0 ? '+' : ''}${fmt(perf.return_pct)}%` },
    { label: 'Day P/L', value: `${risk.day_pnl_pct >= 0 ? '+' : ''}${fmt(risk.day_pnl_pct)}%`,
      cls: signClass(risk.day_pnl_pct), sub: `limit −${fmt(risk.max_daily_loss_pct)}%` },
    { label: 'Drawdown', value: `${fmt(risk.drawdown_pct)}%`,
      cls: risk.drawdown_pct > risk.max_drawdown_pct * 0.6 ? 'warnc' : '',
      sub: `kill at ${fmt(risk.max_drawdown_pct)}%` },
    { label: 'Open risk', value: `${fmt(risk.open_risk_pct)}%`,
      sub: `${fmt(risk.risk_headroom_pct)}% free` },
    { label: 'Win rate', value: `${fmt(perf.win_rate_pct, 1)}%`,
      sub: `${perf.wins}W / ${perf.losses}L` },
    { label: 'Profit factor', value: fmt(perf.profit_factor),
      cls: perf.profit_factor >= 1 ? 'pos' : (perf.trades ? 'neg' : ''),
      sub: `${perf.trades} trades · ${fmt(perf.expectancy_r, 2)}R avg` },
  ];
  $('kpis').innerHTML = items.map((i) => `
    <div class="kpi">
      <span>${i.label}</span>
      <b class="${i.cls || ''}">${i.value}</b>
      <small>${i.sub || ''}</small>
    </div>`).join('');
}

function renderSignal(sig) {
  if (!sig) return;
  const dirColor = sig.direction === 'LONG' ? COLORS.green
    : sig.direction === 'SHORT' ? COLORS.red : COLORS.muted;

  $('score-value').textContent = (sig.score >= 0 ? '+' : '') + fmt(sig.score);
  $('score-value').style.color = dirColor;
  $('score-dir').textContent = sig.direction;
  $('score-dir').style.color = dirColor;

  const chip = $('regime-chip');
  chip.textContent = sig.regime.replace('_', ' ');
  chip.className = 'chip ' + (sig.regime === 'TREND_UP' ? 'up'
    : sig.regime === 'TREND_DOWN' ? 'down'
    : sig.regime === 'VOLATILE' ? 'warn' : '');

  const pct = Math.min(Math.abs(sig.score), 1) * 50;
  const fill = $('score-fill');
  fill.style.background = dirColor;
  if (sig.score >= 0) { fill.style.left = '50%'; fill.style.right = `${50 - pct}%`; }
  else { fill.style.right = '50%'; fill.style.left = `${50 - pct}%`; }

  const th = state.entryThreshold * 50;
  $('th-pos').style.left = `${50 + th}%`;
  $('th-neg').style.left = `${50 - th}%`;

  $('confidence').textContent = fmt(sig.confidence);
  const agree = sig.components.filter((c) => Math.sign(c.score) === Math.sign(sig.score) && Math.abs(c.score) > 0.08).length;
  $('agreement').textContent = `${agree}/6`;

  $('blocks').innerHTML = sig.components.map((c) => {
    const w = Math.min(Math.abs(c.score), 1) * 50;
    const color = c.score > 0 ? COLORS.green : c.score < 0 ? COLORS.red : COLORS.muted;
    const side = c.score >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
    return `
      <div class="block">
        <div class="block-top">
          <b>${c.name.replace('_', ' ')}</b>
          <i>${c.score >= 0 ? '+' : ''}${fmt(c.score)} × w${fmt(c.weight)}</i>
        </div>
        <div class="block-bar"><div class="mid"></div>
          <div class="fill" style="${side};background:${color}"></div></div>
        <div class="block-detail">${c.detail || ''}</div>
      </div>`;
  }).join('');

  const reasons = (sig.reasons || []).map((r) => `<li>${r}</li>`).join('');
  const vetoes = (sig.vetoes || []).map((v) => `<li class="veto">⛔ ${v}</li>`).join('');
  $('verdict').innerHTML = `
    <b>${sig.vetoes.length ? 'Blocked' : sig.direction === 'FLAT' ? 'Standing aside' : 'Ready to trade'}</b>
    <ul>${reasons}${vetoes}</ul>`;
}

function renderRisk(risk, acct) {
  const chip = $('risk-state');
  if (risk.halted) { chip.textContent = 'HALTED'; chip.className = 'chip down'; }
  else if (risk.cooldown_until) { chip.textContent = 'COOL-DOWN'; chip.className = 'chip warn'; }
  else { chip.textContent = 'ACTIVE'; chip.className = 'chip up'; }

  const rows = [
    { label: 'Per-trade risk', value: `${fmt(risk.risk_per_trade_pct)}%`, pct: Math.min(risk.risk_per_trade_pct / 5 * 100, 100), color: COLORS.blue },
    { label: 'Open portfolio risk', value: `${fmt(risk.open_risk_pct)}% of ${fmt(risk.open_risk_pct + risk.risk_headroom_pct)}%`,
      pct: (risk.open_risk_pct / Math.max(risk.open_risk_pct + risk.risk_headroom_pct, 0.01)) * 100, color: COLORS.purple },
    { label: 'Daily loss used', value: `${fmt(Math.max(-risk.day_pnl_pct, 0))}% of ${fmt(risk.max_daily_loss_pct)}%`,
      pct: Math.min(Math.max(-risk.day_pnl_pct, 0) / Math.max(risk.max_daily_loss_pct, .01) * 100, 100), color: COLORS.amber },
    { label: 'Drawdown vs kill-switch', value: `${fmt(risk.drawdown_pct)}% of ${fmt(risk.max_drawdown_pct)}%`,
      pct: Math.min(risk.drawdown_pct / Math.max(risk.max_drawdown_pct, .01) * 100, 100), color: COLORS.red },
  ];
  $('risk-bars').innerHTML = rows.map((r) => `
    <div class="risk-row">
      <div class="lbl"><span>${r.label}</span><b>${r.value}</b></div>
      <div class="risk-track"><div style="width:${Math.max(r.pct, 0)}%;background:${r.color}"></div></div>
    </div>`).join('') + `
    <div class="muted" style="margin-top:10px">
      Losing streak: ${risk.consecutive_losses} · trades today: ${risk.trades_today}
      ${risk.halt_reason ? `<br><span style="color:${COLORS.red}">${risk.halt_reason}</span>` : ''}
    </div>`;
}

function renderPositions(positions, acct) {
  $('pos-count').textContent = `${positions.length} open`;
  const body = document.querySelector('#positions tbody');
  if (!positions.length) {
    body.innerHTML = '<tr><td colspan="11" class="empty">No open positions</td></tr>';
    return;
  }
  body.innerHTML = positions.map((p) => `
    <tr>
      <td>#${p.ticket}</td>
      <td><b>${p.symbol}</b></td>
      <td><span class="tag ${p.side.toLowerCase()}">${p.side}</span></td>
      <td>${fmt(p.lots)}</td>
      <td>${p.entry_price}</td>
      <td>${p.price}</td>
      <td>${p.stop_loss ?? '—'}</td>
      <td>${p.take_profit ?? '—'}</td>
      <td>${fmt(p.risk_pips, 1)} pips</td>
      <td class="${signClass(p.r_multiple)}">${p.r_multiple >= 0 ? '+' : ''}${fmt(p.r_multiple)}R</td>
      <td class="${signClass(p.pnl)}"><b>${p.pnl >= 0 ? '+' : ''}${fmt(p.pnl)}</b></td>
    </tr>`).join('');
}

function renderTrades(trades) {
  $('trade-count').textContent = `${trades.length} shown`;
  const body = document.querySelector('#trades tbody');
  if (!trades.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty">No closed trades yet</td></tr>';
    return;
  }
  body.innerHTML = trades.slice().reverse().map((t) => `
    <tr>
      <td>${timeStr(t.close_time)}</td>
      <td><b>${t.symbol}</b></td>
      <td><span class="tag ${t.side.toLowerCase()}">${t.side}</span></td>
      <td>${fmt(t.lots)}</td>
      <td>${t.entry_price}</td>
      <td>${t.exit_price}</td>
      <td><span class="reason">${t.reason.replace(/_/g, ' ').toLowerCase()}</span></td>
      <td class="${signClass(t.r_multiple)}">${t.r_multiple >= 0 ? '+' : ''}${fmt(t.r_multiple)}R</td>
      <td class="${signClass(t.pnl)}"><b>${t.pnl >= 0 ? '+' : ''}${fmt(t.pnl)}</b></td>
    </tr>`).join('');
}

function renderEvents(events) {
  $('events').innerHTML = events.slice().reverse().map((e) => `
    <li>
      <time>${timeStr(e.time)}</time>
      <span class="k ${e.kind}">${e.kind}</span>
      <span>${e.message}</span>
    </li>`).join('') || '<li class="empty">No activity yet</li>';
}

/* ----------------------------------------------------------------- charts */
function prepCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const h = canvas.getAttribute('height') * 1;
  canvas.width = rect.width * dpr;
  canvas.height = h * dpr;
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, h);
  return { ctx, w: rect.width, h };
}

function drawPrice() {
  const { ctx, w, h } = prepCanvas($('price-chart'));
  const data = state.candles;
  if (!data.length) { emptyChart(ctx, w, h, 'waiting for candles…'); return; }

  const padL = 6, padR = 58, padT = 10, padB = 18;
  const lows = data.map((d) => d.low), highs = data.map((d) => d.high);
  let min = Math.min(...lows), max = Math.max(...highs);
  const span = (max - min) || 1e-5;
  min -= span * 0.08; max += span * 0.08;

  const x = (i) => padL + (i / Math.max(data.length - 1, 1)) * (w - padL - padR);
  const y = (p) => padT + (1 - (p - min) / (max - min)) * (h - padT - padB);

  // grid + right-hand price axis
  ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.muted;
  ctx.font = '10px ui-monospace, monospace'; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const price = min + (max - min) * (i / 4);
    const yy = Math.round(y(price)) + 0.5;
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    ctx.fillText(price.toFixed(data[0].close > 50 ? 2 : 5), w - padR + 6, yy + 3);
  }

  // candles
  const bw = Math.max(1.5, (w - padL - padR) / data.length * 0.62);
  data.forEach((d, i) => {
    const up = d.close >= d.open;
    ctx.strokeStyle = ctx.fillStyle = up ? COLORS.green : COLORS.red;
    ctx.beginPath();
    ctx.moveTo(x(i), y(d.high)); ctx.lineTo(x(i), y(d.low)); ctx.stroke();
    const top = y(Math.max(d.open, d.close));
    const bh = Math.max(1, Math.abs(y(d.open) - y(d.close)));
    ctx.fillRect(x(i) - bw / 2, top, bw, bh);
  });

  // overlays
  const overlay = (key, color, dash) => {
    if (!data.some((d) => d[key] != null)) return;
    ctx.strokeStyle = color; ctx.lineWidth = 1.4;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    let started = false;
    data.forEach((d, i) => {
      if (d[key] == null) { started = false; return; }
      if (!started) { ctx.moveTo(x(i), y(d[key])); started = true; }
      else ctx.lineTo(x(i), y(d[key]));
    });
    ctx.stroke(); ctx.setLineDash([]);
  };
  overlay('ema_fast', COLORS.blue);
  overlay('ema_slow', COLORS.amber);
  overlay('ema_base', COLORS.muted, [4, 4]);
  overlay('supertrend', COLORS.purple, [2, 3]);

  // open-position markers
  const snap = state.snapshot;
  if (snap && snap.positions) {
    snap.positions.filter((p) => p.symbol === state.symbol).forEach((p) => {
      line(ctx, padL, w - padR, y(p.entry_price), '#ffffff55', [5, 4], `${p.side} ${p.lots}`);
      if (p.stop_loss) line(ctx, padL, w - padR, y(p.stop_loss), COLORS.red + '99', [3, 3], 'SL');
      if (p.take_profit) line(ctx, padL, w - padR, y(p.take_profit), COLORS.green + '99', [3, 3], 'TP');
    });
  }
}

function line(ctx, x1, x2, yy, color, dash, label) {
  ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash);
  ctx.beginPath(); ctx.moveTo(x1, yy); ctx.lineTo(x2, yy); ctx.stroke();
  ctx.setLineDash([]);
  if (label) { ctx.fillStyle = color; ctx.font = '9px ui-monospace, monospace';
    ctx.fillText(label, x1 + 3, yy - 3); }
}

function drawScore() {
  const { ctx, w, h } = prepCanvas($('score-chart'));
  const data = state.candles.filter((d) => d.score != null);
  if (!data.length) { emptyChart(ctx, w, h, ''); return; }

  const padL = 6, padR = 58, padT = 8, padB = 10;
  const x = (i) => padL + (i / Math.max(data.length - 1, 1)) * (w - padL - padR);
  const y = (v) => padT + (1 - (v + 1) / 2) * (h - padT - padB);

  // zero line + entry thresholds
  line(ctx, padL, w - padR, y(0), COLORS.grid, []);
  line(ctx, padL, w - padR, y(state.entryThreshold), '#22c98a44', [3, 3]);
  line(ctx, padL, w - padR, y(-state.entryThreshold), '#ff537044', [3, 3]);

  const bw = Math.max(1, (w - padL - padR) / data.length * 0.7);
  data.forEach((d, i) => {
    ctx.fillStyle = d.score > 0 ? COLORS.green + 'cc' : COLORS.red + 'cc';
    const top = d.score > 0 ? y(d.score) : y(0);
    ctx.fillRect(x(i) - bw / 2, top, bw, Math.abs(y(d.score) - y(0)));
  });

  ctx.fillStyle = COLORS.muted; ctx.font = '9px ui-monospace, monospace';
  ctx.fillText('ensemble score', w - padR + 6, padT + 8);
}

function drawEquity() {
  const { ctx, w, h } = prepCanvas($('equity-chart'));
  const pts = state.equity;
  if (pts.length < 2) { emptyChart(ctx, w, h, 'equity curve builds as trades close'); return; }

  const padL = 6, padR = 60, padT = 10, padB = 16;
  const values = pts.map((p) => p.equity);
  let min = Math.min(...values), max = Math.max(...values);
  const span = (max - min) || 1;
  min -= span * 0.1; max += span * 0.1;

  const x = (i) => padL + (i / (pts.length - 1)) * (w - padL - padR);
  const y = (v) => padT + (1 - (v - min) / (max - min)) * (h - padT - padB);

  ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.muted;
  ctx.font = '10px ui-monospace, monospace';
  for (let i = 0; i <= 3; i++) {
    const v = min + (max - min) * (i / 3);
    const yy = Math.round(y(v)) + 0.5;
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
    ctx.fillText(v.toFixed(0), w - padR + 6, yy + 3);
  }

  const start = pts[0].equity, last = pts[pts.length - 1].equity;
  const up = last >= start;
  const color = up ? COLORS.green : COLORS.red;

  // starting balance reference
  line(ctx, padL, w - padR, y(start), '#ffffff22', [4, 4]);

  const grad = ctx.createLinearGradient(0, padT, 0, h - padB);
  grad.addColorStop(0, color + '44');
  grad.addColorStop(1, color + '03');
  ctx.beginPath();
  ctx.moveTo(x(0), y(pts[0].equity));
  pts.forEach((p, i) => ctx.lineTo(x(i), y(p.equity)));
  ctx.lineTo(x(pts.length - 1), h - padB); ctx.lineTo(x(0), h - padB); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath();
  ctx.moveTo(x(0), y(pts[0].equity));
  pts.forEach((p, i) => ctx.lineTo(x(i), y(p.equity)));
  ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.stroke();

  const delta = last - start;
  $('equity-delta').textContent =
    `${delta >= 0 ? '+' : ''}${fmt(delta)} (${fmt(delta / start * 100)}%)`;
  $('equity-delta').className = signClass(delta);
}

function emptyChart(ctx, w, h, message) {
  ctx.fillStyle = COLORS.muted;
  ctx.font = '12px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(message, w / 2, h / 2);
  ctx.textAlign = 'left';
}

/* ------------------------------------------------------------------ boot */
function boot() {
  $('btn-run').onclick = () =>
    control(state.snapshot && state.snapshot.running ? 'stop' : 'start');
  $('btn-flatten').onclick = () => control('flatten');
  $('btn-halt').onclick = () =>
    control(state.snapshot && state.snapshot.risk.halted ? 'resume' : 'halt');
  $('btn-risk').onclick = () => control('risk', { risk_pct: parseFloat($('risk-input').value) });
  $('risk-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') control('risk', { risk_pct: parseFloat(e.target.value) });
  });

  api('/api/config').then((cfg) => {
    state.entryThreshold = cfg.strategy.entry_threshold;
  }).catch(() => {});

  window.addEventListener('resize', () => { drawPrice(); drawScore(); drawEquity(); });

  refresh();
  const loop = () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => refresh().finally(loop), state.refreshMs);
  };
  loop();
}

document.addEventListener('DOMContentLoaded', boot);
