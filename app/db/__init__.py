"""Postgres data layer.

pgvector values are passed as text literals cast with ``::vector`` so no extra
adapter package is required."""
from __future__ import annotations

# Shared infra (kept importable as db._conn etc. — used across the app).
from ._core import (
    _conn, _execute, _fetchall, _fetchone, _get_pool, _term_boundary_regex,
    _vec_literal, log,
)
from .feeds import (
    insert_feed_items, existing_links, record_ingest_run, update_source_health,
    count_missing_embeddings, embed_missing, reembed_stale, count_stale_embeddings,
    get_recent_feed_items, get_feed_items_for_date, get_feed_items_since,
    get_feed_items_since_ts, get_recent_unalerted_items,
    get_source_ingestion_counts, get_chronic_failing_sources,
    get_feed_items_matching_terms, search_feed, get_kaiko_feed_signals,
)
from .digest import (
    get_cache, set_cache, insert_digest, record_keyword_analysis, get_digest,
    get_digest_attempts, get_recent_digests, get_recent_keyword_analyses,
    get_recent_digests_text, get_digest_contents_for_dedup, get_digests_for_range,
    insert_digest_update, get_digest_updates, get_last_covered_ts,
)
from .protocols import (
    upsert_tvl, get_latest_tvl, get_chain_tvl_for_week, upsert_protocols,
    get_top_protocols, get_protocols_by_slugs, get_protocol_tvls,
    get_protocol_category_summary, get_protocol_tvl_movers,
    upsert_market_data, get_market_data_by_slugs,
)
from .entities import (
    CANONICAL_ENTITY_TYPES, entity_generation, upsert_entity, upsert_entity_from_coingecko,
    update_entity_summary, get_all_entity_aliases, get_entities_by_slugs,
    get_governance_for_entity, touch_entity_mentions, decay_stale_entities,
    seed_entities_from_protocols, get_entity_mention_map, get_entities_for_briefing,
    get_entities_for_matching, upsert_entity_intel_brief, get_entity_intel_brief,
    update_digest_mention_counts, get_entity_reviews, set_entity_review,
    prose_doc_count,
)
from .analysis import (
    insert_analyst_notes, upsert_bullet_analyses, get_bullet_analyses,
    get_bullet_analyses_full, delete_bullet_analyses, prune_bullet_analyses,
    get_recent_analyst_notes, get_bullet_analyses_window, get_prior_bullets_matching_terms,
    get_all_digest_bullets,
)
from .threads import (
    upsert_thread, get_thread, get_digest_dates_without_thread,
)
from .audio import (
    upsert_audio_briefing, delete_og_card, upsert_og_card, upsert_entity_avatar,
    get_audio_briefing, get_audio_bytes, get_existing_audio_variants,
    get_audio_variant_status, get_digest_dates_without_audio, null_old_audio,
)
from .weekly import (
    insert_weekly_digest, get_weekly_for_date, list_weekly_digests,
    get_recent_weekly_digests, get_weeks_needing_backfill,
)
from .governance import (
    upsert_governance_proposals, get_active_governance_proposals,
    get_governance_signals_window, close_expired_proposals,
)
from .podcasts import (
    insert_podcast_episode, get_pending_podcast_episodes, store_podcast_summary,
    mark_podcast_episode, get_recent_podcast_summaries, get_podcast_summaries_window,
    insert_podcast_predictions, get_open_predictions, update_prediction_outcome,
    get_predictions_for_research,
)
from .narratives import (
    get_existing_narratives, replace_narratives, replace_entity_edges,
)
from .evals import (
    insert_eval_results, get_eval_batches,
)
from .alerts import (
    insert_alerts_sent, get_recent_alerts, count_alerts_since,
)
