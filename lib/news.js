// /lib/news.js — news aggregation logic (extracted from api/news.js)

const RSS_FEEDS = [
  { url: 'https://feeds.reuters.com/reuters/businessNews', source: 'Reuters' },
  { url: 'https://feeds.reuters.com/Reuters/worldNews', source: 'Reuters' },
  { url: 'https://feeds.bbci.co.uk/news/business/rss.xml', source: 'BBC' },
  { url: 'https://feeds.bbci.co.uk/news/world/rss.xml', source: 'BBC' },
  { url: 'https://www.cnbc.com/id/100003114/device/rss/rss.html', source: 'CNBC' },
  { url: 'https://www.cnbc.com/id/10000664/device/rss/rss.html', source: 'CNBC Markets' },
  { url: 'https://www.aljazeera.com/xml/rss/all.xml', source: 'Al Jazeera' },
  { url: 'https://rss.app/feeds/tvCoDpFhMDuHqZ4Z.xml', source: 'FT' },
  { url: 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147', source: 'CNBC Energy' },
];

const WAR_KEYWORDS = [
  'hormuz', 'iran', 'oil', 'crude', 'brent', 'wti',
  'fed', 'powell', 'rate', 'inflation', 'stagflation',
  'defense', 'rearmament', 'military', 'nato', 'ukraine',
  'gold', 'commodity', 'commodities',
  'bank', 'credit', 'financial', 'markets',
  'trump', 'tariff', 'war', 'conflict', 'middle east',
  'opec', 'energy', 'gas', 'tanker', 'shipping',
  'fertilizer', 'uranium', 'nuclear',
];

function tagArticle(title, desc) {
  const text = (title + ' ' + (desc || '')).toLowerCase();
  if (text.includes('hormuz') || text.includes('strait') || text.includes('iran') || text.includes('crude') || text.includes('brent') || text.includes('oil price')) return 'oil-war';
  if (text.includes('fed') || text.includes('powell') || text.includes('interest rate') || text.includes('inflation') || text.includes('stagflation')) return 'fed';
  if (text.includes('defense') || text.includes('rearmament') || text.includes('military') || text.includes('nato') || text.includes('weapon')) return 'defense';
  if (text.includes('gold') || text.includes('silver') || text.includes('precious metal')) return 'gold';
  if (text.includes('credit') || text.includes('private credit') || text.includes('bank crisis') || text.includes('svb')) return 'credit';
  if (text.includes('fertilizer') || text.includes('agriculture') || text.includes('wheat') || text.includes('grain')) return 'agriculture';
  if (text.includes('uranium') || text.includes('nuclear energy')) return 'uranium';
  if (text.includes('tariff') || text.includes('trade war') || text.includes('trump')) return 'macro';
  return 'macro';
}

function isRelevant(title, desc) {
  const text = (title + ' ' + (desc || '')).toLowerCase();
  return WAR_KEYWORDS.some(kw => text.includes(kw));
}

function parseRSSDate(dateStr) {
  try { return new Date(dateStr).toISOString(); } catch { return new Date().toISOString(); }
}

function extractItems(xml, sourceName) {
  const items = [];
  const itemRegex = /<item>([\s\S]*?)<\/item>/g;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    const title = (block.match(/<title><!\[CDATA\[(.*?)\]\]><\/title>/) || block.match(/<title>(.*?)<\/title>/) || [])[1] || '';
    const link = (block.match(/<link>(.*?)<\/link>/) || block.match(/<link.*?href="(.*?)"/) || [])[1] || '';
    const desc = (block.match(/<description><!\[CDATA\[(.*?)\]\]><\/description>/) || block.match(/<description>(.*?)<\/description>/) || [])[1] || '';
    const pubDate = (block.match(/<pubDate>(.*?)<\/pubDate>/) || block.match(/<dc:date>(.*?)<\/dc:date>/) || [])[1] || '';
    const cleanTitle = title.replace(/<[^>]+>/g, '').trim();
    const cleanDesc = desc.replace(/<[^>]+>/g, '').slice(0, 150).trim();
    if (cleanTitle && isRelevant(cleanTitle, cleanDesc)) {
      items.push({
        title: cleanTitle,
        source: sourceName,
        url: link.trim(),
        publishedAt: parseRSSDate(pubDate),
        description: cleanDesc,
        tag: tagArticle(cleanTitle, cleanDesc),
      });
    }
  }
  return items;
}

async function fetchFeed(feed) {
  try {
    const r = await fetch(feed.url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; NewsBot/1.0)',
        'Accept': 'application/rss+xml, application/xml, text/xml',
      },
      signal: AbortSignal.timeout(6000),
    });
    if (!r.ok) return [];
    const xml = await r.text();
    return extractItems(xml, feed.source);
  } catch (err) {
    console.error(`[news] ${feed.source} failed:`, err.message);
    return [];
  }
}

export async function fetchAllNews() {
  const results = await Promise.allSettled(RSS_FEEDS.map(fetchFeed));
  const allItems = results
    .filter(r => r.status === 'fulfilled')
    .flatMap(r => r.value);

  const seen = new Set();
  const unique = allItems.filter(item => {
    const key = item.title.slice(0, 60).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  unique.sort((a, b) => new Date(b.publishedAt) - new Date(a.publishedAt));

  const top = unique.slice(0, 20);
  return {
    timestamp: new Date().toISOString(),
    count: top.length,
    source: 'RSS — Reuters, BBC, CNBC, Al Jazeera (real-time)',
    articles: top,
  };
}
