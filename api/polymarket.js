// /api/polymarket.js — thin HTTP handler for Polymarket prediction market data
import { fetchPolymarketState } from '../lib/polymarket.js';
import { getLatestBlob } from '../lib/state-helpers.js';

const CORS_ORIGINS = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  if (CORS_ORIGINS.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=60');

  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    let positions;
    if (req.method === 'POST') {
      const body = await readBody(req);
      positions = body.positions || [];
    } else {
      const state = await getLatestBlob();
      positions = (state?.predictionMarkets || []).filter(p => p.platform === 'polymarket');
    }

    const data = await fetchPolymarketState(positions);
    return res.status(200).json(data);
  } catch (err) {
    console.error('[polymarket] handler failed:', err.message);
    return res.status(500).json({ error: err.message });
  }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => data += chunk);
    req.on('end', () => { try { resolve(JSON.parse(data)); } catch { resolve({}); } });
    req.on('error', reject);
  });
}
