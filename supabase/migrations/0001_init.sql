-- ============================================================================
-- Kaamsetu — Supabase (Postgres) schema
-- Replaces Firebase/Firestore as the single source of truth.
--
-- Design in one line:
--   ONE human = one `users` row (by WhatsApp phone).  Everything the agent
--   knows about that human hangs off it in four memory tiers:
--     0. WORKING / short-term   -> sessions
--     1. LONG-TERM (truth)      -> candidate_profiles, employer_profiles,
--                                   job_posts, memory_facts, conversation_messages
--     2. EPISODIC (what happened)-> memory_episodes, events
--     3. SYNTHETIC (inferred)   -> synthetic_profiles
--   Cross-cutting SEMANTIC index (pgvector) -> memory_chunks  (RAG recall)
--
-- Run order: this is migration 0001. Apply with the Supabase CLI
--   (`supabase db push`) or paste into the SQL editor.
-- ============================================================================


-- ── Extensions ──────────────────────────────────────────────────────────────
create extension if not exists pgcrypto;   -- gen_random_uuid()
create extension if not exists vector;     -- pgvector: semantic recall


-- ── updated_at helper ────────────────────────────────────────────────────────
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;


-- ── Enums (mirror app/firebase/schemas.py) ───────────────────────────────────
create type user_role        as enum ('unknown','candidate','employer');
create type candidate_status as enum ('draft','live','paused','placed');
create type job_status       as enum ('draft','live','paused','filled','expired');
create type match_status     as enum ('proposed','candidate_accepted','candidate_declined',
                                       'employer_accepted','employer_declined','expired','placed');
create type optin_status     as enum ('pending','yes','no');
create type active_flow      as enum ('welcome','candidate_intake','employer_intake','idle','optin');
create type job_type         as enum ('full_time','contract','part_time','shift');
create type urgency          as enum ('high','normal','low');
create type msg_direction    as enum ('inbound','outbound');

-- The "multi-data sectors" — every fact/chunk is tagged with the slice of the
-- human it describes. Add values later with `alter type ... add value`.
create type memory_sector    as enum ('professional','personal','behavioral',
                                       'preference','financial','logistics','other');

