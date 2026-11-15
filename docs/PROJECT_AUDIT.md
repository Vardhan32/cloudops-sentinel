# CloudOps Sentinel — Project Audit

**Date:** 2026-10-09 · **Baseline:** 29/29 tests passing · **Tree:** clean, `main` @ `a2046b1`
**Scope:** local-first FastAPI + SQLite + psutil monitoring app. No cloud, no paid services.

## Critical

1. **SSRF protection is string-matching only (`app/services.py`).**
   `check_service()` uses `httpx.Client(follow_redirects=True)` with no IP,
   DNS, or redirect-destination validation. A configured target (or any
   redirect it returns) can reach loopback, RFC-1918, link-local
   (incl. `169.254.169.254` cloud metadata), multicast, or reserved
   destinations. `validate_service_url()` never resolves hostnames.
2. **Docker image cannot serve traffic (`Dockerfile`).** `CMD` binds
   `127.0.0.1` inside the container, so published ports (`-p 8000:8000`)
   never reach the app — port forwarding targets the container's
   interface address, not its loopback. Must bind `0.0.0.0` in-container
   and keep host exposure localhost-only via Compose port mapping.
3. **Internal errors leak to API clients (`app/main.py`).**
   `f"Database error: {exc}"` returns raw driver messages (paths, SQL)
   with HTTP 500. Must log server-side and return a generic message.

## High

4. **Configuration is never validated (`app/config.py`).** Inverted
   (`WARN > CRIT`) or out-of-range thresholds are silently accepted,
   producing wrong severities. Startup validation required.
5. **Auto-seeded service target hardcodes port 8000 (`app/main.py`
   lifespan).** Running on any other port (e.g. `--port 8001`) creates a
   bogus `service_down` incident on first boot. Seed should go; dashboard
   should offer one-click fixture targets instead.
6. **No response-body cap or pool limits.** `client.get(url)` reads an
   unbounded body; default pool is shared without limits. Cap streamed
   reads (~256 KiB) and set explicit `httpx.Limits`.
7. **Dashboard polling can overlap (`static/app.js`).** `setInterval`
   fires while a previous fetch is still pending; failures use `alert()`;
   resolved incidents look identical to active ones.
8. **Dependencies are range-constrained, not pinned
   (`requirements.txt`).** Reproducibility risk across install dates.
9. **Container runs as root; no `.dockerignore`.** `.venv`, `.git`,
   caches, and local DBs would be sent as build context / baked in.
10. **`/api/metrics/current` stores snapshots but never evaluates
    thresholds.** Dashboard polls only produce incidents once the 30 s
    background loop fires — confusing during demos.

## Medium

11. **Acknowledge/resolve re-fetch the incident** (route loads it, then
    `monitor.*` loads it again) — TOCTOU-shaped; pass the object instead.
12. **`_utcnow()` triplicated; zero logging.** Failures are silent
    (`except Exception: db.rollback()` with no log).
13. **CI gaps (`.github/workflows/ci.yml`).** No Docker build, no
    `timeout-minutes`, no `permissions:`, no dependency/security check.
14. **README has `<your-username>` placeholders and a stale TODO
    checklist;** clone URLs don't point at the real repo.
15. **`delete_service` / check-result commits lack rollback safety.**
16. **Health badge mixes observed + simulated incidents** — acceptable for
    demo but must be documented (done in UI labels + docs).

## Low

17. Cosmetic `# type: ignore` comments; Starlette deprecation warning
    about `httpx` TestClient (upstream, ignore).
18. Lifespan seeding lacks rollback on failure.
19. CORS intentionally absent — keep, document.

## Secret / ignore scan

- `git ls-files` contains only source, static, tests, docs, Docker/CI.
  `data/sentinel.db` and `.pytest_cache/` are untracked + ignored. ✔
- Pattern scan (`ghp_|gho_|AKIA|PRIVATE|password =`) hit only the
  `file:///etc/passwd` SSRF-rejection test fixture — no real secrets. ✔

## Fix plan (in order)

1. Centralize `utcnow()` + logging; validate config at startup.
2. Harden `services.py`: resolving transport guard, redirect re-validation
   (max 3 hops, no auto-follow), body cap, pool limits, loopback-allow
   flag (default ON for offline demo, documented).
3. Backend reliability: generic 500s, rollback safety, object-passing
   incident transitions, incident evaluation on `/current`, drop auto-seed.
4. Tests: boundaries, empty-DB, 422s, DB-failure, SSRF matrix (mocked DNS),
   dashboard integration, monitor-cycle persistence.
5. Docker: non-root, `0.0.0.0`, `.dockerignore`, writable volume perms.
6. CI: timeouts, permissions, Docker build, non-blocking pip-audit.
7. Dashboard: overlap guard, toast feedback, resolved styling, fixture
   quick-add, full timestamps.
8. Docs: README rewrite (real URLs, examples), `ARCHITECTURE.md`,
   `SECURITY.md` with residual risks.
