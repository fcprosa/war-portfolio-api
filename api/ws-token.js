// /api/ws-token.js — serves Finnhub key to client (graceful if not set)
export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  const key = process.env.FINNHUB_KEY;
  if (!key) return res.status(200).json({ token: null });
  return res.status(200).json({ token: key });
}
