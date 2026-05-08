// /lib/portwatch.js — IMF PortWatch Hormuz transit data via MacroMicro mirror

const MACROMICRO_URL = 'https://en.macromicro.me/charts/data/94482';
const CHART_REFERER = 'https://en.macromicro.me/charts/94482/imf-strait-of-hormuz-number-of-ships-and-transit-volume';

async function fetchMacroMicro() {
  try {
    const r = await fetch(MACROMICRO_URL, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': CHART_REFERER,
        'X-Requested-With': 'XMLHttpRequest',
      },
      signal: AbortSignal.timeout(10000),
    });
    if (!r.ok) {
      console.error(`[portwatch] MacroMicro returned ${r.status}`);
      return null;
    }

    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      console.error('[portwatch] MacroMicro response is not JSON:', text.slice(0, 200));
      return null;
    }

    // MacroMicro chart data typically arrives as { data: [[timestamp, value], ...] }
    // or { series: [{ data: [[ts, val], ...] }] }
    // We try to extract the most recent value from whatever shape is returned.
    let series = null;

    if (Array.isArray(data?.data)) {
      series = data.data;
    } else if (Array.isArray(data?.series?.[0]?.data)) {
      series = data.series[0].data;
    } else if (Array.isArray(data?.rows)) {
      series = data.rows;
    } else if (Array.isArray(data)) {
      series = data;
    }

    if (!series || series.length === 0) {
      console.error('[portwatch] MacroMicro: unrecognized data shape:', JSON.stringify(data).slice(0, 300));
      return null;
    }

    // Sort descending by timestamp (first element of each pair)
    series.sort((a, b) => b[0] - a[0]);
    const latest = series[0];
    const prev = series[1] ?? latest;

    const ts = latest[0];
    const asOf = new Date(typeof ts === 'number' && ts < 1e10 ? ts * 1000 : ts).toISOString().slice(0, 10);
    const ma7day = typeof latest[1] === 'number' ? latest[1] : null;
    const daily = typeof prev[1] === 'number' ? prev[1] : null;

    if (ma7day === null) {
      console.error('[portwatch] MacroMicro: could not parse value from series point:', latest);
      return null;
    }

    return { ma7day, daily, asOf, source: 'macromicro', warning: null };
  } catch (err) {
    console.error('[portwatch] MacroMicro fetch error:', err.message);
    return null;
  }
}

export async function fetchPortwatchHormuz(manualFallback = null) {
  const live = await fetchMacroMicro();
  if (live) return live;

  // Fall back to hand-maintained manual value
  if (manualFallback && manualFallback.ma7day !== undefined && manualFallback.ma7day !== null) {
    return {
      ma7day: manualFallback.ma7day,
      daily: manualFallback.ma7day,
      asOf: manualFallback.asOf || null,
      source: 'manual',
      warning: manualFallback.note || 'Live data unavailable — using manual fallback value',
    };
  }

  return {
    ma7day: null,
    daily: null,
    asOf: null,
    source: 'unavailable',
    warning: 'PortWatch data unavailable — MacroMicro unreachable and no manual fallback set',
  };
}
