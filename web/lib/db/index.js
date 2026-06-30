// app/lib/db — Postgres data layer (domain-split package).
// Re-exports the full flat API so callers keep `import { … } from "@/lib/db"`.
export { pool } from "./_core.js";
export * from "./digest.js";
export * from "./threads.js";
export * from "./audio.js";
export * from "./tvl.js";
export * from "./governance.js";
export * from "./podcasts.js";
export * from "./weekly.js";
export * from "./analysis.js";
export * from "./entities.js";
export * from "./narratives.js";
