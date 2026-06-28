"""
Mock TMS server — speaks the same fixed-width TCP protocol as the real TMS.

Usage:
    python -m mock_tms.server                        # default port 9000, token "test-token"
    python -m mock_tms.server --port 9100 --token s3cr3t
    python -m mock_tms.server --chaos                # inject random faults (30 % rate)
    python -m mock_tms.server --chaos --chaos-rate 0.5
"""

import argparse
import logging
import random
import socketserver
import threading
import time

from app.protocol import DETAIL_FIELD_ORDER, format_record

log = logging.getLogger("mock_tms")

# ---------------------------------------------------------------------------
# Sample load data
# ---------------------------------------------------------------------------

_LOADS: dict[str, dict] = {
    "LD00001": {
        "LOAD_ID": "LD00001", "ORIG_CITY": "Chicago", "ORIG_STATE": "IL",
        "ORIG_ZIP": "60601", "DEST_CITY": "Dallas", "DEST_STATE": "TX",
        "DEST_ZIP": "75201", "PICKUP_DT": "20260701080000",
        "DELIVERY_DT": "20260702200000", "EQTYPE": "FLATBED",
        "RATE": "1250", "MAX_BUY": "1450", "WEIGHT": "42000",
        "COMMODITY": "Steel Coils", "PIECES": "8", "MILES": "921",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "", "STATUS": "OPEN",
    },
    "LD00002": {
        "LOAD_ID": "LD00002", "ORIG_CITY": "Los Angeles", "ORIG_STATE": "CA",
        "ORIG_ZIP": "90001", "DEST_CITY": "Phoenix", "DEST_STATE": "AZ",
        "DEST_ZIP": "85001", "PICKUP_DT": "20260629060000",
        "DELIVERY_DT": "20260629180000", "EQTYPE": "DRY_VAN",
        "RATE": "850", "MAX_BUY": "1000", "WEIGHT": "28000",
        "COMMODITY": "Consumer Electronics", "PIECES": "22", "MILES": "372",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "Liftgate required", "STATUS": "OPEN",
    },
    "LD00003": {
        "LOAD_ID": "LD00003", "ORIG_CITY": "Atlanta", "ORIG_STATE": "GA",
        "ORIG_ZIP": "30303", "DEST_CITY": "Nashville", "DEST_STATE": "TN",
        "DEST_ZIP": "37201", "PICKUP_DT": "20260701100000",
        "DELIVERY_DT": "20260701160000", "EQTYPE": "REEFER",
        "RATE": "620", "MAX_BUY": "750", "WEIGHT": "35000",
        "COMMODITY": "Frozen Poultry", "PIECES": "40", "MILES": "248",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "Temp: -10F", "STATUS": "OPEN",
    },
    "LD00004": {
        "LOAD_ID": "LD00004", "ORIG_CITY": "Chicago", "ORIG_STATE": "IL",
        "ORIG_ZIP": "60601", "DEST_CITY": "Memphis", "DEST_STATE": "TN",
        "DEST_ZIP": "38101", "PICKUP_DT": "20260702070000",
        "DELIVERY_DT": "20260702190000", "EQTYPE": "DRY_VAN",
        "RATE": "720", "MAX_BUY": "860", "WEIGHT": "31000",
        "COMMODITY": "Auto Parts", "PIECES": "15", "MILES": "531",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "", "STATUS": "OPEN",
    },
    "LD00005": {
        "LOAD_ID": "LD00005", "ORIG_CITY": "Dallas", "ORIG_STATE": "TX",
        "ORIG_ZIP": "75201", "DEST_CITY": "Houston", "DEST_STATE": "TX",
        "DEST_ZIP": "77001", "PICKUP_DT": "20260630090000",
        "DELIVERY_DT": "20260630130000", "EQTYPE": "FLATBED",
        "RATE": "380", "MAX_BUY": "450", "WEIGHT": "18000",
        "COMMODITY": "Lumber", "PIECES": "12", "MILES": "239",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "", "STATUS": "OPEN",
    },
    "LD00006": {
        "LOAD_ID": "LD00006", "ORIG_CITY": "Los Angeles", "ORIG_STATE": "CA",
        "ORIG_ZIP": "90001", "DEST_CITY": "Las Vegas", "DEST_STATE": "NV",
        "DEST_ZIP": "89101", "PICKUP_DT": "20260701110000",
        "DELIVERY_DT": "20260701170000", "EQTYPE": "REEFER",
        "RATE": "410", "MAX_BUY": "490", "WEIGHT": "22000",
        "COMMODITY": "Fresh Produce", "PIECES": "30", "MILES": "270",
        "DIMS": "48ft x 8ft x 9ft", "NOTES": "Temp: 34F", "STATUS": "OPEN",
    },
}

