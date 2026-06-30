import { Pool } from "pg";


// Reuse one pool across hot-reloads / requests.
// max:10 lets buildProjectHints fire ~N*2 parallel queries without heavy queuing.

const g = globalThis;
export const pool =
  g._cryptoPool ??
  new Pool({ connectionString: process.env.DATABASE_URL, max: 10 });
if (!g._cryptoPool) g._cryptoPool = pool;


// ── query helpers ─────────────────────────────────────────────────────────────
// Serialize a timestamptz column to an ISO string (or null) for the client boundary.
export const iso = (v) => (v ? v.toISOString() : null);


// Run a single SELECT and return its rows; on a transient DB error degrade to
// `fallback` so a public read surface shows an empty section instead of a 500.
// (Matches the silent try/catch every read accessor used to repeat.) Multi-query
// functions keep their own try/catch — these are for single-statement reads only.
export async function safeRows(text, params, fallback = []) {
  try {
    return (await pool.query(text, params)).rows;
  } catch {
    return fallback;
  }
}


// As safeRows, but returns the first row (or `fallback`, default null).
export async function safeOne(text, params, fallback = null) {
  try {
    return (await pool.query(text, params)).rows[0] ?? fallback;
  } catch {
    return fallback;
  }
}
