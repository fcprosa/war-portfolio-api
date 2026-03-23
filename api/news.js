// /api/news.js — thin HTTP handler
import { fetchAllNews } from '../lib/news.js';

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=60');

  try {
    const data = await fetchAllNews();
    return res.status(200).json(data);
  } catch (err) {
    console.error('[news] handler failed:', err.message);
    return res.status(500).json({ error: err.message });
  }
}
