"""
Fixed-width encoding / parsing — the ONLY module that knows the TMS wire format.

Derived from live transcripts against tramway.proxy.rlwy.net:17159.

LOAD_QUERY response line length : 246 bytes
LOAD_GET   response line length : 524 bytes

Filter field names (LOAD_QUERY): ORIG_CITY, ORIG_STATE, DEST_CITY, DEST_STATE, EQTYPE
Booking required fields         : LOAD_ID, MC_NUM, AGREED_RATE
Datetime wire format            : YYYYMMDDHHMMSS (14 chars, no separators)
Max-rate internal field         : MAX_BUY
"""

from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Command names
# ---------------------------------------------------------------------------

CMD_QUERY = "LOAD_QUERY"
CMD_GET   = "LOAD_GET"
CMD_BOOK  = "LOAD_BOOK"
CMD_ECHO  = "DEBUG_ECHO"

# ---------------------------------------------------------------------------
# Wire-format field widths (derived from transcript)
# Used by the mock server to emit correctly padded lines.
# ---------------------------------------------------------------------------

# Fields present in LOAD_QUERY summary response
QUERY_FIELD_WIDTHS: dict[str, int] = {
    "LOAD_ID":     12,
    "ORIG_CITY":   30,
    "ORIG_STATE":   2,
    "ORIG_ZIP":     5,
    "DEST_CITY":   30,
    "DEST_STATE":   2,
    "DEST_ZIP":     5,
    "PICKUP_DT":   14,
    "EQTYPE":      10,
    "RATE":         8,
    "MILES":        6,
    "STATUS":       8,
}

# Extra fields present only in LOAD_GET detail response
DETAIL_EXTRA_FIELD_WIDTHS: dict[str, int] = {
    "DELIVERY_DT": 14,
    "WEIGHT":       8,
    "COMMODITY":   40,
    "PIECES":       6,
    "DIMS":        35,
    "NOTES":      120,
    "MAX_BUY":      8,
}

# Full ordered field list for LOAD_GET responses (order matters for the mock)
DETAIL_FIELD_ORDER: list[str] = [
    "LOAD_ID", "ORIG_CITY", "ORIG_STATE", "ORIG_ZIP",
    "DEST_CITY", "DEST_STATE", "DEST_ZIP",
    "PICKUP_DT", "DELIVERY_DT",
    "EQTYPE", "RATE", "WEIGHT", "COMMODITY", "PIECES", "MILES",
    "DIMS", "NOTES", "STATUS", "MAX_BUY",
]

ALL_FIELD_WIDTHS = {**QUERY_FIELD_WIDTHS, **DETAIL_EXTRA_FIELD_WIDTHS}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_dt(raw: str) -> Optional[str]:
    """YYYYMMDDHHMMSS → ISO 8601 string, or None on blank/invalid input."""
    s = raw.strip()
    if len(s) != 14 or not s.isdigit():
        return None
    try:
        dt = datetime(
            int(s[0:4]), int(s[4:6]),  int(s[6:8]),
            int(s[8:10]), int(s[10:12]), int(s[12:14]),
        )
        return dt.isoformat()
    except ValueError:
        return None


