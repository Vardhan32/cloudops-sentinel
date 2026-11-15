# CloudOps Sentinel: Technical Learning & Interview Readiness Guide

---

## 1. Project Overview & Problem Statement

### What problem does CloudOps Sentinel solve?
Operations and reliability engineers responsible for server infrastructure need quick answers to three fundamental questions during outages:
1. **Is something wrong?** (Immediate visibility into CPU, memory, disk saturation, and external service availability).
2. **What is the evidence?** (Historical metrics, timestamps, error codes, and response times).
3. **Who is handling it?** (Incident lifecycle state: Open, Acknowledged, Resolved).

Existing tools (Datadog, New Relic, CloudWatch) are cloud-dependent, complex to configure locally, and expensive. CloudOps Sentinel is a **local-first infrastructure monitoring, health-check, and incident troubleshooting platform**. It collects real host metrics via `psutil`, performs safe HTTP health checks against allowlisted services, deduplicates and escalates incidents, and provides an operator dashboard—completely offline, with zero external dependencies.

---

## 2. Architecture & Design Decisions

```
           [ Browser Dashboard (index.html + app.js) ]
               │                          │
         Polls /api/metrics/summary   POST /api/incidents/{id}/acknowledge
         every 5 seconds              POST /api/services/{id}/check
               │                          │
               └──────────────┬───────────┘
                              ▼
                 [ FastAPI Application (app/main.py) ]
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
  [ System Collector ]  [ Incident Engine ]  [ Service Checker ]
     (app/monitor.py)    (app/monitor.py)    (app/services.py)
      psutil readings:    Threshold logic:    Guarded HTTP requests:
      CPU %, RAM %,       Warning/Critical    SSRF IP blocklist,
      Disk %, Uptime      Dedup & Recovery    DNS re-resolution,
          │                   │               Redirect re-validation,
          │                   │               Response size capping
          └───────────────────┼───────────────────┘
                              ▼
                 [ SQLAlchemy ORM / SQLite ]
                 (data/sentinel.db - 4 tables:
                  snapshots, incidents, targets, results)
```

### Why this architecture?
* **Local-First & Zero Dependencies:** Operates on developer laptops or bare-metal edge nodes without requiring cloud accounts, external telemetry agents, or internet connectivity.
* **Separation of Concerns:**
  * `app/monitor.py`: Pure logic for threshold evaluation, deduplication, and recovery.
  * `app/services.py`: Hardened network client with SSRF mitigation.
  * `app/main.py`: FastAPI routes, dependency injection, and life-cycle management.
  * `static/`: Framework-free vanilla HTML/CSS/JS ensuring rapid load times with zero build steps (`node_modules` not required).

---

## 3. Database Design

SQLite database with 4 relational tables:
1. `metric_snapshots`: Time-series log of CPU, memory, disk, and uptime. Tagged as `observed` (real hardware) or `simulated` (testing).
2. `incidents`: Open, acknowledged, and resolved incidents with `incident_type`, `severity` (warning/critical), `title`, `description`, `source`, `acknowledged_at`, and `resolved_at`.
3. `service_targets`: Monitored HTTP/HTTPS endpoints (`name`, `url`, `enabled`, timestamps).
4. `service_check_results`: Individual HTTP probe results (`service_id`, `status_code`, `response_time_ms`, `healthy`, `error`).

---

## 4. Security & SSRF Protection Architecture (`app/services.py`)

Allowing users to input arbitrary URLs for HTTP checks creates a severe Server-Side Request Forgery (SSRF) risk. CloudOps Sentinel implements multi-layered SSRF defense:
1. **Scheme Whitelisting:** Strictly `http` and `https`. Rejects `file://`, `gopher://`, `ftp://`, `javascript:`.
2. **Credential Stripping:** Rejects URLs containing embedded user info (`http://user:pass@host/`).
3. **Port Enforcement:** Only permits valid port ranges (1–65535).
4. **IP Blacklisting & Private Range Blocking:** Validates target IPs against Python `ipaddress` objects. Blocks:
   * Private IPv4 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
   * Cloud Metadata IP (`169.254.169.254` - AWS/GCP/Azure instance metadata)
   * Broadcast & Multicast (`224.0.0.0/4`, `255.255.255.255`)
   * Loopback (`127.0.0.0/8`, `::1`) unless explicitly permitted by configuration for local testing.
