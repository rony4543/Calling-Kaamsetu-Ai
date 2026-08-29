#!/usr/bin/env python3
"""
One-command Supabase bootstrap for Kaamsetu.

Uses the Supabase Management API + your Personal Access Token
(SUPABASE_ACCESS_TOKEN, the `sbp_...` value in .env) to set the project up with
zero dashboard clicking:

  1. Reveal the project's SECRET (service_role) API key and write it into .env
     as SUPABASE_KEY — the key the FastAPI backend needs. (RLS blocks the
     publishable key, so the app can't use that one.)
  2. Apply every migration in supabase/migrations/*.sql, in order, if the schema
     isn't already present.
  3. Verify by counting the tables that now exist.

Run once, on a machine with internet access:

    python3 scripts/supabase_bootstrap.py

Notes
-----
* Standard library only — no `pip install` required to run this.
* Secret values are written to .env but NEVER printed to the terminal.
* SUPABASE_ACCESS_TOKEN is used only by this script; the app never touches it.
  Rotate it after setup: https://supabase.com/dashboard/account/tokens
"""
from __future__ import annotations

import glob
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
API = "https://api.supabase.com"


# ── .env helpers ─────────────────────────────────────────────────────────────
def load_env() -> dict:
    env: dict = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def upsert_env(key_name: str, value: str) -> None:
    """Set KEY=value in .env, preserving all other lines. Never prints `value`."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    out, found = [], False
    pat = re.compile(rf"\s*{re.escape(key_name)}\s*=")
    for line in lines:
        if pat.match(line) and not line.lstrip().startswith("#"):
            out.append(f"{key_name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key_name}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")


def project_ref(env: dict) -> str | None:
    ref = env.get("SUPABASE_PROJECT_REF")
    if ref:
        return ref
    m = re.search(r"https://([a-z0-9]+)\.supabase\.co", env.get("SUPABASE_URL", ""))
    return m.group(1) if m else None


# ── Management API ───────────────────────────────────────────────────────────
def api(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason} (are you online? this needs api.supabase.com)"


def run_sql(ref: str, token: str, sql: str):
    return api("POST", f"/v1/projects/{ref}/database/query", token, {"query": sql})


def find_secret_key(keys: list) -> tuple[str | None, list]:
    """Return (secret_value, [(name,type)...]) from the api-keys payload.

    Handles both the legacy shape ({name:'service_role', api_key:'eyJ...'}) and
    the new key system ({type:'secret', api_key:'sb_secret_...'})."""
    names = []
    secret = None
    for k in keys or []:
        name = (k.get("name") or "").lower()
        typ = (k.get("type") or "").lower()
        names.append((k.get("name"), k.get("type")))
        value = k.get("api_key") or k.get("secret_key") or k.get("secret")
        if not isinstance(value, str):
            continue
        is_secret = (
            name == "service_role"
            or typ in ("secret", "service_role")
            or value.startswith("sb_secret_")
        )
        if is_secret:
            secret = value
    return secret, names


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    env = load_env()
    token = env.get("SUPABASE_ACCESS_TOKEN", "")
    ref = project_ref(env)

    if not token.startswith("sbp_"):
        sys.exit("✗ SUPABASE_ACCESS_TOKEN (sbp_...) is missing from .env")
    if not ref:
        sys.exit("✗ Could not determine project ref — set SUPABASE_PROJECT_REF or SUPABASE_URL in .env")
    print(f"→ Project ref: {ref}")

    # 1) reveal + store the secret (service_role) key ------------------------------
    print("→ Fetching API keys via the Management API…")
    status, keys = api("GET", f"/v1/projects/{ref}/api-keys?reveal=true", token)
    if status != 200:
        sys.exit(f"✗ api-keys request failed ({status}): {keys}")
    secret, names = find_secret_key(keys if isinstance(keys, list) else [])
    if not secret:
        sys.exit(
            "✗ No service_role / secret key found in the response.\n"
            f"  Keys present: {names}\n"
            "  Open the dashboard → Project Settings → API keys, copy the "
            "sb_secret_… key, and paste it into .env as SUPABASE_KEY manually."
        )
    upsert_env("SUPABASE_KEY", secret)
    print("✓ Wrote the secret key into .env as SUPABASE_KEY (value not shown)")

    # 2) apply migrations (only if the schema isn't already there) -----------------
    status, resp = run_sql(ref, token, "select to_regclass('public.users') as t;")
    already = status == 200 and isinstance(resp, list) and resp and resp[0].get("t")
    if already:
        print("→ Schema already present (public.users exists) — skipping migrations.")
    else:
        files = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))
        if not files:
            sys.exit(f"✗ No .sql files found in {MIGRATIONS_DIR}")
        for f in files:
            name = Path(f).name
            sql = Path(f).read_text()
            print(f"→ Applying {name} ({len(sql):,} bytes)…")
            status, resp = run_sql(ref, token, sql)
            if status in (200, 201):
                print(f"  ✓ {name} ok")
                continue
            msg = resp if isinstance(resp, str) else json.dumps(resp)
            if "already exists" in msg:
                print(f"  ↳ {name}: objects already exist — treating as applied")
            else:
                sys.exit(f"✗ {name} failed ({status}): {msg[:900]}")

    # 3) verify --------------------------------------------------------------------
    status, resp = run_sql(
        ref,
        token,
        "select count(*)::int as n from information_schema.tables "
        "where table_schema = 'public';",
    )
    n = resp[0]["n"] if status == 200 and isinstance(resp, list) and resp else "?"
    print(f"\n✓ Done. The public schema now has {n} tables.")
    print("\nNext steps:")
    print("  1. Fill WHATSAPP_* in .env (verify token, API token, phone number id).")
    print("  2. In the dashboard → Storage, create a public/private bucket named 'media'.")
    print("  3. pip install -r requirements.txt  &&  uvicorn app.main:app --reload")
    print("  4. Rotate SUPABASE_ACCESS_TOKEN — it was only needed for this step.")


if __name__ == "__main__":
    main()
