/* Mark 1 frontend — plain JS, Lightweight Charts v4. */
(() => {
  "use strict";
  const PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "BTCUSD"];
  const TFS = ["1m", "5m", "1h", "4h"];
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("mark1." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("mark1." + k, JSON.stringify(v)); } catch { /* ignore */ } },
  };
  const S = {
    pair: store.get("pair", "XAUUSD"), tf: store.get("tf", "5m"),
    ov: store.get("ov", { fvg: true, ob: true, swings: true, structure: true, range: true, ema: true }),
    candles: null, status: null, signal: null, lastAlertKey: null, digits: 2,
  };

  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((body && body.detail) || r.statusText);
    return body;
  }
  const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });
  const fmt = (v, d = S.digits) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
  const fmtR = (v) => v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "R";
  const pct = (v) => v === null || v === undefined ? "—" : (v * 100).toFixed(1) + "%";
  const ts = (t) => t ? new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ") : "—";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const arrow = (v) => v === 1 ? '<span class="tr pos">▲</span>' : v === -1 ? '<span class="tr neg">▼</span>' : '<span class="tr muted">•</span>';

  // ---------------- tabs ----------------
  function renderTabs() {
    $("pairTabs").innerHTML = PAIRS.map((p) => `<button data-p="${p}" class="${p === S.pair ? "active" : ""}">${p}</button>`).join("");
    $("tfTabs").innerHTML = TFS.map((t) => `<button data-t="${t}" class="${t === S.tf ? "active" : ""}">${t}</button>`).join("");
    $("hPair").textContent = S.pair;
  }
  $("pairTabs").addEventListener("click", (e) => { const p = e.target.dataset.p; if (p) selectPair(p); });
  $("tfTabs").addEventListener("click", (e) => { const t = e.target.dataset.t; if (t) { S.tf = t; store.set("tf", t); renderTabs(); loadCandles(true); } });
  function selectPair(p) {
    S.pair = p; store.set("pair", p); renderTabs(); $("whyBox").classList.add("hidden");
    loadCandles(true); loadSignal(); renderGridSel();
  }

  // ---------------- chart ----------------
  const LW = window.LightweightCharts;
  const chartEl = $("chart");
  const chart = LW.createChart(chartEl, {
    layout: { background: { color: "transparent" }, textColor: "#8aa0bf", fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: "rgba(27,39,64,.5)" }, horzLines: { color: "rgba(27,39,64,.5)" } },
    rightPriceScale: { borderColor: "#1b2740" }, timeScale: { borderColor: "#1b2740", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 }, autoSize: true,
  });
  const candleSeries = chart.addCandlestickSeries({
    upColor: "#19ff9c", downColor: "#ff3b5c", borderUpColor: "#19ff9c", borderDownColor: "#ff3b5c",
    wickUpColor: "#19ff9c", wickDownColor: "#ff3b5c",
  });
  const emaF = chart.addLineSeries({ color: "#00f0ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const emaS = chart.addLineSeries({ color: "#b26bff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  let priceLines = [];

  const canvas = $("overlay");
  const ctx = canvas.getContext("2d");
  function sizeCanvas() {
    const r = $("chartWrap").getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawOverlay();
  }
  new ResizeObserver(sizeCanvas).observe($("chartWrap"));
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawOverlay());
  setInterval(drawOverlay, 700); // price-scale drags don't emit events

  function xOf(t) {
    const c = S.candles && S.candles.candles; if (!c || !c.length) return null;
    let lo = 0, hi = c.length - 1;
    if (t <= c[0].time) return chart.timeScale().logicalToCoordinate(0);
    if (t >= c[hi].time) return chart.timeScale().logicalToCoordinate(hi + (t - c[hi].time) / Math.max(1, c[hi].time - c[hi - 1].time));
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (c[m].time <= t) lo = m; else hi = m; }
    return chart.timeScale().logicalToCoordinate(lo + (t - c[lo].time) / Math.max(1, c[hi].time - c[lo].time));
  }
  const yOf = (p) => candleSeries.priceToCoordinate(p);

  function drawOverlay() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    const d = S.candles; if (!d || !d.overlays) return;
    const plotW = w - (chart.priceScale("right").width() || 60);
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotW, h); ctx.clip();
    const box = (z, fill, stroke, label) => {
      const x1 = xOf(z.start), x2 = xOf(z.end), y1 = yOf(z.top), y2 = yOf(z.bottom);
      if ([x1, x2, y1, y2].some((v) => v === null || v === undefined)) return;
      ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = 1;
      ctx.fillRect(x1, y1, Math.max(2, x2 - x1), y2 - y1); ctx.strokeRect(x1, y1, Math.max(2, x2 - x1), y2 - y1);
      if (label && x2 - x1 > 30) { ctx.fillStyle = stroke; ctx.font = "10px monospace"; ctx.fillText(label, x1 + 3, y1 + 11); }
    };
    if (S.ov.fvg) for (const z of d.overlays.fvg) box(z, z.dir === 1 ? "rgba(25,255,156,.10)" : "rgba(255,59,92,.10)", z.dir === 1 ? "rgba(25,255,156,.5)" : "rgba(255,59,92,.5)", "FVG");
    if (S.ov.ob) for (const z of d.overlays.ob) box(z, z.dir === 1 ? "rgba(0,240,255,.12)" : "rgba(178,107,255,.14)", z.dir === 1 ? "rgba(0,240,255,.7)" : "rgba(178,107,255,.75)", "OB");
    if (S.ov.range) {
      for (const r of d.overlays.range) {
        const x1 = xOf(r.start), x2 = xOf(r.end + 12 * 3600);
        for (const [p, lab] of [[r.high, "6-6 H"], [r.low, "6-6 L"]]) {
          const y = yOf(p); if (y === null || x1 === null) continue;
          ctx.strokeStyle = "rgba(255,193,77,.85)"; ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = "rgba(255,193,77,.95)"; ctx.font = "10px monospace"; ctx.fillText(lab, x1 + 3, y - 3);
        }
      }
    }
    ctx.restore();
  }

  function setMarkers() {
    const d = S.candles; if (!d) return;
    const m = [];
    if (S.ov.swings) for (const s of d.overlays.swings) m.push({ time: s.time, position: s.kind === "H" ? "aboveBar" : "belowBar", color: s.kind === "H" ? "#ff8da1" : "#7dffc6", shape: "circle", size: 0.4, text: s.kind === "H" ? "SH" : "SL" });
    if (S.ov.structure) for (const e of d.overlays.structure) {
      const up = e.event.endsWith("up");
      m.push({ time: e.time, position: up ? "belowBar" : "aboveBar", color: e.event.startsWith("CHoCH") ? "#ffc14d" : "#00f0ff", shape: up ? "arrowUp" : "arrowDown", text: e.event.split("_")[0] });
    }
    m.sort((a, b) => a.time - b.time);
    candleSeries.setMarkers(m);
    emaF.applyOptions({ visible: S.ov.ema }); emaS.applyOptions({ visible: S.ov.ema });
  }

  function setPriceLines() {
    priceLines.forEach((l) => candleSeries.removePriceLine(l)); priceLines = [];
    const s = S.signal; if (!s || s.pair !== S.pair || !s.sl) return;
    const live = s.side === "BUY" || s.side === "SELL";
    const style = live ? 0 : 2;
    priceLines.push(candleSeries.createPriceLine({ price: s.entry, color: "#d7e3f4", lineWidth: 1, lineStyle: style, title: live ? "Entry" : "entry?" }));
    priceLines.push(candleSeries.createPriceLine({ price: s.sl, color: "#ff3b5c", lineWidth: 1, lineStyle: style, title: live ? "SL" : "SL?" }));
    priceLines.push(candleSeries.createPriceLine({ price: s.tp, color: "#19ff9c", lineWidth: 1, lineStyle: style, title: live ? "TP" : "TP?" }));
  }

  let candleReq = 0;
  async function loadCandles(fit) {
    const my = ++candleReq;
    try {
      const d = await api(`/api/candles/${S.pair}?tf=${S.tf}&count=500`);
      if (my !== candleReq) return;
      S.candles = d; S.digits = d.digits ?? 2;
      const prec = { type: "price", precision: S.digits, minMove: Math.pow(10, -S.digits) };
      candleSeries.applyOptions({ priceFormat: prec });
      candleSeries.setData(d.candles); emaF.setData(d.ema_fast || []); emaS.setData(d.ema_slow || []);
      $("chartMsg").classList.toggle("hidden", d.candles.length > 0);
      $("chartMsg").textContent = d.candles.length ? "" : "No candles — check data source status.";
      if (fit) chart.timeScale().fitContent();
      setMarkers(); setPriceLines(); drawOverlay();
    } catch (e) { $("chartMsg").classList.remove("hidden"); $("chartMsg").textContent = "Chart error: " + e.message; }
  }
  document.querySelectorAll("#overlayToggles input").forEach((el) => {
    el.checked = !!S.ov[el.dataset.ov];
    el.addEventListener("change", () => { S.ov[el.dataset.ov] = el.checked; store.set("ov", S.ov); setMarkers(); drawOverlay(); });
  });
  function setOverlay(name, on) {
    S.ov[name] = on; store.set("ov", S.ov);
    const el = document.querySelector(`#overlayToggles input[data-ov="${name}"]`); if (el) el.checked = on;
  }

  // ---------------- status / header ----------------
  async function loadStatus() {
    try {
      const s = await api("/api/status"); S.status = s;
      $("synthBadge").classList.toggle("hidden", s.data_mode !== "synthetic");
      $("hSession").textContent = s.session;
      const n = s.news; const nEl = $("hNews");
      nEl.textContent = n.state === "paused" ? "PAUSED" : (n.error ? "clear*" : "clear");
      nEl.className = "chip " + (n.state === "paused" ? "warn" : "ok");
      nEl.title = n.text + (n.error ? " — " + n.error : "");
      const eng = s.engine; const eEl = $("hEngine");
      eEl.textContent = eng.killed ? "KILLED" : eng.running ? "RUNNING" : "STOPPED";
      eEl.className = "chip " + (eng.killed ? "bad" : eng.running ? "ok" : "warn");
      eEl.title = eng.error ? "Error: " + eng.error : `tick ${eng.tick_ms ?? "—"} ms, last ${ts(eng.last_tick)}`;
      const g = s.gates; const dEl = $("hDaily");
      dEl.textContent = `${fmtR(g.daily_r)} / -${s.params.daily_loss_limit_r}R`;
      dEl.className = "chip " + (g.daily_limit_hit ? "bad" : g.daily_r < 0 ? "warn" : "ok");
      $("hUpdated").textContent = eng.last_tick ? new Date(eng.last_tick * 1000).toLocaleTimeString() : "—";
      const be = $("btnEngine"); be.textContent = eng.running ? "Stop Engine" : "Start Engine"; be.classList.toggle("running", eng.running);
      $("btnNews").classList.toggle("on", s.ui.news_pause_manual); $("btnNews").textContent = s.ui.news_pause_manual ? "Resume (news)" : "Pause for News";
      $("btnSound").classList.toggle("on", s.ui.sound); $("btnSound").textContent = "Sound " + (s.ui.sound ? "on" : "off");
      $("btnWhatsApp").classList.toggle("on", s.ui.whatsapp); $("btnWhatsApp").textContent = "WhatsApp " + (s.ui.whatsapp ? "on" : "off");
      [$("btnKill"), $("btnKill2")].forEach((b) => b.classList.toggle("active", eng.killed));
      $("btnKillReset").classList.toggle("hidden", !eng.killed);
      $("sDailyLimit").textContent = `${fmtR(g.daily_r)} of -${s.params.daily_loss_limit_r}R`;
      const used = Math.min(100, Math.max(0, -g.daily_r / s.params.daily_loss_limit_r * 100));
      $("dailyMeter").style.width = used + "%"; $("dailyMeter").style.background = used >= 100 ? "var(--red)" : used > 50 ? "var(--amber)" : "var(--green)";
      $("sTrailing").textContent = s.params.trailing ? "ON (1×ATR after +1R)" : "off";
      const k = s.keys;
      $("keysInfo").innerHTML = `Data: <b>${esc(s.data_mode)}</b> · OANDA key ${k.oanda ? "✓" : "✗"} · Twilio ${k.twilio ? "✓" : "✗"} · Finnhub ${k.finnhub ? "✓" : "—"}`;
      $("btcInfo").textContent = "BTC on OANDA: " + (s.btc_check?.note || "—");
      const notes = [];
      if (s.data_mode === "synthetic") notes.push("Synthetic data");
      if (s.ui.whatsapp && !k.twilio) notes.push("WhatsApp on but Twilio keys missing");
      if (n.error) notes.push("News feed: " + n.error);
      for (const [p, d] of Object.entries(s.data_status || {})) if (d && !d.ok) notes.push(`${p}: ${d.error}`);
      $("ctlNote").textContent = notes.join(" · ");
    } catch (e) { $("ctlNote").textContent = "Server unreachable: " + e.message; }
  }

  // ---------------- signal card ----------------
  async function loadSignal() {
    try {
      const s = await api(`/api/signal/${S.pair}`);
      S.signal = s; S.digits = s.digits ?? S.digits;
      $("sigPair").textContent = s.pair + (s.setup ? " · " + s.setup : "");
      const sideEl = $("sigSide"); sideEl.textContent = s.side; sideEl.className = "side " + (s.side === "NO TRADE" ? "NO" : s.side);
      const sc = s.score ?? 0; $("sigScore").textContent = s.score ?? "—";
      const ring = $("ringFg"); ring.style.strokeDashoffset = 314 * (1 - sc / 100);
      ring.style.stroke = sc >= 70 ? "var(--green)" : sc >= 50 ? "var(--cyan)" : "var(--amber)";
      const live = s.side === "BUY" || s.side === "SELL";
      $("sigEntry").textContent = fmt(s.entry); $("sigSl").textContent = fmt(s.sl); $("sigTp").textContent = fmt(s.tp);
      $("sigRr").textContent = s.rr ? "1:" + s.rr : "—";
      $("sigNote").textContent = (live ? "" : `Levels shown are hypothetical (${s.direction_checked || "—"} side, if it triggered). `) +
        "Score is a heuristic, not a probability." + (s.action && s.action !== "watch" ? " · " + s.action : "");
      if (s.price) { $("hPrice").textContent = fmt(s.price); $("hSpread").textContent = fmt(s.spread); }
      for (const tf of ["H4", "H1", "M5"]) {
        const v = s.alignment?.[tf] ?? 0; const el = $("al" + tf);
        el.className = "al " + (v === 1 ? "up" : v === -1 ? "down" : "");
        el.querySelector("span").textContent = v === 1 ? "▲ up" : v === -1 ? "▼ down" : "• flat";
      }
      $("alAll").classList.toggle("hidden", !s.all_aligned);
      setPriceLines();
      if (live) notify(s);
    } catch (e) { $("sigNote").textContent = "Signal error: " + e.message; }
  }

  // sound on a fresh BUY/SELL
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "square"; o.frequency.value = 880; g.gain.value = 0.05;
      o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + 0.25);
    } catch { /* audio blocked */ }
  }
  function notify(s) {
    const key = `${s.pair}|${s.side}|${s.candle_t}`;
    if (key === S.lastAlertKey) return;
    S.lastAlertKey = key;
    if (S.status?.ui?.sound) beep();
  }

  async function showWhy(pair) {
    const box = $("whyBox"); box.classList.remove("hidden"); box.innerHTML = '<span class="muted">Loading…</span>';
    try { box.innerHTML = renderWhy(await api(`/api/why/${pair || S.pair}`)); }
    catch (e) { box.textContent = "Error: " + e.message; }
  }
  function renderWhy(w) {
    const st = w.stats;
    const items = w.checklist.map((i) => `<li class="${i.required ? "req" : ""}">${i.ok ? '<span class="pos">✓</span>' : '<span class="neg">✗</span>'} ${esc(i.item)}</li>`).join("");
    const stats = st.trades
      ? `Past <b>${esc(st.setup)}</b> on ${esc(w.pair)}: ${st.trades} trades · win rate ${pct(st.win_rate)} · avg ${fmtR(st.avg_r)} <span class="muted">(${esc(st.sources.join(", "))})</span>`
      : `No past trades for <b>${esc(st.setup)}</b> yet.`;
    return `<div><b>${esc(w.side)}</b> · score ${w.score}/100</div><ul>${items}</ul><div>${stats}</div>` +
      (st.warning ? `<div class="warnbox">⚠ ${esc(st.warning)}</div>` : "") + `<div class="muted tiny">${esc(w.note)}</div>`;
  }
  $("btnWhy").addEventListener("click", () => showWhy());

  // ---------------- market grid ----------------
  async function loadGrid() {
    try {
      const rows = await api("/api/pairs");
      $("marketGrid").querySelector("tbody").innerHTML = rows.map((r) => {
        const a = r.alignment || {};
        const cls = r.side === "BUY" ? "pos" : r.side === "SELL" ? "neg" : "muted";
        return `<tr data-p="${r.pair}" class="${r.pair === S.pair ? "sel" : ""}"><td>${r.pair}${r.all_aligned ? ' <span class="pos" title="All timeframes aligned">●</span>' : ""}</td>
          <td>${fmt(r.price, r.digits)}</td><td>${fmt(r.spread, r.digits)}</td>
          <td>${arrow(a.H4)}${arrow(a.H1)}${arrow(a.M5)}</td><td class="${cls}">${esc(r.side || "—")}</td><td>${r.score ?? "—"}</td></tr>`;
      }).join("");
    } catch { /* keep last */ }
  }
  function renderGridSel() { document.querySelectorAll("#marketGrid tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.p === S.pair)); }
  $("marketGrid").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-p]"); if (tr) selectPair(tr.dataset.p); });

  async function loadNews() {
    try {
      const n = await api("/api/news");
      const up = (n.upcoming || []).map((e) => `<div>⚑ ${ts(e.t)} UTC · ${esc(e.title)} <span class="neg">USD high</span></div>`).join("");
      const hl = (n.headlines || []).slice(0, 4).map((h) => `<div>📰 ${esc(h.headline)} <span class="muted">${esc(h.source)}</span></div>`).join("");
      $("newsList").innerHTML = `<div class="muted">${esc(n.text)}${n.error ? " · " + esc(n.error) : ""}</div>` + (up || '<div class="muted">No upcoming high-impact USD events this week (or feed unavailable).</div>') + hl;
    } catch { /* ignore */ }
  }

  // ---------------- trades + stats ----------------
  async function loadTrades() {
    try {
      const rows = await api("/api/trades?limit=200");
      $("tradeLog").querySelector("tbody").innerHTML = rows.length ? rows.map((t) => {
        const d = t.pair === "EURUSD" || t.pair === "GBPUSD" ? 5 : 2;
        return `<tr><td>${t.id}</td><td>${t.pair}</td><td class="${t.side === "BUY" ? "pos" : "neg"}">${t.side}</td><td>${esc(t.setup)}</td><td>${t.score}</td>
          <td>${fmt(t.entry, d)}</td><td>${fmt(t.sl, d)}</td><td>${fmt(t.tp, d)}</td><td>${ts(t.opened_at)}</td><td>${ts(t.closed_at)}</td>
          <td>${fmt(t.exit, d)}</td><td class="${t.r > 0 ? "pos" : t.r < 0 ? "neg" : ""}">${fmtR(t.r)}</td><td>${esc(t.status)}${t.reason ? " · " + esc(t.reason) : ""}</td></tr>`;
      }).join("") : '<tr><td colspan="13" class="muted">No paper trades yet. Start the engine — trades are logged when a confirmed setup closes.</td></tr>';
    } catch { /* ignore */ }
  }
  async function loadStats() {
    try {
      const s = await api("/api/stats"); const p = s.paper;
      $("sTrades").textContent = p.trades; $("sWl").textContent = p.win_loss_ratio ?? "—"; $("sWr").textContent = p.trades ? pct(p.win_rate) : "—";
      $("sPf").textContent = p.trades ? (p.profit_factor ?? "∞") : "—"; $("sTotal").textContent = p.trades ? fmtR(p.total_r) : "—";
      $("sDd").textContent = p.trades ? p.max_drawdown_r + "R" : "—"; $("sOpen").textContent = p.open;
      $("sNote").textContent = p.trades < 100 ? `Only ${p.trades} closed paper trades — too few to judge the strategy (need 100+).` : "";
      const b = s.latest_backtest;
      $("sBt").innerHTML = b ? `#${b.id} ${esc(b.pair)} ${esc(b.mode)}: ${b.summary.trades} trades · WR ${pct(b.summary.win_rate)} · PF ${b.summary.profit_factor ?? "∞"} · ${fmtR(b.summary.total_r)}` +
        (b.summary.verdict ? `<div class="${b.summary.verdict.level === "ok" ? "pos" : "neg"}">${esc(b.summary.verdict.notes[0])}</div>` : "") : '<span class="muted">none yet</span>';
    } catch { /* ignore */ }
  }

  // ---------------- backtest ----------------
  $("btPair").innerHTML = PAIRS.map((p) => `<option>${p}</option>`).join("");
  let eqChart = null, eqSeries = null;
  function equityChart() {
    if (eqChart) return;
    eqChart = LW.createChart($("equityChart"), {
      layout: { background: { color: "transparent" }, textColor: "#8aa0bf" }, autoSize: true,
      grid: { vertLines: { color: "rgba(27,39,64,.4)" }, horzLines: { color: "rgba(27,39,64,.4)" } },
      timeScale: { timeVisible: true, borderColor: "#1b2740" }, rightPriceScale: { borderColor: "#1b2740" },
    });
    eqSeries = eqChart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, topLineColor: "#19ff9c", bottomLineColor: "#ff3b5c",
      topFillColor1: "rgba(25,255,156,.25)", topFillColor2: "rgba(25,255,156,.02)", bottomFillColor1: "rgba(255,59,92,.02)", bottomFillColor2: "rgba(255,59,92,.25)" });
  }
  const dedupeTimes = (pts) => { const out = []; for (const p of pts) { if (out.length && out[out.length - 1].time >= p.time) { out[out.length - 1].value = p.value; } else out.push({ ...p }); } return out; };
  function metricsHtml(s) {
    const m = [["Trades", s.trades], ["Win rate", pct(s.win_rate)], ["Profit factor", s.profit_factor ?? (s.trades ? "∞" : "—")], ["Avg R", fmtR(s.avg_r)], ["Total R", fmtR(s.total_r)], ["Max DD", s.max_drawdown_r + "R"]];
    return m.map(([k, v]) => `<div class="metric"><label>${k}</label><span>${v}</span></div>`).join("");
  }
  function renderBacktest(r) {
    if (!r) return;
    const v = r.verdict || { level: "weak", notes: [] };
    const el = $("btVerdict"); el.className = "verdict " + v.level;
    const head = { ok: "No red flags", weak: "Weak / inconclusive", bad: "Poor result", synthetic: "Synthetic data" }[v.level] || v.level;
    el.innerHTML = `<b>${esc(r.pair)} · ${esc(r.mode)} · ${head}</b><ul>${v.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
    $("btMetrics").innerHTML = metricsHtml(r.summary);
    equityChart(); eqSeries.setData(dedupeTimes(r.equity || [])); eqChart.timeScale().fitContent();
    $("btFolds").innerHTML = r.folds && r.folds.length ? `<table class="folds"><thead><tr><th>Test window</th><th>Tuned params</th><th>Train R</th><th>Test trades</th><th>Test R</th></tr></thead><tbody>` +
      r.folds.map((f) => `<tr><td>${ts(f.test[0]).slice(0, 5)} → ${ts(f.test[1]).slice(0, 5)}</td><td>z ${f.params.z_entry} · score ${f.params.min_score}</td><td>${fmtR(f.train_summary.total_r)}</td><td>${f.test_summary.trades}</td><td class="${f.test_summary.total_r >= 0 ? "pos" : "neg"}">${fmtR(f.test_summary.total_r)}</td></tr>`).join("") + "</tbody></table>" : "";
  }
  async function runBacktest(req) {
    const btn = $("btForm").querySelector("button"); btn.disabled = true; $("btnBacktest").disabled = true;
    try {
      const { job } = await post("/api/backtest", req);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        const j = await api(`/api/backtest/job/${job}`);
        $("btStage").textContent = j.stage;
        if (j.status === "done") { renderBacktest(j.result); loadStats(); break; }
        if (j.status === "error") { $("btStage").textContent = "Error: " + j.error; break; }
      }
    } catch (e) { $("btStage").textContent = "Error: " + e.message; }
    btn.disabled = false; $("btnBacktest").disabled = false;
  }
  function formReq() {
    const f = new FormData($("btForm"));
    return { pair: f.get("pair"), months: +f.get("months"), slippage_pips: +f.get("slippage_pips"), walkforward: f.get("walkforward") === "on", train_days: +f.get("train_days"), test_days: +f.get("test_days") };
  }
  $("btForm").addEventListener("submit", (e) => { e.preventDefault(); runBacktest(formReq()); });
  $("btnBacktest").addEventListener("click", () => { $("btPair").value = S.pair; $("btForm").scrollIntoView({ behavior: "smooth" }); runBacktest(formReq()); });

  // ---------------- controls ----------------
  const guard = (fn) => async (e) => { const b = e.currentTarget; b.disabled = true; try { await fn(); } catch (err) { alert(err.message); } b.disabled = false; loadStatus(); };
  $("btnEngine").addEventListener("click", guard(() => post(`/api/engine/${S.status?.engine?.running ? "stop" : "start"}`)));
  $("btnNews").addEventListener("click", guard(() => post("/api/toggle/news_pause", { on: !S.status?.ui?.news_pause_manual })));
  $("btnSound").addEventListener("click", guard(async () => { await post("/api/toggle/sound", { on: !S.status?.ui?.sound }); if (!S.status?.ui?.sound) beep(); }));
  $("btnWhatsApp").addEventListener("click", guard(() => post("/api/toggle/whatsapp", { on: !S.status?.ui?.whatsapp })));
  const doKill = guard(async () => { if (!confirm("KILL SWITCH: stop the engine and close all open paper trades?")) return; await post("/api/kill"); loadTrades(); });
  $("btnKill").addEventListener("click", doKill); $("btnKill2").addEventListener("click", doKill);
  $("btnKillReset").addEventListener("click", guard(() => post("/api/kill/reset")));

  // ---------------- assistant ----------------
  const chatEl = $("chat");
  function say(html, who = "bot") { const d = document.createElement("div"); d.className = "msg " + who; d.innerHTML = html; chatEl.appendChild(d); chatEl.scrollTop = chatEl.scrollHeight; return d; }
  function suggestionHtml(r) {
    const rows = [["Trades", "trades"], ["Win rate", "win_rate", pct], ["Profit factor", "profit_factor"], ["Avg R", "avg_r", fmtR], ["Total R", "total_r", fmtR], ["Max DD (R)", "max_drawdown_r"]];
    const b = r.baseline.summary, p = r.proposed.summary;
    const tbl = `<table><thead><tr><th></th><th>Current</th><th>Proposed</th></tr></thead><tbody>${rows.map(([n, k, f]) => `<tr><td>${n}</td><td>${f ? f(b[k]) : (b[k] ?? "—")}</td><td>${f ? f(p[k]) : (p[k] ?? "—")}</td></tr>`).join("")}</tbody></table>`;
    const better = p.total_r > b.total_r;
    const notes = [...(r.notes || []), ...(r.proposed.verdict?.notes || []).slice(0, 2)];
    return `<b>Suggestion #${r.id}</b>: ${esc(r.text)}<div class="muted tiny">${Object.entries(r.changes).map(([k, v]) => `${esc(k)}: ${esc(r.current[k])} → ${esc(v)}`).join(", ")}</div>${tbl}
      <div class="sugg-eq"></div><div class="${better ? "pos" : "neg"}">${better ? "Proposed did better on this test." : "Proposed did NOT beat current on this test."}</div>
      <ul class="tiny">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
      <div class="actions"><button class="btn approve">Approve</button><button class="btn reject">Reject</button></div>`;
  }
  function attachSuggestion(el, r) {
    const eqDiv = el.querySelector(".sugg-eq");
    const c = LW.createChart(eqDiv, { layout: { background: { color: "transparent" }, textColor: "#8aa0bf" }, autoSize: true, grid: { vertLines: { visible: false }, horzLines: { color: "rgba(27,39,64,.4)" } }, timeScale: { visible: false }, rightPriceScale: { borderVisible: false } });
    c.addLineSeries({ color: "#6f819e", lineWidth: 1, title: "current" }).setData(dedupeTimes(r.baseline.equity));
    c.addLineSeries({ color: "#00f0ff", lineWidth: 2, title: "proposed" }).setData(dedupeTimes(r.proposed.equity));
    c.timeScale().fitContent();
    const done = (txt) => { el.querySelector(".actions").innerHTML = `<span class="muted">${txt}</span>`; };
    el.querySelector(".approve").addEventListener("click", async () => {
      try { await post(`/api/suggestions/${r.id}/approve`); done("Approved — applied to strategy settings."); loadStatus(); loadSignal(); } catch (e) { done("Error: " + esc(e.message)); }
    });
    el.querySelector(".reject").addEventListener("click", async () => {
      try { await post(`/api/suggestions/${r.id}/reject`); done("Rejected — nothing changed."); } catch (e) { done("Error: " + esc(e.message)); }
    });
  }
  $("askForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("askInput").value.trim(); if (!text) return;
    $("askInput").value = ""; say(esc(text), "me");
    const wait = say('<span class="muted">Working… (strategy suggestions run a backtest first)</span>');
    try {
      const r = await post("/api/assistant", { text, pair: S.pair });
      wait.remove();
      if (r.kind === "view") {
        for (const o of r.overlays) setOverlay(o, r.show);
        if (r.pair && r.pair !== S.pair) selectPair(r.pair);
        if (r.tf) { S.tf = r.tf; store.set("tf", r.tf); renderTabs(); await loadCandles(true); } else { setMarkers(); drawOverlay(); }
        say(esc(r.text) + (r.overlays.length ? ` (${r.show ? "showing" : "hiding"} ${r.overlays.join(", ")}${r.tf ? " on " + r.tf : ""})` : ""));
      } else if (r.kind === "why") {
        say(renderWhy(r));
      } else if (r.kind === "run_backtest") {
        say(esc(r.text)); $("btPair").value = r.pair; $("btForm").months.value = String(Math.min(6, Math.max(1, r.months)));
        $("btForm").walkforward.checked = !!r.walkforward; runBacktest(formReq());
      } else if (r.kind === "suggestion") {
        const el = say(suggestionHtml(r)); attachSuggestion(el, r);
      } else { say(esc(r.text || JSON.stringify(r))); }
    } catch (err) { wait.remove(); say("Error: " + esc(err.message)); }
  });

  // ---------------- boot ----------------
  renderTabs();
  loadStatus(); loadGrid(); loadSignal(); loadCandles(true); loadTrades(); loadStats(); loadNews();
  api("/api/backtest/latest").then(renderBacktest).catch(() => {});
  setInterval(loadStatus, 5000);
  setInterval(() => { loadGrid(); loadSignal(); }, 10000);
  setInterval(() => loadCandles(false), 30000);
  setInterval(() => { loadTrades(); loadStats(); }, 15000);
  setInterval(loadNews, 300000);
})();