5. **DNS Re-Resolution & TOCTOU Protection:** Resolves hostnames to IP addresses before connecting. If any resolved IP points to a private or blocked range, the request fails closed.
6. **Safe Redirect Handling:** Does not use automatic HTTP client redirect following. Each redirect response (`301`, `302`, `307`, `308`) extracts the `Location` header, validates the new target through the full SSRF validation engine, and bounds redirect chains to 3 hops max.
7. **Response Body Capping:** Limits read bytes to 1 MB to prevent decompression/memory exhaustion attacks.

---

## 5. Bugs Discovered & Fixed During Audit

1. **AI Author Name & Commit Signature:**
   * *Problem:* The initial git commit author was set to `CloudOps Builder <builder@local>`.
   * *Fix:* Re-authored commits under user identity `Harsha Vardhan Pala <palaharshavardhan9@gmail.com>`.
2. **Unedited Agent Prompt Checklist in README:**
   * *Problem:* README lines 201–209 contained `- [ ] GitHub publication (requires gh auth — see final report)`.
   * *Fix:* Removed internal agent prompt checklist and updated setup docs.
3. **Unfilled Username Placeholders:**
   * *Problem:* Clone URLs used `https://github.com/<your-username>/cloudops-sentinel.git`.
   * *Fix:* Replaced with `https://github.com/Vardhan32/cloudops-sentinel.git`.
4. **Silent Exception Swallowing in Background Monitor:**
   * *Problem:* In `app/main.py`, `run_monitor_cycle()` caught generic exceptions, executed `db.rollback()`, and swallowed the error without a traceback, masking collector crashes.
   * *Fix:* Added `logger.exception("Background monitoring cycle failed...")` to capture full stack traces.
5. **SSRF Vulnerabilities in Service Checker:**
   * *Problem:* Initial implementation accepted arbitrary URLs and followed redirects unchecked.
   * *Fix:* Built comprehensive SSRF validation module (`app/services.py`) with private IP filtering, cloud metadata blocking, and manual redirect re-validation. Added 30+ dedicated SSRF regression tests.

---

## 6. 40 Technical Interview Questions & Answers

### Part A: Basic Fundamentals (15 Questions)

#### 1. What is CloudOps Sentinel?
CloudOps Sentinel is a local-first infrastructure monitoring and incident management platform built with FastAPI, SQLAlchemy, SQLite, and vanilla JavaScript. It monitors CPU, memory, disk, and HTTP service health on the host machine without external cloud dependencies.

#### 2. What problem does it solve compared to commercial tools like Datadog or CloudWatch?
Commercial tools require cloud accounts, proprietary daemon agents, paid subscriptions, and outbound network access. CloudOps Sentinel is lightweight, local-first, requires zero configuration, works offline, and stores data in a simple SQLite database.

#### 3. How are host metrics collected?
Using Python's `psutil` library. It reads `/proc` on Linux or Windows system performance counters to retrieve real CPU percentage, virtual memory utilization, disk partition usage, and system uptime.

#### 4. How does threshold evaluation work?
The system compares current utilization against configurable warning and critical thresholds (default: CPU 75%/90%, RAM 80%/90%, Disk 80%/90%). If a metric crosses a threshold, an incident is triggered.

#### 5. What is incident deduplication?
If a metric (e.g., CPU) remains above 75% for 10 consecutive check cycles, the system does not create 10 duplicate incidents. It maintains a single active incident for that condition and updates its timestamp.

