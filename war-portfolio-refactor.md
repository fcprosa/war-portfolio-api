# War Portfolio API — Full Refactor Execution Plan

## Context

You are refactoring a Vercel-deployed personal portfolio dashboard. The repo is at `https://github.com/fcprosa/war-portfolio-api`. Clone it first, then execute every task below **in order**. Do NOT skip steps. Do NOT ask questions — execute autonomously.

The project structure is:
```
/
├── api/
│   ├── brief.js    — aggregates data into a Claude prompt
│   ├── news.js     — RSS feed aggregator
│   ├── quote.js    — single Yahoo Finance quote proxy
│   └── scan.js     — 110+ ticker market scanner
├── index.html      — monolith frontend (900+ lines)
└── vercel.json     — (may or may not exist)
```

Tech: Vercel serverless functions (Node.js), vanilla JS frontend, Yahoo Finance v8 chart API, RSS feeds.

---

## TASK 1: Extract shared logic into `/lib/` (eliminate circular self-calls)

**Problem:** `api/brief.js` calls its own deployed URL (`https://war-portfolio-api.vercel.app/api/scan` and `/api/news`) via HTTP — the backend calls itself. This wastes 2 cold starts + network round trips.

**Action:**

1. Create `/lib/scanner.js` — extract the core scanning logic from `api/scan.js`:
   - Move the `UNIVERSE` array, `fetchOne()`, `sleep()`, and the main scanning logic into an exported `async function runScan()` that returns the same JSON shape.
   - Keep `api/scan.js` as a thin HTTP handler that imports `runScan()` and returns its result.

2. Create `/lib/news.js` — extract from `api/news.js`:
   - Move `RSS_FEEDS`, `WAR_KEYWORDS`, `tagArticle()`, `isRelevant()`, `parseRSSDate()`, `extractItems()`, `fetchFeed()` and the main aggregation logic into an exported `async function fetchAllNews()`.
   - Keep `api/news.js` as a thin handler.

3. Create `/lib/utils.js`:
   - Move `getWarDay()` here (currently duplicated in `api/brief.js` and `index.html`). Export it.

4. Update `api/brief.js` to import directly:
   ```javascript
   import { runScan } from '../lib/scanner.js';
   import { fetchAllNews } from '../lib/news.js';
   import { getWarDay } from '../lib/utils.js';
   ```
   Remove the HTTP fetch calls to self.

5. Update `api/scan.js` and `api/news.js` to import from their respective lib files.

---

## TASK 2: Batch Yahoo Finance quotes (110 requests → ~6 requests)

**Problem:** `scan.js` makes 110+ individual HTTP requests to Yahoo Finance. This causes timeouts on Vercel's 10s limit and risks rate limiting.

**Action:**

In `/lib/scanner.js`, replace the one-by-one `fetchOne()` approach with a batch quote endpoint:

