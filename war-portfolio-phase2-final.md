# War Portfolio API — Phase 2: Elite Upgrade (Final)

## Context

Phase 1 (10 basic refactor tasks) is already executed. This is Phase 2 — the upgrade that turns this from "functional dashboard" into the most advanced personal trading intelligence terminal a solo investor can build with modern web technology. The repo is already cloned locally at `~/Desktop/war-portfolio-api`. Execute every task in order. **DO NOT SKIP ANY TASK. DO NOT ASK QUESTIONS. EXECUTE AUTONOMOUSLY.**

**HARD CONSTRAINTS — VIOLATING ANY OF THESE IS A FAILURE:**
- Keep vanilla JS — NO React, NO Vue, NO Svelte, NO frameworks. Zero-build Vercel deploy.
- NO Anthropic API, NO `ANTHROPIC_API_KEY`, NO inline Claude responses. The brief opens Claude.ai in a new tab OR copies prompt to clipboard. That's it.
- Every frontend library must load via CDN (esm.sh, unpkg, or cdnjs). No `npm install` for the frontend.
- Backend stays as Vercel serverless functions in `/api/`.
- DO NOT break any existing Phase 1 functionality. Everything here is ADDITIVE.
- Remove the "Quick Analysis" tab entirely — it's dead. Remove all `renderAnalysisButtons()`, the tab button, and the `tab-analysis` panel from the HTML.

---

## TASK 1: Add historical price data endpoint

Create `/api/history.js` — a new endpoint that fetches historical OHLCV data from Yahoo Finance for charting.

```javascript
// /api/history.js
export default async function handler(req, res) {
  // CORS — use the same pattern as other endpoints from Phase 1
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  res.setHeader('Cache-Control', 's-maxage=900, stale-while-revalidate=300');

  const { symbol, range, interval } = req.query;
  if (!symbol) return res.status(400).json({ error: 'symbol required' });

  const r = range || '1mo';
  const i = interval || '1d';
  const enc = encodeURIComponent(symbol);

  const urls = [
    `https://query1.finance.yahoo.com/v8/finance/chart/${enc}?range=${r}&interval=${i}&includePrePost=false`,
    `https://query2.finance.yahoo.com/v8/finance/chart/${enc}?range=${r}&interval=${i}&includePrePost=false`,
  ];

  for (const url of urls) {
    try {
      const resp = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!resp.ok) continue;
      const d = await resp.json();
      const result = d?.chart?.result?.[0];
      if (!result) continue;

      const timestamps = result.timestamp || [];
      const quote = result.indicators?.quote?.[0] || {};

      const candles = timestamps.map((t, idx) => ({
        time: t,
        open: quote.open?.[idx] != null ? parseFloat(quote.open[idx].toFixed(2)) : null,
        high: quote.high?.[idx] != null ? parseFloat(quote.high[idx].toFixed(2)) : null,
        low: quote.low?.[idx] != null ? parseFloat(quote.low[idx].toFixed(2)) : null,
        close: quote.close?.[idx] != null ? parseFloat(quote.close[idx].toFixed(2)) : null,
        volume: quote.volume?.[idx] || 0,
      })).filter(c => c.open !== null && c.close !== null);

      return res.status(200).json({ symbol, range: r, interval: i, currency: result.meta?.currency || 'USD', candles });
    } catch (err) {
      console.error(`[history] ${symbol} failed:`, err.message);
    }
  }
  return res.status(502).json({ error: 'history fetch failed for ' + symbol });
}
```

---

## TASK 2: Integrate TradingView Lightweight Charts

TradingView Lightweight Charts is a 35KB open-source (Apache 2.0) library used by Robinhood, Coinbase, and Interactive Brokers. It's the industry standard for embedded financial charts.

### 2a. Convert the main `<script>` to ES module

Change the existing `<script>` tag to `<script type="module">`. This means ALL functions that are referenced by `onclick` handlers in the HTML must be explicitly assigned to `window`:

```javascript
// At the end of the module script, expose all onclick-referenced functions:
window.loadAll = loadAll;
window.switchTab = switchTab;
window.toggleEdit = toggleEdit;
window.addEditRow = addEditRow;
window.deleteRow = deleteRow;
window.savePositions = savePositions;
window.toggleWatchAdd = toggleWatchAdd;
window.addToWatchlist = addToWatchlist;
window.removeFromWatchlist = removeFromWatchlist;
window.openMasterBrief = openMasterBrief;
window.closeBrief = closeBrief;
window.openChartModal = openChartModal;
window.closeChartModal = closeChartModal;
window.loadScanner = loadScanner;
window.loadNews = loadNews;
```

### 2b. Import the library

At the top of the module script:

```javascript
import { createChart, ColorType, LineStyle } from 'https://esm.sh/lightweight-charts@4.2.1';
```

### 2c. Mini chart renderer for position/watchlist cards

```javascript
const HISTORY_API = location.origin + '/api/history';