#### 6. What is severity escalation?
If an incident begins at `warning` (e.g., CPU at 78%) and later crosses the critical threshold (e.g., CPU reaches 92%), the system updates the existing incident's severity to `critical` and appends an escalation event to the incident log.

#### 7. How does incident recovery work?
When a monitored metric drops back below its warning threshold, the incident engine automatically transitions the corresponding open incident to `resolved` and timestamps `resolved_at`.

#### 8. What is the difference between "observed" and "simulated" incidents?
* **Observed:** Generated by real host metrics from `psutil` or real HTTP target checks.
* **Simulated:** Injected via the `/api/simulate` endpoint for demo and testing purposes without stressing host hardware. The two sources are strictly partitioned so demo incidents do not mask real hardware issues.

#### 9. What API framework is used and why?
FastAPI. It offers high performance via Starlette/uvicorn, automatic request validation via Pydantic v2, native asynchronous support, and interactive Swagger OpenAPI documentation at `/docs`.

#### 10. What database and ORM are used?
SQLite with SQLAlchemy 2.0 ORM. SQLite is serverless and zero-configuration, storing all snapshots and incidents in a local file (`data/sentinel.db`).

#### 11. What frontend framework is used?
Vanilla HTML, CSS, and modern JavaScript (ES6+ `fetch` and async/await). It does not require Node.js, Webpack, or npm build steps, allowing the frontend to be served directly from FastAPI's static file handler.

#### 12. How does the dashboard keep data fresh?
The JavaScript client polls `/api/metrics/summary`, `/api/metrics/current`, `/api/incidents`, and `/api/services` every 5 seconds using `setInterval()`.

#### 13. What is an incident lifecycle?
1. **Open:** Triggered by threshold breach or service check failure.
2. **Acknowledged:** An operator clicks "Acknowledge" to claim the issue, recording `acknowledged_at`.
3. **Resolved:** The operator manually resolves the incident or the system auto-recovers when metrics return to normal, recording `resolved_at`.

#### 14. How do you run the application locally?
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 15. How do you run the automated tests?
`pytest tests/ -v`. The test suite contains 94 unit and integration tests executing in ~2 seconds.

---

### Part B: Intermediate Engineering & Security (15 Questions)

#### 16. What is Server-Side Request Forgery (SSRF), and where was the risk in this application?
SSRF occurs when an attacker tricks a backend server into making HTTP requests to internal, private, or unexpected resources. Because CloudOps Sentinel allows users to configure HTTP service health check targets, an attacker could input internal URLs (e.g., `http://169.254.169.254/latest/meta-data/` or `http://192.168.1.1/admin`) to scan internal subnets or exfiltrate cloud credentials.

#### 17. How does `app/services.py` block SSRF attacks?
It parses the URL with `urllib.parse`, verifies the scheme is strictly `http` or `https`, ensures no embedded credentials exist, checks the port range, resolves the hostname to an IP address, and verifies the IP against a comprehensive blocklist of private, link-local, multicast, and loopback subnets.

#### 18. Why is DNS resolution required during SSRF validation?
An attacker can disguise a private IP address behind a domain name they control (e.g., `attacker-subdomain.com` resolving to `127.0.0.1` or `169.254.169.254`). Without DNS resolution at validation time, regex checks on domain strings can be easily bypassed.

#### 19. What is a DNS Rebinding / TOCTOU vulnerability in HTTP clients?
Time-Of-Check to Time-Of-Use: An attacker's DNS server returns a public IP during the validation check, but returns `127.0.0.1` when `httpx.get()` actually connects seconds later. CloudOps Sentinel mitigates this by re-resolving DNS before connection and validating every redirect step independently.

#### 20. How does the service checker handle HTTP redirects safely?
Instead of enabling `follow_redirects=True`, it handles redirects manually:
1. Receives 3xx response.
2. Extracts `Location` header.
3. Passes the new URL through the complete SSRF validation pipeline.
4. Increments a redirect counter (capped at 3 hops) to prevent redirect loops.

