// /lib/utils.js — shared utilities

// War started Feb 28, 2026 — Day 1
export function getWarDay() {
  const start = new Date('2026-02-28T00:00:00Z');
  const now = new Date();
  return Math.floor((now - start) / (1000 * 60 * 60 * 24)) + 1;
}