async function renderMiniChart(containerId, symbol, range = '1mo') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.style.height = '100px';
  container.style.marginTop = '6px';

  try {
    const r = await fetch(`${HISTORY_API}?symbol=${encodeURIComponent(symbol)}&range=${range}&interval=1d`);
    const data = await r.json();
    if (!data.candles?.length) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 100,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#555', fontSize: 9 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: {
        vertLine: { color: '#333', width: 1, style: LineStyle.Dashed, labelVisible: false },
        horzLine: { color: '#333', width: 1, style: LineStyle.Dashed, labelVisible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    const first = data.candles[0].close;
    const last = data.candles[data.candles.length - 1].close;
    const color = last >= first ? '#2ecc71' : '#e74c3c';

    const series = chart.addAreaSeries({
      lineColor: color, topColor: color + '25', bottomColor: color + '03',
      lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: true, crosshairMarkerRadius: 3,
    });
    series.setData(data.candles.map(c => ({ time: c.time, value: c.close })));
    chart.timeScale().fitContent();

    new ResizeObserver(entries => {
      chart.applyOptions({ width: entries[0].contentRect.width });
    }).observe(container);
  } catch (err) {
    console.warn(`[chart] ${symbol}:`, err.message);
  }
}
```

### 2d. Add chart containers to card templates

In `loadPositions()` and `loadWatchlist()`, add this inside each card's HTML, after the `card-sub` div:

```html
<div id="chart-${p.sym.replace(/[^a-zA-Z0-9]/g, '_')}" class="mini-chart"></div>
```

After rendering all position cards (after `document.getElementById('posGrid').innerHTML = html`), render the charts:

```javascript
for (const p of positions) {
  const chartId = 'chart-' + p.sym.replace(/[^a-zA-Z0-9]/g, '_');
  renderMiniChart(chartId, p.sym, '1mo');
}
```

Same for watchlist cards after rendering.

Add CSS:
```css
.mini-chart { border-radius: 0 0 8px 8px; overflow: hidden; }
```

### 2e. Full-screen chart modal

Add this HTML before the closing `</div>` of `.dash`:

```html
<div id="chartModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.9);z-index:1000;padding:2rem;">
  <div style="max-width:960px;margin:0 auto;height:100%;display:flex;flex-direction:column;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <span id="modalTicker" style="font-size:20px;font-weight:700;color:#fff;font-family:'JetBrains Mono',monospace;"></span>
        <span id="modalName" style="font-size:12px;color:#555;margin-left:10px;"></span>
        <span id="modalPrice" style="font-size:14px;color:#888;margin-left:10px;font-family:'JetBrains Mono',monospace;"></span>
      </div>
      <div style="display:flex;gap:3px;align-items:center;">
        <button class="range-btn" data-range="5d" data-interval="15m">1D</button>
        <button class="range-btn" data-range="5d" data-interval="1d">5D</button>
        <button class="range-btn active" data-range="1mo" data-interval="1d">1M</button>
        <button class="range-btn" data-range="3mo" data-interval="1d">3M</button>
        <button class="range-btn" data-range="6mo" data-interval="1d">6M</button>
        <button class="range-btn" data-range="1y" data-interval="1wk">1Y</button>
        <button class="range-btn" data-range="5y" data-interval="1mo">5Y</button>
        <button onclick="closeChartModal()" style="background:transparent;border:0.5px solid #333;color:#555;border-radius:6px;padding:5px 12px;font-size:12px;cursor:pointer;margin-left:16px;">✕ Close</button>
      </div>
    </div>
    <div id="modalChartContainer" style="flex:1;min-height:0;border-radius:10px;overflow:hidden;"></div>
  </div>
</div>
```

CSS for range buttons:
```css
.range-btn { font-size:10px; padding:4px 10px; border-radius:4px; border:0.5px solid #333; background:transparent; color:#555; cursor:pointer; font-weight:600; font-family:'JetBrains Mono',monospace; transition: all 0.15s; }
.range-btn:hover { color:#aaa; background:#1a1a1a; }
.range-btn.active { background:#1a1a1a; color:#fff; border-color:#444; }
```

Modal JavaScript:

```javascript
let _modalChart = null;
let _modalSymbol = null;

function openChartModal(symbol, name, currentPrice) {
  _modalSymbol = symbol;
  document.getElementById('modalTicker').textContent = symbol;
  document.getElementById('modalName').textContent = name || '';
  document.getElementById('modalPrice').textContent = currentPrice || '';
  document.getElementById('chartModal').style.display = 'block';
  document.body.style.overflow = 'hidden';
  loadModalChart(symbol, '1mo', '1d');
  document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('.range-btn[data-range="1mo"]')?.classList.add('active');
}

function closeChartModal() {
  document.getElementById('chartModal').style.display = 'none';
  document.body.style.overflow = '';
  if (_modalChart) { _modalChart.remove(); _modalChart = null; }
}

async function loadModalChart(symbol, range, interval) {
  const container = document.getElementById('modalChartContainer');
  if (_modalChart) { _modalChart.remove(); _modalChart = null; }
  container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;"><span class="spinner"></span><span style="font-size:11px;color:#555;margin-left:8px;">Loading chart...</span></div>';

  try {
    const r = await fetch(`${HISTORY_API}?symbol=${encodeURIComponent(symbol)}&range=${range}&interval=${interval}`);
    const data = await r.json();
    if (!data.candles?.length) { container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#555;font-size:12px;">No data available</div>'; return; }

    container.innerHTML = '';

    _modalChart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#666', fontSize: 11 },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      rightPriceScale: { borderColor: '#1a1a1a', scaleMargins: { top: 0.05, bottom: 0.2 } },
      timeScale: { borderColor: '#1a1a1a', timeVisible: interval !== '1d' && interval !== '1wk' && interval !== '1mo' },
      crosshair: {
        mode: 0,
        vertLine: { color: '#444', labelBackgroundColor: '#1a1a1a' },
        horzLine: { color: '#444', labelBackgroundColor: '#1a1a1a' },
      },
    });

    const isDailyOrLonger = ['1d', '1wk', '1mo'].includes(interval);

    if (isDailyOrLonger) {
      const series = _modalChart.addCandlestickSeries({
        upColor: '#2ecc71', downColor: '#e74c3c',
        borderUpColor: '#2ecc71', borderDownColor: '#e74c3c',
        wickUpColor: '#2ecc7188', wickDownColor: '#e74c3c88',
      });
      series.setData(data.candles);
    } else {
      const first = data.candles[0].close;
      const last = data.candles[data.candles.length - 1].close;
      const isUp = last >= first;
      const color = isUp ? '#2ecc71' : '#e74c3c';
      const series = _modalChart.addAreaSeries({
        lineColor: color, topColor: color + '25', bottomColor: color + '03', lineWidth: 2,
      });
      series.setData(data.candles.map(c => ({ time: c.time, value: c.close })));
    }

    // Volume histogram overlay
    const volSeries = _modalChart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    _modalChart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volSeries.setData(data.candles.map(c => ({
      time: c.time, value: c.volume,
      color: c.close >= c.open ? '#2ecc7115' : '#e74c3c15',
    })));

    _modalChart.timeScale().fitContent();

    new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      _modalChart?.applyOptions({ width, height });
    }).observe(container);

  } catch (err) {
    container.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#e74c3c;font-size:11px;">Chart error: ${err.message}</div>`;
  }
}