```javascript
async function fetchBatch(symbols) {
  const encoded = symbols.map(s => encodeURIComponent(s)).join(',');
  const urls = [
    `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encoded}`,
    `https://query2.finance.yahoo.com/v7/finance/quote?symbols=${encoded}`,
  ];
  for (const url of urls) {
    try {
      const r = await fetch(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'application/json',
          'Referer': 'https://finance.yahoo.com',
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!r.ok) continue;
      const d = await r.json();
      const results = d?.quoteResponse?.result || [];
      return results.map(q => ({
        symbol: q.symbol,
        price: q.regularMarketPrice,
        chgPct: q.regularMarketChangePercent,
        currency: q.currency,
        volume: q.regularMarketVolume,
        avgVolume: q.averageDailyVolume3Month,
      }));
    } catch (err) {
      console.error(`[scan] batch fetch failed:`, err.message);
    }
  }
  return [];
}
```

Then in `runScan()`, batch UNIVERSE into chunks of 20 symbols:

```javascript
const BATCH_SIZE = 20;
const allQuotes = [];
for (let i = 0; i < UNIVERSE.length; i += BATCH_SIZE) {
  const batch = UNIVERSE.slice(i, i + BATCH_SIZE);
  const symbols = batch.map(b => b.sym);
  const quotes = await fetchBatch(symbols);
  allQuotes.push(...quotes);
  if (i + BATCH_SIZE < UNIVERSE.length) await sleep(150);
}
```

Map the quote results back to the UNIVERSE metadata (tag, name) by matching on symbol.

Also add a `volumeSpike` calculation:
```javascript
volumeSpike: q.volume && q.avgVolume ? parseFloat((q.volume / q.avgVolume).toFixed(1)) : null,
```

Keep the old `fetchOne()` as a **fallback** — if the batch endpoint fails (Yahoo sometimes blocks v7), fall back to the existing v8 chart approach but with `BATCH_SIZE = 20` and reduced sleep.

Also update `/api/quote.js` to try the v7 endpoint first as primary, v8 as fallback.

---

## TASK 3: Parallelize client-side loads (20s → 2-3s)

**Problem:** `index.html` loads positions, watchlist, and macro quotes sequentially with `for...await` loops. Each quote is a separate HTTP request waited on individually.

**Action:**

In the `<script>` section of `index.html` (or the new modular JS files if you've split them), refactor these three functions:

### `loadPositions()`:
Change from:
```javascript
for (const p of positions) {
  const q = await getQuote(p.sym);
  // ...process
}
```
To:
```javascript
const quotes = await Promise.allSettled(
  positions.map(p => getQuote(p.sym).then(q => ({ pos: p, quote: q })))
);
for (const result of quotes) {
  if (result.status === 'fulfilled') {
    const { pos: p, quote: q } = result.value;
    // ...same processing logic
  } else {
    // render error card for this position
  }
}
```

### `loadWatchlist()`:
Same pattern — `Promise.allSettled()` on all watchlist items.

### `loadMacro()`:
Same pattern — `Promise.allSettled()` on all 6 macro symbols.

The outer `loadAll()` already uses `Promise.all([loadPositions(), loadWatchlist(), loadMacro()])` which is correct — keep that.

---

## TASK 4: XSS sanitization

**Problem:** RSS feed titles and news content are injected directly into `innerHTML` without escaping. Malicious RSS content could execute JavaScript.

**Action:**

Add this utility function near the top of the `<script>` block:

```javascript
function esc(str) {
  const el = document.createElement('div');
  el.textContent = str || '';
  return el.innerHTML;
}
```

Then find and replace ALL instances where external data goes into `innerHTML` templates. Specifically:

- `renderNews()`: `${a.title}` → `${esc(a.title)}`, `${a.source}` → `${esc(a.source)}`
- `renderScanner()`: `${s.symbol}` → `${esc(s.symbol)}`, `${s.name}` → `${esc(s.name)}`, `${s.tag}` → `${esc(s.tag)}`
- `loadPositions()`: `${p.display}` → `${esc(p.display)}`, `${p.name}` → `${esc(p.name)}`, `${p.thesis}` → `${esc(p.thesis)}`
- `loadWatchlist()`: same fields as positions
- Any other place where data from API responses or localStorage enters innerHTML.

Do NOT escape values that are already numbers or computed strings (prices, percentages, etc.) — only user-facing text fields.

---

## TASK 5: Extract personal data from public repo

**Problem:** `index.html` has hardcoded portfolio positions with exact share counts, average costs, cash balances, and strategy notes — all publicly visible on GitHub.

**Action:**

1. Create a file `/api/config.js` that serves the default configuration from environment variables:

```javascript
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  // These come from Vercel environment variables
  // Fallback to empty/safe defaults if not set
  const config = {
    positions: JSON.parse(process.env.DEFAULT_POSITIONS || '[]'),
    watchlist: JSON.parse(process.env.DEFAULT_WATCHLIST || '[]'),
    cash: process.env.DEFAULT_CASH || '~$0',
    cashSub: process.env.DEFAULT_CASH_SUB || '',
  };

  return res.status(200).json(config);
}
```

2. In `index.html`, replace the hardcoded `DEFAULT_POSITIONS`, `DEFAULT_WATCHLIST`, cash display values, and `cashSub` text with a fetch to `/api/config` on startup:

```javascript
let DEFAULT_POSITIONS = [];
let DEFAULT_WATCHLIST = [];

