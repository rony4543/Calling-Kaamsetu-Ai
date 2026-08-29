-- ============================================================================
-- Kaamsetu — migration 0002: agent-skill hardening
--
-- Applies guidance from Supabase's `supabase-postgres-best-practices` agent
-- skill to the 0001 schema. Everything here is IDEMPOTENT (safe to re-run):
--   • Schema Design  → index every foreign key not already covered
--   • Query Perf     → partial indexes for the hot "live" matchmaker sweeps
--   • Security & RLS → defense-in-depth: strip anon/authenticated privileges
--   • Docs           → comments so the schema is self-describing
--
-- Apply automatically with:  python scripts/supabase_bootstrap.py
-- ============================================================================


-- ── Schema Design: foreign-key indexes ───────────────────────────────────────
-- An unindexed FK makes child rows expensive to find on JOINs and forces a full
-- scan on every parent DELETE/UPDATE (cascade). These are the FKs from 0001 not
-- already covered by an existing (or composite-leading) index.

create index if not exists idx_matches_job
  on matches(job_id);                        -- FK matches.job_id -> job_posts(id)

create index if not exists idx_facts_observed_msg
  on memory_facts(observed_message_id)       -- FK -> conversation_messages(id)
  where observed_message_id is not null;

create index if not exists idx_facts_superseded_by
  on memory_facts(superseded_by)             -- self-FK
  where superseded_by is not null;

create index if not exists idx_synthetic_user
  on synthetic_profiles(user_id);            -- FK -> users(id); history + cascade


-- ── Query Performance: partial indexes for the matchmaker sweep ───────────────
-- The scheduler repeatedly scans for LIVE candidates and LIVE jobs. Partial
-- indexes stay tiny (only live rows) and turn those sweeps into index scans.

create index if not exists idx_candidate_live
  on candidate_profiles(updated_at desc)
  where status = 'live';

create index if not exists idx_jobs_live
  on job_posts(updated_at desc)
  where status = 'live';

-- Opt-in timeout sweep: matches still awaiting a reply.
create index if not exists idx_matches_pending_optin
  on matches(created_at)
  where status in ('proposed', 'candidate_accepted');

-- Idempotency-key retention pruning (delete rows older than N days).
create index if not exists idx_idempotency_created
  on idempotency_keys(created_at);


-- ── Advanced (pgvector): recall/speed tuning note ─────────────────────────────
-- The HNSW index from 0001 (idx_chunks_embedding) uses the sensible defaults
-- m=16, ef_construction=64. Tune recall vs latency at QUERY time, per session:
--     set hnsw.ef_search = 40;   -- higher = better recall, slower
-- (No DDL change needed; documented here so it isn't forgotten.)


-- ── Security & RLS: defense-in-depth privilege hardening ──────────────────────
-- 0001 already enables RLS with no policies, so the anon/publishable key is
-- blocked from every row. This goes further and REVOKES table privileges from
-- the anon/authenticated roles entirely, so even a future accidental RLS policy
-- can't expose PII without an explicit re-GRANT. The backend uses the
-- service_role key, which BYPASSES RLS and is unaffected.
--
-- To reverse (e.g. if you add a browser client later):
--   grant select on <table> to anon;   -- then add a scoped RLS policy
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
-- Cover future tables created by later migrations too.
alter default privileges in schema public
  revoke all on tables from anon, authenticated;


-- ── Docs: self-describing schema ─────────────────────────────────────────────
comment on table users              is 'One row per real human (by WhatsApp id). Anchor for all memory tiers.';
comment on table sessions           is 'Tier 0 — working/short-term memory: live flow state + last-N turns.';
comment on table memory_facts       is 'Tier 1 — long-term flexible facts, tagged by sector, with provenance + temporal validity.';
comment on table memory_episodes    is 'Tier 2 — episodic memory: NL summaries of what happened, embedded for recall.';
comment on table synthetic_profiles is 'Tier 3 — synthetic/inferred read of the human; versioned, low-trust, <=5% match weight.';
comment on table memory_chunks      is 'Cross-tier pgvector semantic index (1536-dim) powering match_memory() RAG recall.';
comment on column memory_facts.valid_to is 'NULL = currently true. On update, stamp the old row''s valid_to, then insert the new value.';