// Range button click handler
document.addEventListener('click', e => {
  if (e.target.classList.contains('range-btn') && _modalSymbol) {
    document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    loadModalChart(_modalSymbol, e.target.dataset.range, e.target.dataset.interval);
  }
});
```

Make ticker names in cards clickable — wrap them with onclick that calls `openChartModal`:

```javascript
// In position card template, change card-ticker to:
<div class="card-ticker" style="cursor:pointer;" onclick="openChartModal('${p.sym}', '${esc(p.name)}', '${cs}${displayPrice}')">${esc(p.display)}</div>

// Same for watchlist cards
```

---

## TASK 3: Real-time WebSocket prices (Finnhub free tier)

Finnhub gives free real-time US stock trades via WebSocket. Free tier = 30 symbols. This adds live-updating prices to position cards during market hours.

### 3a. Create `/api/ws-token.js`

```javascript
export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  const key = process.env.FINNHUB_KEY;
  if (!key) return res.status(200).json({ token: null }); // graceful — no key = no live prices, no error
  return res.status(200).json({ token: key });
}
```

### 3b. Client-side WebSocket manager

```javascript
let _ws = null;
const _wsCallbacks = {};

async function initLivePrices() {
  try {
    const r = await fetch(location.origin + '/api/ws-token');
    const { token } = await r.json();
    if (!token) { console.log('[ws] No Finnhub key — live prices disabled'); return; }

    _ws = new WebSocket(`wss://ws.finnhub.io?token=${token}`);

    _ws.onopen = () => {
      console.log('[ws] Finnhub connected');
      for (const sym of Object.keys(_wsCallbacks)) {
        _ws.send(JSON.stringify({ type: 'subscribe', symbol: sym }));
      }
      // Update status indicator
      const el = document.getElementById('liveIndicator');
      if (el) { el.textContent = 'LIVE'; el.style.color = '#2ecc71'; }
    };

    _ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'trade' && msg.data) {
        // Use only the latest trade per symbol
        const latest = {};
        for (const trade of msg.data) {
          latest[trade.s] = trade.p;
        }
        for (const [sym, price] of Object.entries(latest)) {
          if (_wsCallbacks[sym]) _wsCallbacks[sym](price);
        }
      }
    };

    _ws.onclose = () => {
      console.warn('[ws] Disconnected — reconnecting in 5s');
      const el = document.getElementById('liveIndicator');
      if (el) { el.textContent = 'RECONNECTING'; el.style.color = '#f39c12'; }
      setTimeout(initLivePrices, 5000);
    };

    _ws.onerror = () => {}; // onclose handles reconnect
  } catch (err) {
    console.warn('[ws] Live prices unavailable:', err.message);
  }
}

