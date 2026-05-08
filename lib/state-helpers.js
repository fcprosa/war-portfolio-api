// /lib/state-helpers.js — shared Blob state reader used across API routes
import { list } from '@vercel/blob';

const BLOB_KEY = 'war-portfolio-state.json';

export async function getLatestBlob() {
  try {
    const { blobs } = await list({ prefix: BLOB_KEY });
    if (blobs.length === 0) return null;
    const latest = blobs.sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt))[0];
    const resp = await fetch(latest.url);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('[state-helpers] blob read failed:', err.message);
    return null;
  }
}
