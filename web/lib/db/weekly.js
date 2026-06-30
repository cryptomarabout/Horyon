// app/lib/db/weekly.js — weekly macro digests
import { safeRows, safeOne } from "./_core.js";


// List of recent weekly digests for the sidebar (week_start, week_end, rotation, content).
export async function listWeeklyDigests(limit = 10) {
  return safeRows(
    `SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
            to_char(week_end,'YYYY-MM-DD')   AS week_end,
            rotation, content,
            to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
     FROM weekly_digest
     WHERE error IS NULL
     ORDER BY week_start DESC
     LIMIT $1`,
    [limit]
  );
}


// Structured market snapshots per week, keyed by week_start. Queried SEPARATELY
// from listWeeklyDigests so the `market_snapshot` column being absent (migration
// not yet applied) degrades to [] via safeRows instead of blanking the page — the
// Weekly view then falls back to parsing the market prose. Once the migration is
// applied + a week regenerated, these take precedence (see lib/weekly.snapshotCells).
export async function getWeeklyMarketSnapshots(limit = 10) {
  return safeRows(
    `SELECT to_char(week_start,'YYYY-MM-DD') AS week_start, market_snapshot
     FROM weekly_digest
     WHERE error IS NULL AND market_snapshot IS NOT NULL
     ORDER BY week_start DESC
     LIMIT $1`,
    [limit]
  );
}


// Weekly digest that covers the given date (null if none yet).
export async function getWeeklyForDate(date) {
  return safeOne(
    `SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
            to_char(week_end,'YYYY-MM-DD')   AS week_end,
            content, rotation,
            to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
     FROM weekly_digest
     WHERE error IS NULL AND week_start <= $1::date AND week_end >= $1::date
     ORDER BY week_start DESC
     LIMIT 1`,
    [date]
  );
}
