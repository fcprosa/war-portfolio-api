// /api/config.js
// Serves portfolio configuration from Vercel environment variables.
// Set these in the Vercel dashboard under Project → Settings → Environment Variables:
//   DEFAULT_POSITIONS            — JSON array of position objects
//   DEFAULT_WATCHLIST            — JSON array of watchlist objects
//   DEFAULT_CASH                 — string like "~$645"
//   DEFAULT_CASH_SUB             — string like "$146 Revolut cash + ~$498 outside"
//   DEFAULT_PREDICTION_MARKETS   — JSON array of prediction market positions
//   DEFAULT_PORTWATCH_MANUAL     — JSON object or null

export default async function handler(req, res) {
  const origin = req.headers.origin || '';
  const allowed = ['https://war-portfolio-api.vercel.app', 'http://localhost:3000', 'http://localhost:5500'];
  if (allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  const config = {
    positions: JSON.parse(process.env.DEFAULT_POSITIONS || '[]'),
    watchlist: JSON.parse(process.env.DEFAULT_WATCHLIST || '[]'),
    cash: process.env.DEFAULT_CASH || '~$0',
    cashSub: process.env.DEFAULT_CASH_SUB || '',
    predictionMarkets: JSON.parse(process.env.DEFAULT_PREDICTION_MARKETS || '[]'),
    portwatchManual: JSON.parse(process.env.DEFAULT_PORTWATCH_MANUAL || 'null'),
  };

  return res.status(200).json(config);
}
