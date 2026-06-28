"""
TCP client for the legacy TMS.

Responsibilities:
- Open a fresh TCP connection per request (server lifecycle contract).
- Send a pipe-delimited request line, read response lines until END or ERR.
- Retry transient failures with exponential backoff.
- Map all socket/protocol failures to typed exceptions.

This module knows nothing about HTTP — it returns raw parsed dicts.
Field-level encoding/decoding lives in protocol.py.
"""

import logging
import socket
import time

from app.config import settings

log = logging.getLogger(__name__)

CRLF = b"\r\n"


# ---------------------------------------------------------------------------
# Typed exceptions — REST layer maps these to HTTP status codes
# ---------------------------------------------------------------------------

class TMSError(Exception):
    """Base class for all TMS client errors."""


class TMSConnectionError(TMSError):
    """Could not establish or maintain the TCP connection."""


class TMSTimeoutError(TMSError):
    """Socket timed out waiting for the TMS."""


class TMSAuthError(TMSError):
    """TMS rejected the auth token."""


class TMSNotFoundError(TMSError):
    """The requested load does not exist."""


class TMSAlreadyBookedError(TMSError):
    """Load has already been booked."""


class TMSProtocolError(TMSError):
    """Response did not conform to the expected framing."""


class TMSServerError(TMSError):
    """TMS returned a SERVER_ERROR or an unrecognized error code."""


# Map known TMS error codes to exception types.
# NOT_FOUND observed in practice; spec listed UNKNOWN_LOAD — both handled.
_ERR_MAP: dict[str, type[TMSError]] = {
    "AUTH_FAILED":    TMSAuthError,
    "UNKNOWN_LOAD":   TMSNotFoundError,
    "NOT_FOUND":      TMSNotFoundError,
    "ALREADY_BOOKED": TMSAlreadyBookedError,
    "SERVER_ERROR":   TMSServerError,
}


# ---------------------------------------------------------------------------
# Wire-level helpers
# ---------------------------------------------------------------------------

def _build_request(cmd: str, extra: dict[str, str]) -> bytes:
    """Encode one TMS request line (pipe-delimited KEY:VALUE pairs + CRLF)."""
    parts = [f"CMD:{cmd}", f"AUTH:{settings.tms_auth_token}"]
    parts += [f"{k}:{v}" for k, v in extra.items()]
    return ("|".join(parts) + "\r\n").encode("ascii")


def _parse_fields(line: bytes) -> dict[str, str]:
    """
    Parse one pipe-delimited response line into a dict.
    Each segment is split on the first colon only, so values may contain colons.
    """
    result: dict[str, str] = {}
    for segment in line.decode("ascii", errors="replace").split("|"):
        if ":" in segment:
            key, _, value = segment.partition(":")
            result[key.strip()] = value.strip()
    return result


def _raise_for_error(fields: dict[str, str]) -> None:
    """If the parsed line is an ERR response, raise the appropriate exception."""
    code = fields.get("CODE", "UNKNOWN")
    msg  = fields.get("MSG", "")
    exc_class = _ERR_MAP.get(code, TMSServerError)
    raise exc_class(f"TMS error {code}: {msg}")


def _do_exchange(request: bytes) -> list[dict[str, str]]:
    """
    Send *request* over a fresh TCP connection and return parsed response records.
    Raises TMSConnectionError / TMSTimeoutError / TMSProtocolError on failure.
    Does NOT retry — retries are handled by the caller.
    """
    try:
        sock = socket.create_connection(
            (settings.tms_host, settings.tms_port),
            timeout=settings.socket_timeout,
        )
    except (ConnectionRefusedError, OSError) as exc:
        raise TMSConnectionError(f"Cannot connect to TMS at {settings.tms_host}:{settings.tms_port}: {exc}") from exc

    records: list[dict[str, str]] = []

    try:
        with sock:
            sock.sendall(request)
            buf = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout as exc:
                    raise TMSTimeoutError("Socket timed out reading TMS response") from exc
                if not chunk:
                    break
                buf += chunk
                while CRLF in buf:
                    raw_line, buf = buf.split(CRLF, 1)
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue

                    if raw_line == b"END":
                        return records

                    fields = _parse_fields(raw_line)

                    if raw_line.startswith(b"ERR"):
                        _raise_for_error(fields)

                    records.append(fields)

            # Server closed without sending END — treat as a protocol error
            # unless we already have records (some implementations omit END on empty)
            if not records:
                raise TMSProtocolError("Connection closed before END or ERR received")
            return records

    except (TMSError, OSError):
        raise
    except Exception as exc:
        raise TMSProtocolError(f"Unexpected error during TMS exchange: {exc}") from exc


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def exchange(cmd: str, fields: dict[str, str] | None = None) -> list[dict[str, str]]:
    """
    Send a TMS command and return the list of parsed record dicts.
    Retries transient failures (connection errors, timeouts, server errors)
    with exponential backoff.  Auth/not-found/already-booked errors are not
    retried — they are deterministic failures.
    """
    request = _build_request(cmd, fields or {})
    last_exc: TMSError | None = None

    for attempt in range(settings.max_retries + 1):
        if attempt:
            delay = settings.retry_backoff_base * (2 ** (attempt - 1))
            log.warning(
                "TMS %s attempt %d/%d failed (%s); retrying in %.1fs",
                cmd, attempt, settings.max_retries, last_exc, delay,
            )
            time.sleep(delay)

        try:
            log.debug("TMS %s attempt %d fields=%s", cmd, attempt + 1, list((fields or {}).keys()))
            return _do_exchange(request)
        except (TMSAuthError, TMSNotFoundError, TMSAlreadyBookedError) as exc:
            # Deterministic — retrying won't help
            raise
        except TMSError as exc:
            last_exc = exc

    log.error("TMS %s failed after %d attempts: %s", cmd, settings.max_retries + 1, last_exc)
    raise last_exc  # type: ignore[misc]
