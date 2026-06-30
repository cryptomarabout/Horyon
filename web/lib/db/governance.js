// app/lib/db/governance.js — Snapshot governance proposals
import { safeRows, iso } from "./_core.js";


// Active + pending Snapshot governance proposals, ordered soonest-ending first.
export async function getGovernanceProposals(limit = 6) {
  const rows = await safeRows(
    `SELECT proposal_id, space_id, space_name, title, state,
            start_ts, end_ts
     FROM governance_proposals
     WHERE state = 'active'
     ORDER BY end_ts ASC NULLS LAST
     LIMIT $1`,
    [limit]
  );
  return rows.map(r => ({ ...r, end_ts: iso(r.end_ts) }));
}
