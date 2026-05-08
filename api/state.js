// /api/state.js — Server-side portfolio state (replaces config.js)
// Stores positions, watchlist, cash, predictionMarkets, portwatchManual as a single JSON blob.
// GET  → returns current state
// POST → saves new state (protected by PIN)
//
// Required env vars:
//   BLOB_READ_WRITE_TOKEN — auto-set when you connect Vercel Blob in dashboard
//   STATE_PIN             — any short PIN you choose (e.g. "1234") to protect writes

import { put } from '@vercel/blob';
import { getLatestBlob } from '../lib/state-helpers.js';

const BLOB_KEY = 'war-portfolio-state.json';
const CORS_ORIGINS = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];

function setCors(req, res) {
  const origin = req.headers.origin || '';
  if (CORS_ORIGINS.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Pin');
}

export default async function handler(req, res) {
  setCors(req, res);

  if (req.method === 'OPTIONS') return res.status(200).end();

  // ── GET: return current state ──
  if (req.method === 'GET') {
    const state = await getLatestBlob();
    if (state) {
      // Ensure new fields exist for older blobs
      return res.status(200).json({
        ...state,
        predictionMarkets: state.predictionMarkets ?? [],
        portwatchManual: state.portwatchManual ?? null,
      });
    }
    // Fallback to env-var defaults
    return res.status(200).json({
      positions: JSON.parse(process.env.DEFAULT_POSITIONS || '[]'),
      watchlist: JSON.parse(process.env.DEFAULT_WATCHLIST || '[]'),
      cash: process.env.DEFAULT_CASH || '~$0',
      cashSub: process.env.DEFAULT_CASH_SUB || '',
      predictionMarkets: JSON.parse(process.env.DEFAULT_PREDICTION_MARKETS || '[]'),
      portwatchManual: JSON.parse(process.env.DEFAULT_PORTWATCH_MANUAL || 'null'),
    });
  }

  // ── POST: save new state (PIN-protected) ──
  if (req.method === 'POST') {
    const pin = req.headers['x-pin'] || '';
    const expectedPin = process.env.STATE_PIN || '';
    if (!expectedPin) {
      return res.status(500).json({ error: 'STATE_PIN not configured on server' });
    }
    if (pin !== expectedPin) {
      return res.status(403).json({ error: 'Invalid PIN' });
    }

    try {
      const body = await readBody(req);

      // Load existing state so a partial POST (e.g. only predictionMarkets) merges cleanly
      const existing = await getLatestBlob() || {};

      const state = {
        positions: body.positions ?? existing.positions ?? [],
        watchlist: body.watchlist ?? existing.watchlist ?? [],
        cash: body.cash ?? existing.cash ?? '~$0',
        cashSub: body.cashSub ?? existing.cashSub ?? '',
        predictionMarkets: body.predictionMarkets ?? existing.predictionMarkets ?? [],
        portwatchManual: body.portwatchManual !== undefined ? body.portwatchManual : (existing.portwatchManual ?? null),
        updatedAt: new Date().toISOString(),
      };

      await put(BLOB_KEY, JSON.stringify(state), {
        access: 'public',
        contentType: 'application/json',
        addRandomSuffix: false,
      });

      return res.status(200).json({ ok: true, updatedAt: state.updatedAt });
    } catch (err) {
      console.error('[state] save failed:', err.message);
      return res.status(500).json({ error: err.message });
    }
  }

  return res.status(405).json({ error: 'Method not allowed' });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => data += chunk);
    req.on('end', () => {
      try { resolve(JSON.parse(data)); }
      catch { resolve({}); }
    });
    req.on('error', reject);
  });
}
