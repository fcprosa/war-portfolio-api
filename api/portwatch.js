// /api/portwatch.js — thin HTTP handler for IMF PortWatch Hormuz transit data
import { fetchPortwatchHormuz } from '../lib/portwatch.js';
import { getLatestBlob } from '../lib/state-helpers.js';

const CORS_ORIGINS = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  if (CORS_ORIGINS.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=60');

  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const state = await getLatestBlob();
    const manualFallback = state?.portwatchManual || null;
    const data = await fetchPortwatchHormuz(manualFallback);
    return res.status(200).json(data);
  } catch (err) {
    console.error('[portwatch] handler failed:', err.message);
    return res.status(500).json({ error: err.message });
  }
}
