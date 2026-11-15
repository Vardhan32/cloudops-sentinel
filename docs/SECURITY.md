# Security

This is a **local-first demo tool**, not a production-hardened monitor.
What follows is what is enforced, what is assumed, and what remains risky.

## Enforced

- **Allowlisted targets only.** No endpoint accepts an arbitrary URL to
  probe. `POST /api/services` validates before storing; checks request
  only the stored URL.
- **Per-request destination validation** (`app/services.py`):
  1. Syntax: `http`/`https` only, hostname required, no credentials, no
     whitespace/control chars, port 1–65535, length ≤ 1024.
  2. Literal IPs checked against an explicit blocklist; hostnames resolved
     via `getaddrinfo` and **every** returned address must pass — one
     blocked address (or resolution failure) fails the whole check.
  3. Blocked: unspecified, loopback (unless `SENTINEL_ALLOW_LOOPBACK=1`),
     RFC-1918, CGNAT `100.64.0.0/10`, link-local `169.254.0.0/16`
     (covers cloud metadata `169.254.169.254`), multicast, reserved,
     broadcast, documentation ranges — IPv4, IPv6, and IPv4-mapped IPv6.
  4. Redirects never auto-followed: each hop (max 3) is re-validated;
     unsafe hops abort with `Blocked redirect destination`.
- **Bounded work:** 5 s default timeout, pool limits (10 connections),
  streamed bodies capped at 256 KiB.
- **Safe defaults:** binds `127.0.0.1` locally; container binds `0.0.0.0`
  only inside its netns with host exposure pinned to localhost in Compose;
  no CORS; no `--reload`/debug in production paths; non-root container user.
- **No secret/error leakage:** DB failures return a generic message and
  log server-side; `data/`, `.env`, caches are git-ignored; secret-pattern
  scan of tracked files is clean (only the `file:///etc/passwd`
  *rejection-test* fixture matches naive patterns).

## Assumed (trust boundary)

- The operator configuring service targets is trusted; the policy guards
  against malicious *destinations* (compromised DNS, evil redirects), not
  against a malicious operator.
- The local DNS resolver is trusted.

## Residual risks (not claimed fixed)

1. **DNS rebinding:** validation and connect are separate steps; a
   fast-flux hostname could resolve safely, then to an internal IP at
   connect time. Mitigation: re-validation immediately pre-connect narrows
   the window; only configure trustworthy targets.
2. **No authentication or multi-tenancy.** Anyone reaching the port can
   ack/resolve incidents and add targets. Acceptable on loopback; do not
   expose to a network without adding auth.
3. **Single-node SQLite**, no audit log of operator actions.
4. `pip-audit` in CI is informational (`continue-on-error`) to avoid noisy
   red builds; review its output manually.

## Verifying yourself

```powershell
pytest tests/test_ssrf.py -v   # 40+ cases: blocklists, mocked DNS, redirects, body cap
```