_booked: set[str] = set()
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

QUERY_FIELDS = [
    "LOAD_ID", "ORIG_CITY", "ORIG_STATE", "ORIG_ZIP",
    "DEST_CITY", "DEST_STATE", "DEST_ZIP",
    "PICKUP_DT", "EQTYPE", "RATE", "MILES", "STATUS",
]


def _parse_request(line: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for segment in line.decode("ascii", errors="replace").split("|"):
        if ":" in segment:
            key, _, val = segment.partition(":")
            fields[key.strip()] = val.strip()
    return fields


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class TMSHandler(socketserver.BaseRequestHandler):

    def _send(self, line: str) -> None:
        self.request.sendall((line + "\r\n").encode("ascii"))

    def _err(self, code: str, msg: str) -> None:
        self._send(f"ERR|CODE:{code}|MSG:{msg}")

    def _end(self) -> None:
        self._send("END")

    # ------------------------------------------------------------------

    def handle(self) -> None:
        raw = b""
        try:
            while b"\r\n" not in raw:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                raw += chunk
        except OSError:
            return

        line = raw.split(b"\r\n")[0]
        fields = _parse_request(line)

        cmd   = fields.get("CMD",  "")
        token = fields.get("AUTH", "")

        log.info("← %s (fields: %s)", cmd, list(fields.keys()))

        srv: "TMSServer" = self.server  # type: ignore[assignment]

        if token != srv.auth_token:
            self._err("AUTH_FAILED", "Invalid or missing auth token")
            return

        # Chaos: random hang before responding (simulates timeout)
        if srv.chaos and cmd != "DEBUG_ECHO":
            if random.random() < srv.chaos_rate * 0.4:
                log.warning("CHAOS: hanging connection for %s", cmd)
                time.sleep(35)
                return

        dispatch = {
            "DEBUG_ECHO":  self._echo,
            "LOAD_QUERY":  self._query,
            "LOAD_GET":    self._get,
            "LOAD_BOOK":   self._book,
        }

        handler = dispatch.get(cmd)
        if handler is None:
            self._err("UNKNOWN_CMD", f"Unknown command: {cmd}")
            return

        handler(fields)

    # ------------------------------------------------------------------

    def _echo(self, fields: dict) -> None:
        msg = fields.get("MSG", "")
        count = len(fields)
        self._send(f"ECHO|AUTH:OK|FIELDS_PARSED:{count}|MSG:{msg}")
        self._end()

    def _query(self, fields: dict) -> None:
        srv: "TMSServer" = self.server  # type: ignore[assignment]
        orig_city  = fields.get("ORIG_CITY",  "").lower()
        orig_state = fields.get("ORIG_STATE", "").upper()
        dest_city  = fields.get("DEST_CITY",  "").lower()
        dest_state = fields.get("DEST_STATE", "").upper()
        eqtype     = fields.get("EQTYPE",     "").upper()

        if not any([orig_city, orig_state, dest_city, dest_state, eqtype]):
            self._err("MISSING_FIELD", "At least one filter required")
            return

        results = list(_LOADS.values())
        if orig_city:  results = [l for l in results if orig_city  in l["ORIG_CITY"].lower()]
        if orig_state: results = [l for l in results if orig_state == l["ORIG_STATE"].upper()]
        if dest_city:  results = [l for l in results if dest_city  in l["DEST_CITY"].lower()]
        if dest_state: results = [l for l in results if dest_state == l["DEST_STATE"].upper()]
        if eqtype:     results = [l for l in results if eqtype     == l["EQTYPE"].upper()]

        for load in results:
            if srv.chaos and random.random() < srv.chaos_rate * 0.3:
                log.warning("CHAOS: injecting malformed line for %s", load["LOAD_ID"])
                self._send("CORRUPTED!!@#$%^&*()")
                continue
            self._send(format_record(load, QUERY_FIELDS))

        self._end()

    def _get(self, fields: dict) -> None:
        srv: "TMSServer" = self.server  # type: ignore[assignment]
        load_id = fields.get("LOAD_ID", "").strip()
        if not load_id:
            self._err("MISSING_FIELD", "LOAD_ID required")
            return

        load = _LOADS.get(load_id)
        if not load:
            self._err("NOT_FOUND", "Load not found")
            return

        if srv.chaos and random.random() < srv.chaos_rate * 0.2:
            log.warning("CHAOS: malformed LOAD_GET response for %s", load_id)
            self._send("MALFORMED_DETAIL_LINE!!!")
            self._end()
            return

        self._send(format_record(load, DETAIL_FIELD_ORDER))
        self._end()

    def _book(self, fields: dict) -> None:
        load_id     = fields.get("LOAD_ID",     "").strip()
        mc_num      = fields.get("MC_NUM",      "").strip()
        agreed_rate = fields.get("AGREED_RATE", "").strip()

        if not all([load_id, mc_num, agreed_rate]):
            missing = [f for f, v in [("LOAD_ID", load_id), ("MC_NUM", mc_num), ("AGREED_RATE", agreed_rate)] if not v]
            self._err("MISSING_FIELD", f"{', '.join(missing)} required")
            return

        load = _LOADS.get(load_id)
        if not load:
            self._err("NOT_FOUND", "Load not found")
            return

        try:
            rate = float(agreed_rate)
            max_buy = float(load["MAX_BUY"])
        except ValueError:
            self._err("INVALID_RATE", "AGREED_RATE must be numeric")
            return

        if rate > max_buy:
            self._err("INVALID_RATE", f"Rate {rate} exceeds MAX_BUY {max_buy}")
            return

        with _lock:
            if load_id in _booked:
                self._err("ALREADY_BOOKED", f"Load {load_id} is already booked")
                return
            _booked.add(load_id)

        conf = f"BK{random.randint(100000, 999999)}"
        self._send(f"LOAD_ID:{load_id}|STATUS:BOOKED|CONF_NUM:{conf}|AGREED_RATE:{agreed_rate}")
        self._end()


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class TMSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, auth_token: str,
                 chaos: bool = False, chaos_rate: float = 0.3) -> None:
        super().__init__((host, port), TMSHandler)
        self.auth_token  = auth_token
        self.chaos       = chaos
        self.chaos_rate  = chaos_rate


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Mock TMS server")
    parser.add_argument("--host",       default="0.0.0.0")
    parser.add_argument("--port",       type=int, default=9000)
    parser.add_argument("--token",      default="test-token")
    parser.add_argument("--chaos",      action="store_true",
                        help="Inject random faults (timeouts + malformed lines)")
    parser.add_argument("--chaos-rate", type=float, default=0.3, dest="chaos_rate",
                        help="Base probability of a fault per request (default 0.3)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [mock_tms] %(levelname)s %(message)s",
    )

    server = TMSServer(args.host, args.port, args.token, args.chaos, args.chaos_rate)

    log.info("Mock TMS listening on %s:%d  token=%s  chaos=%s",
             args.host, args.port, args.token, args.chaos)
    log.info("Loads: %s", list(_LOADS.keys()))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
