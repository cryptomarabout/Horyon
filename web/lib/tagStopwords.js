// Shared stop-list for entity-tag matching: crypto-generic vocab + common English
// words that are ALSO protocol/entity names. Mirrors app/entities.GENERIC_TERMS.
// A bare word-boundary match on any of these is a false-positive generator —
// "Current" on "AAVE's current value", "Team Finance" on any "team", "Funding
// Commons" on any "funding", "Reserve Protocol" on "Chainlink Reserve".
//
// SINGLE SOURCE for both matcher layers: the SQL matchers (lib/db/entities.js builds
// STOP_SQL from this) and the JS matchers that render (lib/projects.js). These two
// files used to carry hand-synced copies — the twin-files rule existed precisely
// because a fix applied to one side silently left the other matching differently.
// Pure data, no imports: safe from both server SQL code and anything else.
//
// NEVER add a brand-that-is-also-a-word (Base/Flow/Spark/Render/Strike) — those are
// handled by CASE-SENSITIVE matching; stop-listing them kills the real brand too.
export const TAG_STOPWORDS = [
  // crypto-generic
  'chain','free','idle','defi','token','tokens','network','protocol','finance',
  'open','world','new','core','main','node','fund','funds','labs','across','yield',
  'capital','basis','group','standard','bridge','native','wrapped','push','vault',
  'vaults','credit','lending','stable','stablecoin','savings','saving','staking',
  'staked','liquid','restaking','perp','perps','pool','pools','prime','real','spot',
  'treasury','rewards','points','public','swap','swaps','dex','blockchain','digital',
  'crypto','decentralized','global','circle','fun','onchain','notional','loan','loans',
  // common English words that are also protocol/entity names (false-positive class)
  'current','team','reserve','extra','neutral','funding','story','general','future',
  'futures','signal','simple','instant','secure','trust','official','select','fixed',
  'smart','auto','strategy','movement','bullish','bearish','believe','master','super',
  'summit','pure','solid','basic','fair','grand','markets','market',
  // junk single-word aliases (extraction noise) — NOT brand names (see note above).
  'hard','edge','next','move','link','farm','coin','call','date','deal','news','live',
  'wave','gate',
];