async function loadConfig() {
  try {
    const r = await fetch('https://war-portfolio-api.vercel.app/api/config');
    const cfg = await r.json();
    DEFAULT_POSITIONS = cfg.positions || [];
    DEFAULT_WATCHLIST = cfg.watchlist || [];
    if (cfg.cash) document.getElementById('cashDisplay').textContent = cfg.cash;
    if (cfg.cashSub) document.getElementById('cashSub').textContent = cfg.cashSub;
  } catch (e) {
    console.error('[config] Failed to load:', e.message);
  }
}
```

Call `loadConfig()` before `loadAll()` in the startup flow.

3. Create a file called `.env.example` with the structure (no real values):

```
DEFAULT_POSITIONS=[{"sym":"TICKER","display":"TICKER","name":"Company Name","shares":0,"avgCost":0,"badge":"hold","thesis":"Your thesis"}]
DEFAULT_WATCHLIST=[{"sym":"TICKER","display":"TICKER","name":"Company Name","badge":"watch","thesis":"Your thesis","custom":false}]
DEFAULT_CASH=~$0
DEFAULT_CASH_SUB=Description of cash allocation
```

4. Add to `.gitignore`:
```
.env
.env.local
.vercel
node_modules/
```

5. The user will need to set these env vars in Vercel dashboard manually. Add a comment at the top of `api/config.js` explaining this.

---

## TASK 6: Error logging (replace silent catch blocks)

**Problem:** Multiple `catch {}` blocks swallow errors silently. When Yahoo fails, there's no way to debug.

**Action:**

Find every `catch {}` or `catch { }` in ALL files and replace with proper logging:

- In `/lib/scanner.js` (formerly `api/scan.js` logic): `catch (err) { console.error(\`[scan] ${sym} failed:\`, err.message); }`
- In `/api/quote.js`: `catch (err) { console.error(\`[quote] ${symbol} failed:\`, err.message); }`
- In `/lib/news.js`: `catch (err) { console.error(\`[news] ${feed.source} failed:\`, err.message); }`
- In `index.html` JS: `catch (err) { console.warn(\`[ui] quote failed:\`, err.message); }` (use warn for client-side, not error)

Every catch must log at minimum the context (which module/function) and `err.message`.

---

## TASK 7: CORS tightening

**Problem:** All API endpoints have `Access-Control-Allow-Origin: *`. Anyone can use the Vercel deployment as a free Yahoo Finance proxy.

**Action:**

In every API handler (`api/quote.js`, `api/scan.js`, `api/news.js`, `api/brief.js`, `api/config.js`), replace:

```javascript
res.setHeader('Access-Control-Allow-Origin', '*');
```

With:

```javascript
const origin = req.headers.origin || '';
const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
if (allowed.includes(origin)) {
  res.setHeader('Access-Control-Allow-Origin', origin);
}
```

This way only your own frontend (and local dev) can call the APIs.

---

## TASK 8: Remove dead code

**Action:**

1. Remove `renderBriefMarkdown()` function from `index.html` — it's defined but never called (brief now opens in Claude.ai via URL).

2. Remove the `#briefOutput` div and all associated HTML/CSS since inline brief rendering is dead code (everything goes to Claude.ai now).

3. Remove `closeBrief()` function.

4. Clean up any CSS classes that are only used by the removed brief output section (`.brief-header`, `.brief-close`, `#briefText`, `.brief-cursor`, `.section-h` inside `#briefText`).

---

## TASK 9: Client-side caching for scan and news

**Problem:** Every tab switch to Scanner triggers a full 110-ticker rescan. Every tab switch to News refetches all RSS feeds.

**Action:**

Add a simple in-memory cache with TTL:

```javascript
const _cache = {};
function getCached(key, ttlMs) {
  const entry = _cache[key];
  if (entry && (Date.now() - entry.ts) < ttlMs) return entry.data;
  return null;
}
function setCache(key, data) {
  _cache[key] = { data, ts: Date.now() };
}
```

Then in `loadScanner()`:
```javascript
async function loadScanner() {
  const cached = getCached('scan', 5 * 60 * 1000); // 5 min TTL
  if (cached) { renderScanner(cached, el); return; }
  // ... existing fetch logic ...
  setCache('scan', data);
  renderScanner(data, el);
}
```

Same pattern for `loadNews()` with a 5-minute TTL.

Add a "force refresh" that bypasses cache when the ↻ button is clicked:

```javascript
async function loadScanner(force = false) {
  if (!force) {
    const cached = getCached('scan', 5 * 60 * 1000);
    if (cached) { renderScanner(cached, el); return; }
  }
  // ...
}
```

Wire the refresh button: `onclick="loadScanner(true)"` and `onclick="loadNews(true)"`.

---

## TASK 10: Add volume spike data to scanner display

**Problem:** The scan data now includes `volumeSpike` from the batch endpoint (Task 2) but the scanner UI doesn't show it.

**Action:**

In `renderScanner()`, for each scanner row, add the volume spike indicator when > 2x:

```javascript
const volBadge = s.volumeSpike && s.volumeSpike > 2
  ? `<span style="color:#f39c12;font-size:9px;font-weight:700;">${s.volumeSpike}x vol</span>`
  : '';
```

Add it to the row HTML after the tag badge.

---

## Final checks

After all tasks are complete:

1. Run `node -c api/quote.js && node -c api/scan.js && node -c api/news.js && node -c api/brief.js` to syntax-check all API files.
2. Verify the `lib/` imports work: `node -e "import('./lib/scanner.js').then(m => console.log('scanner OK')).catch(e => console.error(e))"` (adjust for ESM/CJS as needed based on what the project uses).
3. Open `index.html` in a browser and verify no console errors on load.
4. Make sure `.gitignore` exists and includes `.env`, `.env.local`, `.vercel`, `node_modules/`.

---

## What NOT to do

- Do NOT convert to TypeScript — this is a personal tool, JS is fine.
- Do NOT add React, Vue, or any framework — vanilla JS is the right call here.
- Do NOT add a build step (webpack, vite, etc.) — the zero-build Vercel deploy is a feature.
- Do NOT add a database — localStorage + env vars is sufficient for one user.
- Do NOT restructure the Vercel serverless function conventions (`/api/*.js` handler pattern).
- Do NOT change the visual design or CSS aesthetic — it's already good.