-- Provenance drives trust: did the user SAY it, did an LLM parse it, or did we
-- INFER it? Inferred facts should never be treated as hard truth.
create type memory_source    as enum ('user_stated','extracted','inferred','system','agent');


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  IDENTITY                                                                  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- One row per real human. A person can be BOTH a candidate and an employer;
-- those are roles/flags, not separate identities. This is the anchor every
-- memory row points back to.
create table users (
  id                 uuid primary key default gen_random_uuid(),
  wa_id              text unique not null,            -- WhatsApp phone id
  display_name       text,
  primary_role       user_role not null default 'unknown',
  is_candidate       boolean not null default false,
  is_employer        boolean not null default false,
  preferred_language text default 'hi',               -- hi | mwr | en (see utils/i18n)
  locale             text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  last_seen_at       timestamptz
);
create trigger trg_users_updated before update on users
  for each row execute function set_updated_at();


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  TIER 1 — LONG-TERM MEMORY (the source of truth)                           ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- Candidate role data. 1:1 with users. Typed columns = fast, indexable matching.
create table candidate_profiles (
  user_id             uuid primary key references users(id) on delete cascade,
  status              candidate_status not null default 'draft',
  name                text,
  -- location
  loc_district        text,
  loc_city            text,
  loc_state           text,
  loc_lat             double precision,
  loc_lng             double precision,
  willing_to_relocate boolean not null default false,
  max_commute_km      int default 25,
  -- professional
  skills              text[] not null default '{}',
  experience_years    int,
  education            text,
  languages           text[] not null default '{}',
  job_type_pref       text[] not null default '{}',
  availability        text,
  -- expected salary
  expected_salary_min int,
  expected_salary_max int,
  salary_currency     text not null default 'INR',
  salary_period       text not null default 'month',
  -- intake / bookkeeping
  resume_url          text,
  raw_intake          jsonb not null default '{}'::jsonb,   -- voice_urls, pdf_url, chat_snippets
  pending_confirmation jsonb not null default '{}'::jsonb,  -- low-confidence fields awaiting confirm
  missing_fields      text[] not null default '{}',
  completeness_pct    int not null default 0,
  source              text not null default 'whatsapp',
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create trigger trg_candidate_updated before update on candidate_profiles
  for each row execute function set_updated_at();
create index idx_candidate_status    on candidate_profiles(status);
create index idx_candidate_district  on candidate_profiles(loc_district);
create index idx_candidate_skills    on candidate_profiles using gin(skills);
create index idx_candidate_jobtypes  on candidate_profiles using gin(job_type_pref);


-- Employer role data. 1:1 with users.
create table employer_profiles (
  user_id      uuid primary key references users(id) on delete cascade,
  company_name text,
  contact_name text,
  verified     boolean not null default false,
  industry     text,
  loc_district text,
  loc_city     text,
  loc_state    text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create trigger trg_employer_updated before update on employer_profiles
  for each row execute function set_updated_at();


-- Job postings. employer_id -> users(id) (the employer role of that human).
create table job_posts (
  id              uuid primary key default gen_random_uuid(),
  employer_id     uuid not null references users(id) on delete cascade,
  status          job_status not null default 'draft',
  title           text,
  description_raw text,
  skills_required text[] not null default '{}',
  nice_to_have    text[] not null default '{}',
  experience_min  int,
  loc_district    text,
  loc_city        text,
  loc_state       text,
  loc_lat         double precision,
  loc_lng         double precision,
  remote_ok       boolean not null default false,
  job_type        job_type,
  salary_min      int,
  salary_max      int,
  salary_currency text not null default 'INR',
  salary_period   text not null default 'month',
  openings        int,
  urgency         urgency,
  missing_fields  text[] not null default '{}',
  completeness_pct int not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  expires_at      timestamptz
);
create trigger trg_job_updated before update on job_posts
  for each row execute function set_updated_at();
create index idx_job_employer on job_posts(employer_id);
create index idx_job_status   on job_posts(status);
create index idx_job_skills   on job_posts using gin(skills_required);


-- FLEXIBLE fact store — this is the "know everything about the user" layer.
-- Anything that doesn't have a typed column lives here as one row per fact,
-- tagged with a sector, with provenance + confidence + TEMPORAL validity so a
-- preference can change over time without losing the history.
--   e.g. (professional, 'has_two_wheeler', true, user_stated, 0.95)
--        (preference,   'wants_night_shift', true, extracted, 0.7)
--        (personal,     'supports_family_of', 5, extracted, 0.6)
create table memory_facts (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references users(id) on delete cascade,
  sector              memory_sector not null default 'other',
  key                 text not null,
  value               jsonb not null,
  confidence          real not null default 0.5,
  source              memory_source not null default 'extracted',
  -- temporal validity: valid_to IS NULL means "currently true"
  valid_from          timestamptz not null default now(),
  valid_to            timestamptz,
  superseded_by       uuid references memory_facts(id),
  observed_message_id uuid,  -- FK to conversation_messages wired below (that table is created after this one)
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create trigger trg_facts_updated before update on memory_facts
  for each row execute function set_updated_at();
create index idx_facts_user_sector on memory_facts(user_id, sector);
create index idx_facts_key          on memory_facts(key);
-- Exactly one CURRENT value per (user, sector, key). Writing a new value should
-- first stamp valid_to on the old row, then insert the new one.
create unique index uq_facts_current
  on memory_facts(user_id, sector, key) where valid_to is null;


-- Full message log — the raw substrate episodic memory is distilled from, and
-- the fast source for short-term recall (last N by created_at).
create table conversation_messages (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references users(id) on delete cascade,
  direction     msg_direction not null,
  message_type  text not null,               -- text|voice|document|interactive|template
  content       text not null default '',
  media_url     text,
  wa_message_id text,
  agent         text,                         -- which agent produced an outbound msg
  created_at    timestamptz not null default now()
);
create index idx_msg_user_time on conversation_messages(user_id, created_at desc);

-- Deferred FK: memory_facts.observed_message_id was declared before this table
-- existed. Wire it now that conversation_messages is created.
alter table memory_facts
  add constraint fk_facts_observed_msg
  foreign key (observed_message_id) references conversation_messages(id) on delete set null;


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  TIER 0 — WORKING / SHORT-TERM MEMORY                                      ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- One row per user. Holds live flow state + a tiny ring buffer of the last few
-- turns for conversational continuity. Ephemeral by nature — safe to reset.
create table sessions (
  user_id         uuid primary key references users(id) on delete cascade,
  wa_id           text unique not null,
  role            user_role not null default 'unknown',
  active_flow     active_flow not null default 'welcome',
  expected_field  text,
  short_term      jsonb not null default '[]'::jsonb,   -- [{role,text}, ...] max ~6
  retry_counts    jsonb not null default '{}'::jsonb,
  last_agent      text,
  last_message_id text,
  updated_at      timestamptz not null default now()
);
create trigger trg_sessions_updated before update on sessions
  for each row execute function set_updated_at();


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  TIER 2 — EPISODIC MEMORY                                                  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- Meaningful things that HAPPENED to/with this user, summarised in natural
-- language so they can be embedded and recalled ("declined 3 night shifts",
-- "went silent for a week after being placed"). Curated from events + messages
-- by a background job — this is memory, not the raw audit log below.
create table memory_episodes (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users(id) on delete cascade,
  kind        text not null,                       -- applied_job | declined_match | placed | went_silent ...
  title       text,
  summary     text not null,                       -- NL summary, this is what gets embedded
  payload     jsonb not null default '{}'::jsonb,  -- structured refs (job_id, match_id, ...)
  importance  real not null default 0.5,
  occurred_at timestamptz not null default now(),
  created_at  timestamptz not null default now()
);
create index idx_episodes_user_time on memory_episodes(user_id, occurred_at desc);


-- Raw, append-only audit/analytics stream (was Firestore `events`). No embeddings.
create table events (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references users(id) on delete set null,
  wa_id      text,
  type       text not null,                         -- consent_given|profile_live|match_proposed|...
  data       jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index idx_events_user_time on events(user_id, created_at desc);
create index idx_events_type       on events(type);


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  TIER 3 — SYNTHETIC MEMORY (inferred, never hard truth)                    ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- The agent's inferred read of the human: soft skills, tone, reliability,
-- personality per sector. VERSIONED (we keep history, never overwrite) so we can
-- watch how our read of someone drifts. Matchmaker uses only as a low-weight
-- signal (<= 5%).
create table synthetic_profiles (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references users(id) on delete cascade,
  soft_skills       text[] not null default '{}',
  tone              text,
  reliability_signal real,
  urgency           text,                            -- mainly for employers
  personality       jsonb not null default '{}'::jsonb,  -- per-sector inferences
  summary           text,
  model             text,                            -- which LLM produced this
  version           int not null default 1,
  is_current        boolean not null default true,
  generated_at      timestamptz not null default now()
);
-- Exactly one current synthetic profile per user.
create unique index uq_synthetic_current
  on synthetic_profiles(user_id) where is_current;


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  SEMANTIC INDEX — pgvector RAG recall across ALL memory                    ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- One searchable projection of every memory type. When a fact/episode/message/
-- synthetic summary is written, its text is embedded and dropped here. A single
-- similarity query then recalls the most relevant memories regardless of tier.
--   1536 dims = OpenAI text-embedding-3-small (change if you switch models).
create table memory_chunks (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users(id) on delete cascade,
  source_type text not null,                         -- fact | episode | message | synthetic | profile
  source_id   uuid,                                  -- back-reference to the structured row
  sector      memory_sector,
  content     text not null,                         -- the exact text that was embedded
  embedding   vector(1536),
  metadata    jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index idx_chunks_user   on memory_chunks(user_id);
create index idx_chunks_source on memory_chunks(source_type, source_id);
-- Approximate-nearest-neighbour index for fast cosine similarity recall.
create index idx_chunks_embedding on memory_chunks
  using hnsw (embedding vector_cosine_ops);


-- Recall helper: top-k memories for a user by semantic similarity.
-- Call from the app as an RPC:  supabase.rpc('match_memory', {...})
create or replace function match_memory(
  p_user_id         uuid,
  p_query_embedding vector(1536),
  p_match_count     int   default 8,
  p_min_similarity  float default 0.0
)
returns table (
  id          uuid,
  source_type text,
  source_id   uuid,
  sector      memory_sector,
  content     text,
  similarity  float,
  metadata    jsonb
)
language sql stable as $$
  select c.id, c.source_type, c.source_id, c.sector, c.content,
         1 - (c.embedding <=> p_query_embedding) as similarity,
         c.metadata
  from memory_chunks c
  where c.user_id = p_user_id
    and c.embedding is not null
    and 1 - (c.embedding <=> p_query_embedding) >= p_min_similarity
  order by c.embedding <=> p_query_embedding
  limit p_match_count;
$$;


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  MATCHING                                                                  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

create table matches (
  id                 uuid primary key default gen_random_uuid(),
  job_id             uuid not null references job_posts(id) on delete cascade,
  candidate_id       uuid not null references users(id) on delete cascade,
  overall_score      int not null default 0,
  -- dimension scores (was DimensionScores)
  score_skills       int not null default 0,
  score_experience   int not null default 0,
  score_location     int not null default 0,
  score_salary       int not null default 0,
  score_availability int not null default 0,
  score_soft         int not null default 0,
  rationale          text not null default '',
  red_flags          text[] not null default '{}',
  pitch_for_employer text not null default '',
  pitch_for_candidate text not null default '',
  confidence         real not null default 0,
  status             match_status not null default 'proposed',
  -- double opt-in
  candidate_optin    optin_status not null default 'pending',
  candidate_optin_at timestamptz,
  employer_optin     optin_status not null default 'pending',
  employer_optin_at  timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  -- replaces MatchRepo.exists_for_pair(): DB-enforced dedupe
  unique (job_id, candidate_id)
);
create trigger trg_matches_updated before update on matches
  for each row execute function set_updated_at();
create index idx_matches_candidate_status on matches(candidate_id, status);
create index idx_matches_status           on matches(status);


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  SUPPORTING                                                                ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- Dynamic config (was Firestore `config`). Tunable without a deploy.
create table app_config (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
create trigger trg_config_updated before update on app_config
  for each row execute function set_updated_at();

-- Media (voice notes, resumes, images) -> Supabase Storage. Voice keeps its
-- Whisper transcript so it feeds long-term/semantic memory like any text.
create table media_assets (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid references users(id) on delete cascade,
  kind         text not null,                        -- voice | document | image
  storage_path text not null,                         -- path in Supabase Storage bucket
  mime_type    text,
  transcript   text,
  created_at   timestamptz not null default now()
);
create index idx_media_user on media_assets(user_id);

-- Inbound WhatsApp message dedupe (was utils/idempotency).
create table idempotency_keys (
  key        text primary key,                        -- wa_message_id
  created_at timestamptz not null default now()
);


-- ── Seed dynamic config (defaults from app/config.py) ─────────────────────────
insert into app_config(key, value) values
  ('scoring_weights',
     '{"skills":35,"experience":20,"location":15,"salary":15,"availability":10,"soft":5}'::jsonb),
  ('thresholds',
     '{"match_threshold":85,"extraction_confidence_min":0.7,"max_field_retries":3,
       "matchmaker_interval_minutes":15,"synthetic_refresh_interval_minutes":60,
       "optin_timeout_hours":48,"max_pending_optins_per_candidate":2}'::jsonb),
  ('required_fields',
     '{"candidate":["name","location","skills","experience_years","expected_salary","job_type_pref","availability"],
       "job":["title","skills_required","experience_min","location","job_type","salary","openings"]}'::jsonb)
on conflict (key) do nothing;


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  ROW-LEVEL SECURITY                                                        ║
-- ║  This is a server-side backend: it uses the Supabase SERVICE ROLE key,     ║
-- ║  which BYPASSES RLS. We enable RLS with no public policies so that the     ║
-- ║  anon/authenticated keys can't read PII if they ever leak. Add policies    ║
-- ║  later only if you build a client-side app.                                ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
alter table users                 enable row level security;
alter table candidate_profiles    enable row level security;
alter table employer_profiles     enable row level security;
alter table job_posts             enable row level security;
alter table matches               enable row level security;
alter table sessions              enable row level security;
alter table conversation_messages enable row level security;
alter table memory_facts          enable row level security;
alter table memory_episodes       enable row level security;
alter table synthetic_profiles    enable row level security;
alter table memory_chunks         enable row level security;
alter table events                enable row level security;
alter table media_assets          enable row level security;
alter table app_config            enable row level security;
alter table idempotency_keys      enable row level security;
