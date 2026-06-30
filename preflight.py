#!/usr/bin/env python3
"""
Preflight diagnostic — checks every EXTERNAL dependency this service relies on
(legacy TMS, FMCSA carrier-verification API, deployed public URL) and prints a
single PASS/FAIL/WARN/SKIP report.

Every check is isolated: one failing check never stops the others. Exit code
is 0 if no check FAILed, non-zero otherwise (SKIP and WARN don't affect it).

Usage:
    python preflight.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))  # allow `python preflight.py` from anywhere

import httpx
from dotenv import dotenv_values

from app import protocol
from app.config import settings
from app.tms_client import TMSAuthError, TMSConnectionError, TMSError, TMSTimeoutError, exchange

ENV_PATH = REPO_ROOT / ".env"
REQUIRED_VARS = ["TMS_HOST", "TMS_PORT", "TMS_AUTH_TOKEN", "API_AUTH_TOKEN"]
API_AUTH_TOKEN_MIN_LEN = 16

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_GREEN, _RED, _YELLOW, _BOLD, _RESET = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_STATUS_COLOR = {"PASS": _GREEN, "FAIL": _RED, "WARN": _YELLOW, "SKIP": _YELLOW}


def _c(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}" if _USE_COLOR else text


def mask(value: str, show_last: int = 4) -> str:
    """Mask a secret, showing only the last `show_last` characters."""
    if not value:
        return "(empty)"
    if len(value) <= show_last:
        return "*" * len(value)
    return "*" * (len(value) - show_last) + value[-show_last:]


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / FAIL / WARN / SKIP
    latency_ms: float | None
    detail: str


def run_check(name: str, fn) -> CheckResult:
    """Run one check, never letting it raise — a broken check is reported as FAIL."""
    t0 = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 — intentionally broad, this is a diagnostic tool
        elapsed = (time.perf_counter() - t0) * 1000
        return CheckResult(name, "FAIL", elapsed, f"unexpected error — {exc}")
    elapsed = (time.perf_counter() - t0) * 1000
    return CheckResult(name, status, elapsed, detail)


def format_row(result: CheckResult) -> str:
    tag = _c(f"[{result.status}]".ljust(6), _STATUS_COLOR[result.status])
    name = result.name.ljust(24)
    if result.status == "SKIP":
        extra = result.detail
    elif result.latency_ms is not None:
        extra = f"{result.latency_ms:.0f}ms"
        if result.detail:
            extra += f"  {result.detail}"
    else:
        extra = result.detail
    return f"  {tag} {name}{extra}"


# ---------------------------------------------------------------------------
# 1. Config sanity
# ---------------------------------------------------------------------------

def print_config_block() -> None:
    print(_c("Config (masked)", _BOLD))
    print(f"  TMS_HOST        = {settings.tms_host}")
    print(f"  TMS_PORT        = {settings.tms_port}")
    print(f"  TMS_AUTH_TOKEN  = {mask(settings.tms_auth_token)}")
    print(f"  API_AUTH_TOKEN  = {mask(settings.api_auth_token)}")
    print(f"  FMCSA_WEB_KEY   = {mask(settings.fmcsa_web_key)}")
    print(f"  FMCSA_TEST_DOT  = {settings.fmcsa_test_dot or '(not set)'}")
    print(f"  FMCSA_TEST_MC   = {settings.fmcsa_test_mc or '(not set)'}")
    print(f"  PUBLIC_BASE_URL = {settings.public_base_url or '(not set)'}")
    print()


def check_config_sanity() -> tuple[str, str]:
    on_disk = dict(dotenv_values(ENV_PATH))
    # A var counts as present if it's in .env OR injected via real process env
    # (e.g. docker-compose overrides TMS_HOST/TMS_PORT at runtime).
    merged = {k: on_disk.get(k) or os.environ.get(k) for k in REQUIRED_VARS}
    missing = [k for k, v in merged.items() if not v]

    if missing:
        return "FAIL", f"missing required var(s): {', '.join(missing)}"
    if settings.api_auth_token == "changeme":
        return "WARN", "API_AUTH_TOKEN is still the default 'changeme'"
    if len(settings.api_auth_token) < API_AUTH_TOKEN_MIN_LEN:
        return "WARN", (
            f"API_AUTH_TOKEN is only {len(settings.api_auth_token)} chars "
            f"(< {API_AUTH_TOKEN_MIN_LEN}) — too short/guessable for a production secret"
        )
    return "PASS", "all required vars present"


# ---------------------------------------------------------------------------
# 2. TMS connectivity — raw DEBUG_ECHO via the real client
# ---------------------------------------------------------------------------

def check_tms_connectivity() -> tuple[str, str]:
    try:
        exchange(protocol.CMD_ECHO, {"MSG": "preflight"})
    except TMSAuthError as exc:
        return "FAIL", f"AUTH_FAILED — token rejected ({exc})"
    except TMSTimeoutError:
        return "FAIL", f"timeout after {settings.socket_timeout:.0f}s"
    except TMSConnectionError as exc:
        return "FAIL", f"connection refused/unreachable — {exc}"
    except TMSError as exc:
        return "FAIL", str(exc)
    return "PASS", f"echoed OK ({settings.tms_host}:{settings.tms_port})"


# ---------------------------------------------------------------------------
# 3. TMS round-trip — a real LOAD_QUERY through the real client + parser
# ---------------------------------------------------------------------------

def check_tms_roundtrip() -> tuple[str, str]:
    # The real TMS rejects LOAD_QUERY with no filters at all (MISSING_FIELD),
    # unlike the mock server. ORIG_STATE=OR is a known-good broad filter —
    # see the captured transcript in probe.py.
    try:
        records = exchange(protocol.CMD_QUERY, protocol.search_fields(orig_state="OR"))
    except TMSError as exc:
        return "FAIL", str(exc)
    parsed = [protocol.parse_load_summary(r) for r in records]
    return "PASS", f"{len(parsed)} load(s)"


# ---------------------------------------------------------------------------
# 4. FMCSA web key — real request against the QCMobile API
# ---------------------------------------------------------------------------

def check_fmcsa() -> tuple[str, str]:
    if not settings.fmcsa_web_key:
        return "SKIP", "FMCSA_WEB_KEY not set"

    # QCMobile API: DOT lookups use /carriers/{dot}; MC/docket lookups use a
    # different path. They are NOT interchangeable — a DOT number plugged
    # into the docket-number endpoint (or vice versa) will 404.
    auth_only = not settings.fmcsa_test_dot and not settings.fmcsa_test_mc
    if auth_only:
        # No real carrier to verify against — use a syntactically valid
        # placeholder DOT purely to exercise auth. 401/403 still proves the
        # key is dead; anything else (200 or 404) proves the key is accepted.
        url = f"{settings.fmcsa_base_url}/carriers/1"
        id_desc = "placeholder DOT 1 (auth-only check)"
    elif settings.fmcsa_test_dot:
        url = f"{settings.fmcsa_base_url}/carriers/{settings.fmcsa_test_dot}"
        id_desc = f"DOT {settings.fmcsa_test_dot}"
    else:
        url = f"{settings.fmcsa_base_url}/carriers/docket-number/{settings.fmcsa_test_mc}"
        id_desc = f"MC/docket {settings.fmcsa_test_mc}"

    try:
        resp = httpx.get(url, params={"webKey": settings.fmcsa_web_key}, timeout=10.0)
    except httpx.ConnectError as exc:
        return "FAIL", f"connection failed — {exc}"
    except httpx.TimeoutException:
        return "FAIL", "timeout after 10s"

    if resp.status_code == 401:
        return "FAIL", f"401 Unauthorized — web key rejected (queried {id_desc})"
    if resp.status_code == 403:
        return "FAIL", f"403 Forbidden — web key invalid/expired (queried {id_desc})"

    if auth_only:
        # Any non-401/403 response means FMCSA accepted the key itself.
        if resp.status_code in (200, 404):
            return "PASS", (
                f"key accepted by FMCSA (HTTP {resp.status_code} on {id_desc}) — "
                "set FMCSA_TEST_DOT or FMCSA_TEST_MC for a full carrier-lookup test"
            )
        return "FAIL", f"{resp.status_code} — {resp.text[:120]!r}"

    if resp.status_code == 404:
        return "FAIL", f"404 Not Found — {id_desc} not found, or wrong endpoint for this ID type"
    if resp.status_code != 200:
        return "FAIL", f"{resp.status_code} — {resp.text[:120]!r}"

    try:
        data = resp.json()
    except ValueError:
        return "FAIL", f"200 OK but non-JSON response — {resp.text[:120]!r}"

    content = data.get("content")
    carrier = None
    if isinstance(content, dict):
        carrier = content.get("carrier", content)
    elif isinstance(content, list) and content:
        first = content[0]
        carrier = first.get("carrier", first) if isinstance(first, dict) else None

    if not carrier:
        return "FAIL", f"200 OK but no carrier record in response — check {id_desc} is valid"

    name = carrier.get("legalName") or carrier.get("dbaName") or "(unnamed)"
    return "PASS", f"{name} (queried {id_desc})"


# ---------------------------------------------------------------------------
# 5. Public deployment URL — optional
# ---------------------------------------------------------------------------

def check_public_url() -> tuple[str, str]:
    if not settings.public_base_url:
        return "SKIP", "PUBLIC_BASE_URL not set — not deployed yet"

    base = settings.public_base_url.rstrip("/")

    try:
        health = httpx.get(f"{base}/health", timeout=10.0)
    except httpx.ConnectError as exc:
        return "FAIL", f"connection failed — {exc}"
    except httpx.TimeoutException:
        return "FAIL", "timeout after 10s"

    if health.status_code != 200:
        return "FAIL", f"GET /health returned {health.status_code}"
    try:
        if health.json().get("status") != "ok":
            return "FAIL", f"GET /health unexpected body — {health.text[:120]!r}"
    except ValueError:
        return "FAIL", f"GET /health non-JSON response — {health.text[:120]!r}"

    try:
        search = httpx.post(
            f"{base}/loads/search",
            json={"orig_state": "OR"},
            headers={"Authorization": f"Bearer {settings.api_auth_token}"},
            timeout=15.0,
        )
    except httpx.ConnectError as exc:
        return "FAIL", f"/health OK but POST /loads/search connection failed — {exc}"
    except httpx.TimeoutException:
        return "FAIL", "/health OK but POST /loads/search timed out after 15s"

    if search.status_code == 401:
        return "FAIL", "/health OK but /loads/search returned 401 — API_AUTH_TOKEN mismatch with deployed service"
    if search.status_code in (502, 504):
        return "FAIL", f"/health OK but /loads/search returned {search.status_code} — deployed service can't reach TMS"
    if search.status_code != 200:
        return "FAIL", f"/health OK but /loads/search returned {search.status_code}"

    return "PASS", "/health + authenticated TMS round-trip OK"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CHECKS = [
    ("Config sanity", check_config_sanity),
    ("TMS connectivity", check_tms_connectivity),
    ("TMS round-trip", check_tms_roundtrip),
    ("FMCSA API key", check_fmcsa),
    ("Public URL", check_public_url),
]


def main() -> int:
    print()
    print_config_block()

    results = [run_check(name, fn) for name, fn in CHECKS]

    print(_c("PREFLIGHT REPORT", _BOLD))
    for result in results:
        print(format_row(result))

    passed = sum(r.status == "PASS" for r in results)
    failed = sum(r.status == "FAIL" for r in results)
    warned = sum(r.status == "WARN" for r in results)
    skipped = sum(r.status == "SKIP" for r in results)

    print("  " + "-" * 52)
    parts = [f"{passed} passed"]
    if warned:
        parts.append(f"{warned} warned")
    parts.append(f"{failed} failed")
    parts.append(f"{skipped} skipped")
    print("  " + ", ".join(parts))
    print()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