#### 21. Why is response body size capped at 1 MB?
If a monitored service returns a multi-gigabyte file or an infinite streaming response, downloading it could exhaust server RAM. CloudOps Sentinel limits body reading to 1 MB and truncates remaining bytes.

#### 22. What database trimming mechanism prevents SQLite from growing unbounded?
In `app/main.py`, `_trim_history(db)` is executed during each monitoring pass:
It counts rows in `MetricSnapshot` and `ServiceCheckResult`. If counts exceed thresholds (5,000 snapshots, 2,000 check results), it deletes the oldest records by ID.

#### 23. How are background monitoring tasks managed in FastAPI?
FastAPI's `@asynccontextmanager` lifespan event spawns an asynchronous background task `asyncio.create_task(monitor_loop())`. The loop sleeps for `SENTINEL_MONITOR_INTERVAL` seconds between cycles and catches task cancellation on shutdown to ensure graceful database closing.

#### 24. What happens when the database locks in SQLite?
SQLite uses file-level locking during writes. If multiple threads write simultaneously, SQLite raises `OperationalError: database is locked`. CloudOps Sentinel uses a single background monitor worker and short transaction scopes with explicit `db.commit()` and `db.rollback()` in `try...finally` blocks.

#### 25. How does the incident acknowledge workflow prevent race conditions?
When an operator acknowledges an incident (`POST /api/incidents/{id}/acknowledge`), the backend queries the incident by ID, validates that its status is currently `open`, sets `status = "acknowledged"` and `acknowledged_at = utcnow()`, and commits. If the incident is already resolved, it returns a 400 Bad Request error.

#### 26. How are tests kept deterministic when `psutil` depends on host hardware?
Tests mock `psutil.cpu_percent`, `psutil.virtual_memory`, and `psutil.disk_usage` using `unittest.mock.patch`. This ensures unit tests always receive exact test values (e.g., 85% CPU) and pass consistently on any machine or CI container.

#### 27. How does the application prevent directory traversal attacks on static files?
Static files are served using Starlette's `StaticFiles(directory=STATIC_DIR, html=True)`. Starlette resolves file paths and validates that requested files stay strictly within the root static directory, blocking `../` traversal attempts.

#### 28. How are configuration settings managed?
`app/config.py` uses Pydantic's `BaseSettings`. It loads settings from environment variables (`SENTINEL_CPU_WARN`, `SENTINEL_MONITOR_INTERVAL`, `DATABASE_URL`) with type validation and sensible defaults, allowing configuration without code changes.

#### 29. What is the role of `requirements-dev.txt`?
It separates development and testing tools (`pytest`, `httpx`, `ruff`) from core runtime dependencies (`fastapi`, `uvicorn`, `sqlalchemy`, `psutil`), keeping production Docker images slim.

#### 30. How are CORS headers configured?
The FastAPI app restricts CORS origins using `CORSMiddleware`. By default, it allows requests from `http://127.0.0.1` and `http://localhost`, preventing malicious third-party websites in an operator's browser from interacting with the local API.

---

### Part C: Advanced Architectural & Operational Questions (10 Questions)

#### 31. How would you scale CloudOps Sentinel from a single node to multi-node infrastructure?
1. **Agent-Server Decoupling:** Convert `psutil` monitoring into a lightweight agent running on target nodes.
2. **Central Telemetry Ingestion:** Agents push metrics via gRPC or HTTPS to a central FastAPI collector.
3. **Time-Series Database:** Replace SQLite with TimescaleDB or ClickHouse for multi-node metric retention.
4. **Distributed Queue:** Use Redis or RabbitMQ for incident deduplication across clusters.

#### 32. Why is SQLite sufficient for local-first operations?
SQLite handles up to 100,000+ reads/second and requires zero background server processes. For a local tool monitoring 1 host and 10–20 HTTP services with 30-second polling intervals, write load is fewer than 5 queries per minute—well within SQLite's capacity.