function subscribeLivePrice(symbol, callback) {
  // Only US tickers (no dots in symbol = no .L, .DE, .PA)
  if (symbol.includes('.') || symbol.includes('=') || symbol.startsWith('^')) return;
  _wsCallbacks[symbol] = callback;
  if (_ws?.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ type: 'subscribe', symbol }));
  }
}
```

### 3c. Wire to position cards

After `loadPositions()` renders, give each price element a unique ID and subscribe:

```javascript
// Add id to price div in card template:
<div class="card-price" id="live-${p.sym.replace(/[^a-zA-Z0-9]/g, '_')}">${cs}${displayPrice}</div>
```

After rendering:
```javascript
for (const p of _positionData) {
  const priceId = 'live-' + p.sym.replace(/[^a-zA-Z0-9]/g, '_');
  subscribeLivePrice(p.sym, (livePrice) => {
    const el = document.getElementById(priceId);
    if (!el) return;
    const prev = parseFloat(el.textContent.replace(/[^0-9.]/g, ''));
    el.textContent = '$' + livePrice.toFixed(2);
    // Flash green/red on update
    el.style.color = livePrice > prev ? '#2ecc71' : livePrice < prev ? '#e74c3c' : '#fff';
    setTimeout(() => { el.style.color = '#fff'; }, 800);
  });
}
```

Call `initLivePrices()` at the end of `loadAll()`.

Add a live indicator in the header:
```html
<span id="liveIndicator" style="font-size:9px;font-weight:700;letter-spacing:0.06em;color:#555;font-family:'JetBrains Mono',monospace;">—</span>
```

**Graceful degradation:** If `FINNHUB_KEY` env var isn't set, everything works exactly as before. The WebSocket is purely additive.

---

## TASK 4: Sector heatmap visualization

Replace the plain text sector performance in the scanner tab with a visual heatmap (like finviz.com).

```javascript
function renderHeatmap(data, containerId) {
  const container = document.getElementById(containerId);
  if (!container || !data.byTag) return;

  const sectors = Object.entries(data.byTag).map(([tag, items]) => {
    const avg = items.reduce((s, i) => s + i.changePct, 0) / items.length;
    const sorted = [...items].sort((a, b) => b.changePct - a.changePct);
    return { tag, avg, count: items.length, items: sorted };
  }).sort((a, b) => b.count - a.count);

  const total = sectors.reduce((s, sec) => s + sec.count, 0);

  let html = '<div style="display:flex;flex-wrap:wrap;gap:4px;">';
  for (const sec of sectors) {
    const intensity = Math.min(Math.abs(sec.avg) * 18, 85);
    const bg = sec.avg >= 0
      ? `rgba(46, 204, 113, ${intensity / 100})`
      : `rgba(231, 76, 60, ${intensity / 100})`;
    const textColor = intensity > 35 ? '#fff' : '#aaa';
    const sign = sec.avg >= 0 ? '+' : '';
    const top = sec.items[0];
    const bottom = sec.items[sec.items.length - 1];

    html += `<div style="
      flex:${sec.count} 1 0;min-width:90px;background:${bg};border:0.5px solid #2a2a2a;border-radius:8px;
      padding:12px 14px;cursor:default;transition:transform 0.1s,box-shadow 0.15s;
    " onmouseover="this.style.transform='scale(1.03)';this.style.boxShadow='0 4px 20px rgba(0,0,0,0.3)'"
       onmouseout="this.style.transform='';this.style.boxShadow=''">
      <div style="font-size:11px;font-weight:700;color:${textColor};text-transform:uppercase;letter-spacing:0.05em;">${esc(sec.tag)}</div>
      <div style="font-size:20px;font-weight:600;color:${textColor};margin-top:3px;font-family:'JetBrains Mono',monospace;">${sign}${sec.avg.toFixed(2)}%</div>
      <div style="font-size:9px;color:${textColor};opacity:0.7;margin-top:6px;font-family:'JetBrains Mono',monospace;">
        ▲ ${esc(top.symbol)} ${top.changePct >= 0 ? '+' : ''}${top.changePct.toFixed(1)}%<br>
        ▼ ${esc(bottom.symbol)} ${bottom.changePct >= 0 ? '+' : ''}${bottom.changePct.toFixed(1)}%
      </div>
      <div style="font-size:8px;color:${textColor};opacity:0.35;margin-top:4px;">${sec.count} tickers</div>
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}
```

In `renderScanner()`, replace the existing `sector-grid` div and its content with:

```html
<div class="section-label" style="margin-top:1.4rem;">Sector heatmap</div>
<div id="sectorHeatmap"></div>
```

Then call `renderHeatmap(data, 'sectorHeatmap')` after writing that HTML.

---

## TASK 5: Portfolio analytics panel

Add a new section after "Macro signals" in the portfolio tab:

```html
<div class="section-label">Portfolio analytics</div>
<div id="analyticsPanel" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;"></div>
<div id="pnlTimeline" style="margin-top:12px;"></div>
```

On mobile (`@media max-width: 600px`), make `analyticsPanel` single column:
```css
@media (max-width: 600px) { #analyticsPanel { grid-template-columns: 1fr; } }
```

### 5a. Allocation donut chart (pure CSS)

```javascript
function renderAllocation(positionData) {
  const el = document.getElementById('analyticsPanel');
  if (!el || !positionData.length) return;

  const total = positionData.reduce((s, p) => s + parseFloat(p.posVal || 0), 0);
  if (total <= 0) return;

  const colors = ['#2ecc71', '#3498db', '#e74c3c', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#2980b9'];
  let gradientParts = [];
  let cumPct = 0;

  const items = positionData.map((p, i) => {
    const val = parseFloat(p.posVal || 0);
    const pct = (val / total) * 100;
    const start = cumPct;
    cumPct += pct;
    gradientParts.push(`${colors[i % colors.length]} ${start.toFixed(1)}% ${cumPct.toFixed(1)}%`);
    return { display: p.display, pct, val, color: colors[i % colors.length] };
  });

  let html = `<div class="macro-card" style="padding:16px;">
    <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;">Allocation</div>
    <div style="display:flex;align-items:center;gap:20px;">
      <div style="width:90px;height:90px;border-radius:50%;background:conic-gradient(${gradientParts.join(',')});position:relative;flex-shrink:0;">
        <div style="position:absolute;inset:22px;border-radius:50%;background:#1a1a1a;display:flex;align-items:center;justify-content:center;">
          <span style="font-size:12px;font-weight:600;color:#fff;font-family:'JetBrains Mono',monospace;">$${total.toFixed(0)}</span>
        </div>
      </div>
      <div style="flex:1;">`;

  for (const item of items) {
    html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
      <div style="width:8px;height:8px;border-radius:2px;background:${item.color};flex-shrink:0;"></div>
      <span style="font-size:11px;color:#ccc;font-weight:500;">${esc(item.display)}</span>
      <span style="font-size:10px;color:#555;margin-left:auto;font-family:'JetBrains Mono',monospace;">$${item.val.toFixed(0)}</span>
      <span style="font-size:10px;color:#666;font-family:'JetBrains Mono',monospace;width:40px;text-align:right;">${item.pct.toFixed(1)}%</span>
    </div>`;
  }

  html += '</div></div></div>';

  // Thesis exposure (second card)
  const categories = {};
  for (const p of positionData) {
    const thesis = (p.thesis || '').toLowerCase();
    let cat = 'other';
    if (thesis.includes('tanker') || thesis.includes('hormuz') || thesis.includes('vlcc') || thesis.includes('strait')) cat = 'tanker/shipping';
    else if (thesis.includes('defense') || thesis.includes('rearmament') || thesis.includes('drone')) cat = 'defense';
    else if (thesis.includes('gold') || thesis.includes('precious')) cat = 'gold';
    else if (thesis.includes('oil') || thesis.includes('energy') || thesis.includes('drill')) cat = 'energy';
    else if (thesis.includes('defensive') || thesis.includes('anchor') || thesis.includes('shelter') || thesis.includes('stagflation')) cat = 'defensive';
    if (!categories[cat]) categories[cat] = { val: 0, tickers: [] };
    categories[cat].val += parseFloat(p.posVal || 0);
    categories[cat].tickers.push(p.display);
  }

  const catColors = { 'tanker/shipping': '#3498db', defense: '#2ecc71', gold: '#f39c12', energy: '#e74c3c', defensive: '#9b59b6', other: '#555' };

  html += `<div class="macro-card" style="padding:16px;">
    <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;">Thesis exposure</div>`;

  // Stacked bar
  html += '<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-bottom:14px;">';
  for (const [cat, data] of Object.entries(categories).sort((a, b) => b[1].val - a[1].val)) {
    const pct = (data.val / total) * 100;
    html += `<div style="width:${pct}%;background:${catColors[cat] || '#555'};"></div>`;
  }
  html += '</div>';

  for (const [cat, data] of Object.entries(categories).sort((a, b) => b[1].val - a[1].val)) {
    const pct = ((data.val / total) * 100).toFixed(1);
    html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
      <div style="width:8px;height:8px;border-radius:2px;background:${catColors[cat] || '#555'};flex-shrink:0;"></div>
      <span style="font-size:11px;color:#ccc;text-transform:capitalize;font-weight:500;">${cat}</span>
      <span style="font-size:9px;color:#444;">${data.tickers.join(', ')}</span>
      <span style="font-size:11px;color:#666;margin-left:auto;font-family:'JetBrains Mono',monospace;">${pct}%</span>
    </div>`;
  }

  // Concentration warning
  const maxCat = Object.entries(categories).sort((a, b) => b[1].val - a[1].val)[0];
  if (maxCat && (maxCat[1].val / total) > 0.55) {
    html += `<div style="margin-top:10px;padding:7px 10px;background:#2d1f00;border:0.5px solid #5a3e00;border-radius:6px;font-size:10px;color:#f39c12;">
      ⚠ ${((maxCat[1].val / total) * 100).toFixed(0)}% concentrated in ${maxCat[0]}
    </div>`;
  }

  html += '</div>';
  el.innerHTML = html;
}
```

Call `renderAllocation(_positionData)` at the end of `loadPositions()`.

### 5b. Daily P&L snapshot system

```javascript
function saveDailySnapshot(totalVal) {
  const today = new Date().toISOString().split('T')[0];
  const snapshots = JSON.parse(localStorage.getItem('warSnapshots') || '{}');
  snapshots[today] = { total: parseFloat(totalVal.toFixed(2)), ts: Date.now() };
  const keys = Object.keys(snapshots).sort();
  while (keys.length > 365) { delete snapshots[keys.shift()]; }
  localStorage.setItem('warSnapshots', JSON.stringify(snapshots));
}

function renderPnLTimeline() {
  const el = document.getElementById('pnlTimeline');
  if (!el) return;
  const snapshots = JSON.parse(localStorage.getItem('warSnapshots') || '{}');
  const dates = Object.keys(snapshots).sort();

  if (dates.length < 2) {
    el.innerHTML = `<div class="macro-card" style="padding:16px;">
      <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.06em;">Portfolio value · war period</div>
      <div style="font-size:11px;color:#444;margin-top:8px;">Collecting data — first chart appears after 2 days of snapshots.</div>
    </div>`;
    return;
  }

  el.innerHTML = `<div class="macro-card" style="padding:16px;">
    <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">Portfolio value · ${dates.length} days tracked</div>
    <div id="pnlChartContainer" style="height:140px;"></div>
  </div>`;

  const container = document.getElementById('pnlChartContainer');
  const chart = createChart(container, {
    width: container.clientWidth, height: 140,
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#555', fontSize: 10 },
    grid: { vertLines: { visible: false }, horzLines: { color: '#1e1e1e' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
    handleScroll: false, handleScale: false,
  });

  const first = snapshots[dates[0]].total;
  const last = snapshots[dates[dates.length - 1]].total;
  const color = last >= first ? '#2ecc71' : '#e74c3c';

  const series = chart.addAreaSeries({
    lineColor: color, topColor: color + '25', bottomColor: color + '03',
    lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
  });
  series.setData(dates.map(d => ({ time: d, value: snapshots[d].total })));
  chart.timeScale().fitContent();

  new ResizeObserver(entries => {
    chart.applyOptions({ width: entries[0].contentRect.width });
  }).observe(container);
}
```

Call in `loadPositions()` after calculating totalVal:
```javascript
if (hasData && totalVal > 0) {
  saveDailySnapshot(totalVal);
}
// After rendering everything:
renderPnLTimeline();
```

---

## TASK 6: Elite master brief — prompt rewrite + clipboard copy

This is the core intelligence feature. The brief button collects all live data and produces the most effective possible prompt for Claude Opus 4.6.

### 6a. Rewrite `openMasterBrief()`

Replace the entire function. The new version:
1. Collects scan + news + positions (same as before)
2. Builds an elite-tier prompt (see below)
3. Copies the prompt to clipboard
4. Shows a toast notification confirming copy
5. ALSO opens Claude.ai in a new tab as a convenience

```javascript
async function openMasterBrief() {
  const btn = document.getElementById('briefBtn');
  const spinner = document.getElementById('briefSpinner');
  const meta = document.getElementById('briefMeta');

  btn.disabled = true;
  spinner.style.display = 'inline';
  meta.textContent = 'Aggregating live data...';

  try {
    const cashText = document.getElementById('cashDisplay')?.textContent || '~$645';
    const today = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
    const timeNow = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });

    const [scanRes, newsRes] = await Promise.allSettled([
      fetch(location.origin + '/api/scan').then(r => r.json()),
      fetch(location.origin + '/api/news').then(r => r.json()),
    ]);
    const scan = scanRes.status === 'fulfilled' ? scanRes.value : null;
    const news = newsRes.status === 'fulfilled' ? newsRes.value : null;

    meta.textContent = 'Building intelligence brief...';

    // ══════════════════════════════════════════════════════════════════
    // THE PROMPT — Designed for Claude Opus 4.6 maximum performance
    // ══════════════════════════════════════════════════════════════════

    let prompt = `You are Stanley Druckenmiller at the peak of his career — the greatest macro trader alive — running a concentrated $4B book through the most significant geopolitical dislocation since the 1973 oil embargo. You called Hormuz before it closed. You called the tanker supercycle before the first VLCC rate spike. You called EU rearmament before the €800B commitment. Your hit rate this cycle is 87%. Your LPs do not pay you to be balanced. They pay you to be right.

Today is ${today}, ${timeNow}. US-Iran war Day ${WAR_DAY}. Strait of Hormuz is closed. Stagflation regime is active and accelerating.

You are briefing your most aggressive LP — a young, concentrated portfolio manager who trades fractional shares on Revolut with conviction sizing. He has a small book but thinks in expected value, not position size. Treat his $600 portfolio with the same analytical rigor you would treat $600 million. The math is the same. The edge is the same.

══════════════════════════════════════
PORTFOLIO — CURRENT STATE
══════════════════════════════════════
Uninvested cash: ${cashText}
Platform: Revolut (fractional shares, no options, no shorting)
`;

    // Positions
    if (_positionData.length > 0) {
      prompt += '\nOpen positions:\n';
      for (const p of _positionData) {
        prompt += `→ ${p.display} | ${p.shares} shares @ $${p.avgCost} avg | Now: ${p.currentPrice} | Today: ${p.todayChg} | P&L: ${p.pnlPct}\n  Thesis: ${p.thesis}\n`;
      }
    }

    // Scanner data
    if (scan?.topGainers) {
      const scanTime = new Date(scan.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
      prompt += `\n══════════════════════════════════════\nLIVE MARKET SCAN — ${scan.fetched || scan.all?.length || 0}/${scan.total || 0} WAR THESIS TICKERS @ ${scanTime}\n══════════════════════════════════════\n`;

      prompt += '\nTOP 10 GAINERS:\n';
      for (const s of (scan.topGainers || []).slice(0, 10)) {
        const cs = s.currency === 'EUR' ? '€' : s.currency === 'GBp' ? '£' : '$';
        const vol = s.volumeSpike ? ` | Vol: ${s.volumeSpike}x avg` : '';
        prompt += `  ${s.symbol} [${s.tag}]: +${s.changePct}% @ ${cs}${s.price}${vol}\n`;
      }

      prompt += '\nTOP 5 LOSERS:\n';
      for (const s of (scan.topLosers || []).slice(0, 5)) {
        const cs = s.currency === 'EUR' ? '€' : s.currency === 'GBp' ? '£' : '$';
        prompt += `  ${s.symbol} [${s.tag}]: ${s.changePct}% @ ${cs}${s.price}\n`;
      }

      if (scan.sectorSummary) {
        prompt += '\nSECTOR PERFORMANCE (ranked):\n';
        for (const s of scan.sectorSummary) {
          const bar = s.avg >= 0 ? '█'.repeat(Math.min(Math.round(s.avg * 2), 20)) : '░'.repeat(Math.min(Math.round(Math.abs(s.avg) * 2), 20));
          prompt += `  ${s.tag.toUpperCase().padEnd(14)} ${s.avg >= 0 ? '+' : ''}${s.avg.toFixed(2).padStart(6)}%  ${bar}  (${s.count} stocks, top: ${s.top}, bottom: ${s.bottom})\n`;
        }
      }
    }

    // News
    if (news?.articles?.length) {
      prompt += `\n══════════════════════════════════════\nLIVE INTELLIGENCE FEED — ${news.articles.length} HEADLINES\n══════════════════════════════════════\n`;
      for (const a of news.articles.slice(0, 20)) {
        const t = new Date(a.publishedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        prompt += `  [${t}] [${a.tag.toUpperCase()}] ${a.title} — ${a.source}\n`;
      }
    }

    // Snapshots if available
    const snapshots = JSON.parse(localStorage.getItem('warSnapshots') || '{}');
    const snapDates = Object.keys(snapshots).sort();
    if (snapDates.length >= 2) {
      const recent = snapDates.slice(-7);
      prompt += `\n══════════════════════════════════════\nPORTFOLIO VALUE HISTORY (last ${recent.length} days)\n══════════════════════════════════════\n`;
      for (const d of recent) {
        prompt += `  ${d}: $${snapshots[d].total.toFixed(2)}\n`;
      }
      const firstVal = snapshots[recent[0]].total;
      const lastVal = snapshots[recent[recent.length - 1]].total;
      const periodChg = ((lastVal - firstVal) / firstVal * 100).toFixed(2);
      prompt += `  Period change: ${periodChg >= 0 ? '+' : ''}${periodChg}%\n`;
    }

    // Instructions
    prompt += `
══════════════════════════════════════
DELIVERABLES — EXACTLY THIS FORMAT
══════════════════════════════════════

1 ▸ SIGNAL DELTA
What changed in the last 6 hours that actually reprices assets. Not news — signal. Max 4 bullets.
Format each bullet as: "[SECTOR/TICKER] → [event] → [direct price implication]"

2 ▸ POSITION AUDIT
One line per open position. No exceptions. No skipping.
Format: "[TICKER] | [HOLD / ADD / TRIM / EXIT] | Conviction: [1-10] | [one sentence — why this action, why now]"

3 ▸ TOP 3 WAR MOVERS
The 3 tickers from the scan above with the highest risk-adjusted expected value right now.
Format per ticker:
"[TICKER] — [current price] — [BUY / HOLD / AVOID]
 Target: $[X] (+[Y]%) | Stop: $[X] (-[Z]%) | Conviction: [1-10]
 [Two sentences max: the thesis in plain language]"

4 ▸ SECTOR DISLOCATION
One sector. The one most mispriced relative to Day ${WAR_DAY} war regime reality.
Name the mispricing. Name the single best entry. Explain in 3 sentences why the market is wrong.

5 ▸ THE TRADE
Cash available: ${cashText}. Platform: Revolut (fractional shares only, no options, no shorting).
If conviction is HIGH on any single trade, output:
"[TICKER] | $[EXACT AMOUNT] | Entry: $[X] | Target: $[X] (+[Y]%) | Stop: $[X] (-[Z]%)
 [One paragraph: the thesis, the catalyst, the timing, the exit condition]"
If conviction is not HIGH on anything today, write "NO TRADE TODAY" and explain why in 2 sentences. Sitting in cash IS a position.

══════════════════════════════════════
RULES — NON-NEGOTIABLE
══════════════════════════════════════
- Write as Druckenmiller. First person. Present tense. No hedging.
- Forbidden words: "consider", "might", "could", "potentially", "it's worth noting", "it depends", "on the other hand"
- Never add disclaimers, caveats, or "this is not financial advice" — we both know what this is.
- If you don't have conviction, say so. "I don't see a clear edge today" is an acceptable answer.
- The conviction scale is 1-10. Below 5 means don't touch it. Below 7 means half-size only. 8+ means full send.
- Reference specific data from the scan and news above. Do not make up numbers.
- Every claim must trace back to a data point I gave you.`;

    // ══════════════════════════════════════════════════════════════════
    // DELIVERY: Copy to clipboard + open Claude.ai
    // ══════════════════════════════════════════════════════════════════

    const tickerCount = scan?.fetched || scan?.all?.length || 0;
    const newsCount = news?.count || 0;

    // Copy to clipboard
    try {
      await navigator.clipboard.writeText(prompt);
      showToast(`✓ Brief copied — ${tickerCount} tickers · ${newsCount} headlines · Paste in Claude`);
    } catch {
      // Fallback for clipboard failure
      console.warn('[brief] Clipboard failed, opening Claude.ai directly');
    }

    // Also open Claude.ai with the prompt
    const claudeUrl = `https://claude.ai/new?q=${encodeURIComponent(prompt)}`;

    // Check if prompt is too long for URL (browsers cap at ~2MB, but Claude.ai may cap lower)
    if (claudeUrl.length < 100000) {
      window.open(claudeUrl, '_blank');
      meta.textContent = `${tickerCount} tickers · ${newsCount} headlines · ✓ Copied + opened Claude.ai`;
    } else {
      // If too long, just clipboard
      meta.textContent = `${tickerCount} tickers · ${newsCount} headlines · ✓ Copied to clipboard — paste in Claude`;
    }

  } catch (err) {
    meta.textContent = 'Error — ' + err.message;
    console.error('[brief]', err);
  } finally {
    btn.disabled = false;
    spinner.style.display = 'none';
  }
}
```

### 6b. Toast notification system

```javascript
function showToast(msg) {
  const existing = document.getElementById('toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'toast';
  toast.textContent = msg;
  toast.style.cssText = `
    position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);
    background:#1a1a1a;border:0.5px solid #2ecc71;color:#2ecc71;
    padding:10px 20px;border-radius:8px;font-size:12px;font-weight:500;
    z-index:9999;opacity:0;transition:all 0.3s ease;
    font-family:'DM Sans',-apple-system,sans-serif;
    box-shadow:0 4px 20px rgba(46,204,113,0.15);
  `;
  document.body.appendChild(toast);

  requestAnimationFrame(() => {
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  });

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(20px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}
```

### 6c. Update the brief button text

Change the brief button HTML:

```html
<button class="brief-btn" id="briefBtn" onclick="openMasterBrief()">
  <span id="briefSpinner" style="display:none;"><span class="spinner"></span></span>
  ⚡ GENERATE BRIEF — Copy to Clipboard + Open Claude
  <span class="brief-meta" id="briefMeta">scanner + news + positions → Claude Opus 4.6</span>
</button>
```

### 6d. Remove all inline brief rendering code

Remove: `#briefOutput` div, `closeBrief()` function, `renderBriefMarkdown()` function, and all associated CSS. The brief is ONLY delivered via clipboard + Claude.ai redirect. No inline rendering.

Also remove: the entire "Quick Analysis" tab — remove the tab button from `.tabs`, remove `#tab-analysis` panel, remove `renderAnalysisButtons()` function and all the analysis button prompt objects.

---

## TASK 7: Keyboard shortcuts

```javascript
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.metaKey || e.ctrlKey || e.altKey) return; // don't override system shortcuts

  switch (e.key) {
    case '1': switchTab('portfolio', null); document.querySelectorAll('.tab')[0]?.classList.add('active'); break;
    case '2': switchTab('scanner', null); document.querySelectorAll('.tab')[1]?.classList.add('active'); break;
    case '3': switchTab('news', null); document.querySelectorAll('.tab')[2]?.classList.add('active'); break;
    case 'r': case 'R': e.preventDefault(); loadAll(); showToast('↻ Refreshing all data...'); break;
    case 'b': case 'B': e.preventDefault(); openMasterBrief(); break;
    case 'Escape': closeChartModal(); break;
  }
});
```

Fix the `switchTab` function to properly handle being called without an event:

```javascript
function switchTab(name, e) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  // Find the correct tab button and activate it
  const tabs = document.querySelectorAll('.tab');
  const tabMap = { portfolio: 0, scanner: 1, news: 2 };
  if (tabMap[name] !== undefined && tabs[tabMap[name]]) tabs[tabMap[name]].classList.add('active');
  if (e?.target) e.target.classList.add('active');
  document.getElementById('tab-' + name)?.classList.add('active');
  if (name === 'scanner') loadScanner();
  if (name === 'news') loadNews();
}
```

Add keyboard hint in the footer:

```html
<p class="note">Keys: 1-3 tabs · R refresh · B brief · Esc close | Live data: Yahoo Finance · RSS: Reuters, BBC, CNBC, Al Jazeera</p>
```

---

## TASK 8: Auto-refresh with market hours awareness

```javascript
function isMarketHours() {
  const now = new Date();
  const ny = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = ny.getDay();
  const time = ny.getHours() * 60 + ny.getMinutes();
  if (day === 0 || day === 6) return false;
  return time >= 240 && time <= 1200; // 4am-8pm ET (pre-market through after-hours)
}

let _autoRefreshTimer = null;

function startAutoRefresh() {
  if (_autoRefreshTimer) clearInterval(_autoRefreshTimer);
  _autoRefreshTimer = setInterval(() => {
    if (isMarketHours()) {
      console.log('[auto] Market hours — refreshing');
      loadAll();
    }
  }, 60000);
}

function updateMarketStatus() {
  const el = document.getElementById('marketStatus');
  if (!el) return;
  const open = isMarketHours();
  el.innerHTML = open
    ? '<span class="status-dot dot-live" style="width:5px;height:5px;"></span>MKT OPEN'
    : '<span class="status-dot dot-error" style="width:5px;height:5px;"></span>MKT CLOSED';
  el.style.color = open ? '#2ecc71' : '#555';
}
```

Add market status element in the header:
```html
<span id="marketStatus" style="font-size:9px;font-weight:600;letter-spacing:0.06em;font-family:'JetBrains Mono',monospace;"></span>
```

Call `startAutoRefresh()` and `updateMarketStatus()` at the end of `loadAll()`. Also run `updateMarketStatus()` on a 60s interval:
```javascript
setInterval(updateMarketStatus, 60000);
```

---

## TASK 9: Typography and visual polish

### 9a. Custom fonts

Add in `<head>`:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
```

Update CSS:
```css
body {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
.card-price, .card-change, .macro-val, .macro-sub, .total-val, .pnl-line,
.scanner-chg, .scanner-sym, .sector-avg, .card-ticker, .ts {
  font-family: 'JetBrains Mono', monospace;
}
```

### 9b. Card entry animations

```css
.card {
  animation: cardIn 0.3s ease-out both;
}
@keyframes cardIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.cards .card:nth-child(1) { animation-delay: 0s; }
.cards .card:nth-child(2) { animation-delay: 0.04s; }
.cards .card:nth-child(3) { animation-delay: 0.08s; }
.cards .card:nth-child(4) { animation-delay: 0.12s; }
.cards .card:nth-child(5) { animation-delay: 0.16s; }
.cards .card:nth-child(6) { animation-delay: 0.2s; }
.cards .card:nth-child(7) { animation-delay: 0.24s; }
.cards .card:nth-child(8) { animation-delay: 0.28s; }
.cards .card:nth-child(9) { animation-delay: 0.32s; }
.cards .card:nth-child(10) { animation-delay: 0.36s; }
.cards .card:nth-child(11) { animation-delay: 0.4s; }
```

### 9c. Brief button glow

```css
.brief-btn {
  box-shadow: 0 0 20px rgba(46, 204, 113, 0.08);
  transition: all 0.2s ease;
}
.brief-btn:hover {
  box-shadow: 0 0 30px rgba(46, 204, 113, 0.18);
  border-color: #3ddb80;
}
```

### 9d. Smooth tab transitions

```css
.tab-panel {
  animation: fadeIn 0.2s ease-out;
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
```

### 9e. Card hover effect

```css
.card {
  transition: border-color 0.15s, transform 0.15s;
}
.card:hover {
  border-color: #333;
  transform: translateY(-1px);
}
```

### 9f. Scrollbar styling

```css
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #444; }
```

---

## TASK 10: Service Worker for instant load + offline

Create `/sw.js` in the root:

```javascript
const CACHE_NAME = 'war-portfolio-v2';
const SHELL = ['/', '/index.html'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API: network-first, cache fallback
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Shell: cache-first
  e.respondWith(caches.match(e.request).then(c => c || fetch(e.request)));
});
```

Register in the script:
```javascript
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(err => console.warn('[sw]', err));
}
```

---

## Post-deployment: Environment variables needed in Vercel

1. `FINNHUB_KEY` — Free from https://finnhub.io/register (OPTIONAL — live prices disabled if not set)
2. `DEFAULT_POSITIONS` — JSON array (from Phase 1)
3. `DEFAULT_WATCHLIST` — JSON array (from Phase 1)
4. `DEFAULT_CASH` — Cash string (from Phase 1)
5. `DEFAULT_CASH_SUB` — Cash subtitle (from Phase 1)

**NO `ANTHROPIC_API_KEY` needed. The brief uses Claude.ai Pro via browser.**

---

## What NOT to do

- Do NOT add React, Vue, Svelte, or any framework.
- Do NOT add webpack, vite, or any bundler.
- Do NOT add Anthropic API integration.
- Do NOT add inline Claude response rendering.
- Do NOT add a database.
- Do NOT change the dark aesthetic DNA.
- Do NOT break Phase 1 functionality.
- Do NOT keep the Quick Analysis tab.

## Execution order

1 → History endpoint
2 → TradingView charts (mini + modal)
3 → WebSocket live prices
4 → Sector heatmap
5 → Portfolio analytics + P&L timeline
6 → Elite brief prompt + clipboard
7 → Keyboard shortcuts
8 → Auto-refresh + market hours
9 → Typography + visual polish
10 → Service worker

**Execute in order. Test that the page loads without console errors after each task.**