def _fmt_dt(iso: str) -> str:
    """ISO 8601 string → YYYYMMDDHHMMSS wire format."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y%m%d%H%M%S")
    except ValueError:
        return "0" * 14


def _float_or_none(raw: str) -> Optional[float]:
    s = raw.strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _int_or_none(raw: str) -> Optional[int]:
    s = raw.strip()
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Request field builders
# ---------------------------------------------------------------------------

def search_fields(
    orig_city: Optional[str]  = None,
    orig_state: Optional[str] = None,
    dest_city: Optional[str]  = None,
    dest_state: Optional[str] = None,
    eqtype: Optional[str]     = None,
) -> dict[str, str]:
    """Build the extra-fields dict for a LOAD_QUERY command."""
    f: dict[str, str] = {}
    if orig_city:  f["ORIG_CITY"]  = orig_city
    if orig_state: f["ORIG_STATE"] = orig_state.upper()
    if dest_city:  f["DEST_CITY"]  = dest_city
    if dest_state: f["DEST_STATE"] = dest_state.upper()
    if eqtype:     f["EQTYPE"]     = eqtype.upper()
    return f


def get_fields(load_id: str) -> dict[str, str]:
    return {"LOAD_ID": load_id}


def book_fields(load_id: str, mc_num: str, agreed_rate: float) -> dict[str, str]:
    return {
        "LOAD_ID":     load_id,
        "MC_NUM":      mc_num,
        "AGREED_RATE": f"{agreed_rate:.2f}",
    }


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def parse_load_summary(raw: dict[str, str]) -> dict:
    """Parse one LOAD_QUERY record into a clean typed dict."""
    orig_city  = raw.get("ORIG_CITY",  "").strip()
    orig_state = raw.get("ORIG_STATE", "").strip()
    dest_city  = raw.get("DEST_CITY",  "").strip()
    dest_state = raw.get("DEST_STATE", "").strip()
    return {
        "load_id":         raw.get("LOAD_ID", "").strip(),
        "origin":          f"{orig_city}, {orig_state}" if orig_state else orig_city,
        "destination":     f"{dest_city}, {dest_state}" if dest_state else dest_city,
        "pickup_datetime": _parse_dt(raw.get("PICKUP_DT", "")),
        "equipment_type":  raw.get("EQTYPE", "").strip() or None,
        "loadboard_rate":  _float_or_none(raw.get("RATE",  "")),
        "miles":           _int_or_none(raw.get("MILES", "")),
    }


def parse_load_detail(raw: dict[str, str]) -> dict:
    """Parse one LOAD_GET record into a clean typed dict (full detail)."""
    orig_city  = raw.get("ORIG_CITY",  "").strip()
    orig_state = raw.get("ORIG_STATE", "").strip()
    dest_city  = raw.get("DEST_CITY",  "").strip()
    dest_state = raw.get("DEST_STATE", "").strip()
    return {
        "load_id":           raw.get("LOAD_ID", "").strip(),
        "origin":            f"{orig_city}, {orig_state}" if orig_state else orig_city,
        "destination":       f"{dest_city}, {dest_state}" if dest_state else dest_city,
        "pickup_datetime":   _parse_dt(raw.get("PICKUP_DT",   "")),
        "delivery_datetime": _parse_dt(raw.get("DELIVERY_DT", "")),
        "equipment_type":    raw.get("EQTYPE",    "").strip() or None,
        "loadboard_rate":    _float_or_none(raw.get("RATE",      "")),
        "max_rate":          _float_or_none(raw.get("MAX_BUY",   "")),
        "weight":            _float_or_none(raw.get("WEIGHT",    "")),
        "commodity_type":    raw.get("COMMODITY", "").strip() or None,
        "num_of_pieces":     _int_or_none(raw.get("PIECES",   "")),
        "miles":             _int_or_none(raw.get("MILES",    "")),
        "dimensions":        raw.get("DIMS",  "").strip() or None,
        "notes":             raw.get("NOTES", "").strip() or None,
    }


def parse_booking(raw: dict[str, str]) -> dict:
    """Parse a LOAD_BOOK success record."""
    return {
        "load_id":             raw.get("LOAD_ID",      "").strip(),
        "status":              raw.get("STATUS",       "").strip(),
        "confirmation_number": raw.get("CONF_NUM",     "").strip(),
        "agreed_rate":         _float_or_none(raw.get("AGREED_RATE", "")),
    }


# ---------------------------------------------------------------------------
# Mock-server helper: format one load record line
# ---------------------------------------------------------------------------

def format_record(data: dict[str, str], fields: list[str]) -> str:
    """Emit a pipe-delimited, fixed-width record line (for mock server use)."""
    parts: list[str] = []
    for field in fields:
        width = ALL_FIELD_WIDTHS.get(field, 20)
        value = str(data.get(field, ""))
        parts.append(f"{field}:{value[:width].ljust(width)}")
    return "|".join(parts)