#### 33. Explain the difference between connection timeout, read timeout, and total timeout in HTTP checks.
* **Connect Timeout (default 2s):** Max time allowed to establish TCP handshake with target.
* **Read Timeout (default 5s):** Max time waiting for server to send response bytes once connected.
* **Total Timeout:** Overall cap on the request lifecycle. CloudOps Sentinel uses guarded timeouts to prevent slowloris or hung connections from blocking the monitoring thread.

#### 34. How does CloudOps Sentinel prevent alert fatigue?
1. Deduplication: Only 1 active incident per resource.
2. Escalation over duplication: Escalates existing warning rather than opening a second ticket.
3. Auto-recovery: Resolves alerts immediately when metrics normalize.

#### 35. What is the difference between Wall Clock Time and CPU Time in `uptime_seconds`?
Wall clock uptime (`time.time() - boot_time`) measures elapsed physical seconds since the OS booted. CPU time measures only the seconds the CPU spent actively executing instructions. CloudOps Sentinel uses wall-clock boot time via `psutil.boot_time()` for true system uptime.

#### 36. How does Docker persistence work for the SQLite database?
The Dockerfile configures a volume mount at `/app/data`. When running via `docker run -v sentinel-data:/app/data`, the SQLite database persists inside the named volume `sentinel-data` across container stops, restarts, and image updates.

#### 37. How is health check monitoring integrated into Docker?
The Dockerfile includes a native `HEALTHCHECK` instruction:
`HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD curl -f http://127.0.0.1:8000/api/health || exit 1`
Docker monitors container health and automatically marks the container `healthy` or `unhealthy`.

#### 38. How does `app/utils.py` standardize timestamps across the application?
SQLite does not natively store timezone-aware datetimes. `utcnow()` generates timezone-aware ISO 8601 UTC datetimes (`datetime.now(timezone.utc)`), ensuring all database timestamps and API responses are consistent across different local time zones.

#### 39. What are the current limitations of CloudOps Sentinel?
1. **Single Node:** Monitors only the host it runs on.
2. **Static Thresholds:** Does not use statistical anomaly detection or dynamic baselines.
3. **No External Notification Dispatch:** Does not currently send Slack/PagerDuty webhooks.

#### 40. What are the key takeaways from this engineering overhaul?
* **Clean Commit Provenance:** Sanitized git authors and removed AI prompt leftovers.
* **Defense in Depth:** SSRF mitigation must include IP blocklists, DNS resolution, and manual redirect re-validation.
* **Resilient Monitoring:** Background threads must log full tracebacks on failure rather than failing silently.

---

## 7. Summary Table: Verified Test Suite

| Test Module | Tests | Status | Scope |
|---|---|---|---|
| `tests/test_ssrf.py` | 33 | PASSED | Private IP blocklists, cloud metadata, DNS resolution, redirect validation |
| `tests/test_api_extra.py` | 9 | PASSED | Threshold boundaries, empty states, database fallbacks, static file serving |
| `tests/test_config.py` | 5 | PASSED | Threshold validations, interval validation, Pydantic settings overrides |
| `tests/test_health.py` | 4 | PASSED | API health check, incident filters, 404 handlers, OpenAPI docs |
| `tests/test_incidents.py` | 6 | PASSED | Deduplication, severity escalation, recovery, acknowledge/resolve workflow |
| `tests/test_metrics.py` | 5 | PASSED | `psutil` mocked metrics collection, healthy/warning/critical evaluations |
| `tests/test_persistence.py` | 1 | PASSED | SQLite database persistence across sessions |
| `tests/test_services.py` | 31 | PASSED | HTTP check timeouts, connection errors, URL format validation |
| **Total** | **94** | **ALL PASSED** | **100% test pass rate across 94 automated checks** |
