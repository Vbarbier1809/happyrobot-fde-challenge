#!/usr/bin/env python3
"""
Raw TCP probe — Step 2 checkpoint.

Sends a DEBUG_ECHO (connectivity test) then a bare LOAD_QUERY to the TMS
and prints every raw byte that comes back.  No parsing — the goal is to
capture a real transcript so we can derive field names and widths for
protocol.py in step 3.

Usage:
    python probe.py                    # uses .env in the current directory
    TMS_HOST=x TMS_PORT=9000 python probe.py
"""

import os
import socket
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Minimal env loading (no third-party deps needed for this probe)
# ---------------------------------------------------------------------------

def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

TMS_HOST    = os.environ.get("TMS_HOST", "localhost")
TMS_PORT    = int(os.environ.get("TMS_PORT", "9000"))
TMS_TOKEN   = os.environ.get("TMS_AUTH_TOKEN", "")
TIMEOUT     = float(os.environ.get("SOCKET_TIMEOUT", "10"))

CRLF = b"\r\n"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def build_request(cmd: str, **fields: str) -> bytes:
    """Encode a single request line in the TMS pipe-delimited format."""
    parts = [f"CMD:{cmd}", f"AUTH:{TMS_TOKEN}"]
    parts += [f"{k}:{v}" for k, v in fields.items()]
    return ("|".join(parts) + "\r\n").encode("ascii")


def raw_exchange(request: bytes, timeout: float = TIMEOUT) -> list[bytes]:
    """
    Open a fresh TCP connection, send *request*, collect all response lines
    until the server closes the connection, and return them as raw bytes.
    One connection per call — matches server's lifecycle contract.
    """
    lines: list[bytes] = []
    with socket.create_connection((TMS_HOST, TMS_PORT), timeout=timeout) as sock:
        sock.sendall(request)
        buf = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                print("  [WARN] socket timed out waiting for more data", file=sys.stderr)
                break
            if not chunk:          # server closed connection
                break
            buf += chunk
            # Yield complete lines as they arrive
            while CRLF in buf:
                line, buf = buf.split(CRLF, 1)
                lines.append(line)
    if buf:                        # any trailing bytes without CRLF
        lines.append(buf)
    return lines


# ---------------------------------------------------------------------------
# Probe runs
# ---------------------------------------------------------------------------

def probe(label: str, cmd: str, **fields: str) -> None:
    request = build_request(cmd, **fields)
    print(f"\n{'='*60}")
    print(f"PROBE: {label}")
    print(f"{'='*60}")
    print(f"  → SENT ({len(request)} bytes):")
    print(f"    {request!r}")
    print()

    t0 = time.perf_counter()
    try:
        lines = raw_exchange(request)
    except ConnectionRefusedError:
        print(f"  [ERROR] Connection refused — is the TMS reachable at {TMS_HOST}:{TMS_PORT}?")
        return
    except OSError as exc:
        print(f"  [ERROR] {exc}")
        return
    elapsed = time.perf_counter() - t0

    print(f"  ← RECEIVED {len(lines)} line(s) in {elapsed:.3f}s:")
    for i, line in enumerate(lines, 1):
        print(f"    [{i:02d}] {line!r}")
        print(f"         (len={len(line)})")


def main() -> None:
    print(f"\nTMS Probe  —  {TMS_HOST}:{TMS_PORT}")
    print(f"Token set: {'yes' if TMS_TOKEN else 'NO — TMS_AUTH_TOKEN is empty'}")

    probe("DEBUG_ECHO", "DEBUG_ECHO", MSG="hello")

    # --- LOAD_GET with real IDs found from LOAD_QUERY DEST_CITY=Dallas ---
    probe("LOAD_GET LD00314", "LOAD_GET", LOAD_ID="LD00314")
    probe("LOAD_GET LD00343", "LOAD_GET", LOAD_ID="LD00343")

    # --- LOAD_QUERY: probe remaining filter names using response field names ---
    probe("QUERY ORIG_STATE=OR",          "LOAD_QUERY", ORIG_STATE="OR")
    probe("QUERY DEST_STATE=TX",          "LOAD_QUERY", DEST_STATE="TX")
    probe("QUERY EQTYPE=FLATBED",         "LOAD_QUERY", EQTYPE="FLATBED")
    probe("QUERY ORIG_CITY+EQTYPE",       "LOAD_QUERY", ORIG_CITY="Salem", EQTYPE="POWER_ONLY")

    # --- LOAD_BOOK: try booking LD00314 ---
    probe("LOAD_BOOK LD00314 RATE=3500",  "LOAD_BOOK",  LOAD_ID="LD00314", RATE="3500")
    # try again — should get ALREADY_BOOKED (or another error code)
    probe("LOAD_BOOK LD00314 again",      "LOAD_BOOK",  LOAD_ID="LD00314", RATE="3500")


if __name__ == "__main__":
    main()
