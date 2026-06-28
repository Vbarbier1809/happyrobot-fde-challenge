"""
Integration tests for tms_client.py against a live mock TCP server.
A fresh TMSServer is started once per module; each test resets shared state.
"""

import socket
import threading
import pytest

from mock_tms.server import TMSServer, _booked
from app.config import settings
from app.tms_client import (
    TMSAlreadyBookedError,
    TMSAuthError,
    TMSConnectionError,
    TMSNotFoundError,
    TMSServerError,
    TMSTimeoutError,
    exchange,
)

_PORT = 19100  # isolated from the default 9000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def start_mock_server():
    """Start the mock TMS once for the entire module."""
    server = TMSServer("127.0.0.1", _PORT, "test-token")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Point the TMS client at the local mock for every test."""
    monkeypatch.setattr(settings, "tms_host",            "127.0.0.1")
    monkeypatch.setattr(settings, "tms_port",            _PORT)
    monkeypatch.setattr(settings, "tms_auth_token",      "test-token")
    monkeypatch.setattr(settings, "socket_timeout",      5.0)
    monkeypatch.setattr(settings, "max_retries",         1)
    monkeypatch.setattr(settings, "retry_backoff_base",  0.05)
    _booked.clear()


# ---------------------------------------------------------------------------
# DEBUG_ECHO
# ---------------------------------------------------------------------------

class TestDebugEcho:
    def test_round_trip(self):
        records = exchange("DEBUG_ECHO", {"MSG": "hello"})
        assert len(records) == 1
        assert records[0]["AUTH"] == "OK"
        assert records[0]["MSG"] == "hello"


# ---------------------------------------------------------------------------
# LOAD_QUERY
# ---------------------------------------------------------------------------

class TestLoadQuery:
    def test_filter_by_orig_state(self):
        records = exchange("LOAD_QUERY", {"ORIG_STATE": "IL"})
        assert len(records) >= 1
        assert all(r["ORIG_STATE"].strip() == "IL" for r in records)

    def test_filter_by_eqtype(self):
        records = exchange("LOAD_QUERY", {"EQTYPE": "FLATBED"})
        assert len(records) >= 1
        assert all(r["EQTYPE"].strip() == "FLATBED" for r in records)

    def test_combined_filters_are_anded(self):
        records = exchange("LOAD_QUERY", {"ORIG_STATE": "IL", "EQTYPE": "FLATBED"})
        for r in records:
            assert r["ORIG_STATE"].strip() == "IL"
            assert r["EQTYPE"].strip() == "FLATBED"

    def test_no_match_returns_empty_list(self):
        records = exchange("LOAD_QUERY", {"ORIG_STATE": "ZZ"})
        assert records == []

    def test_records_contain_load_id(self):
        records = exchange("LOAD_QUERY", {"ORIG_STATE": "IL"})
        for r in records:
            assert r["LOAD_ID"].strip().startswith("LD")


# ---------------------------------------------------------------------------
# LOAD_GET
# ---------------------------------------------------------------------------

class TestLoadGet:
    def test_returns_full_detail(self):
        records = exchange("LOAD_GET", {"LOAD_ID": "LD00001"})
        assert len(records) == 1
        r = records[0]
        assert r["LOAD_ID"].strip() == "LD00001"
        assert "MAX_BUY" in r
        assert "COMMODITY" in r
        assert "DELIVERY_DT" in r

    def test_not_found_raises(self):
        with pytest.raises(TMSNotFoundError):
            exchange("LOAD_GET", {"LOAD_ID": "DOESNOTEXIST"})


# ---------------------------------------------------------------------------
# LOAD_BOOK
# ---------------------------------------------------------------------------

class TestLoadBook:
    def test_successful_booking(self):
        records = exchange("LOAD_BOOK", {
            "LOAD_ID": "LD00001", "MC_NUM": "MC123456", "AGREED_RATE": "1200.00",
        })
        assert len(records) == 1
        r = records[0]
        assert r["STATUS"].strip() == "BOOKED"
        assert r["CONF_NUM"].startswith("BK")
        assert r["AGREED_RATE"].strip() == "1200.00"

    def test_already_booked_raises(self):
        exchange("LOAD_BOOK", {
            "LOAD_ID": "LD00002", "MC_NUM": "MC123", "AGREED_RATE": "800.00",
        })
        with pytest.raises(TMSAlreadyBookedError):
            exchange("LOAD_BOOK", {
                "LOAD_ID": "LD00002", "MC_NUM": "MC456", "AGREED_RATE": "800.00",
            })

    def test_rate_exceeding_max_buy_raises(self):
        # LD00001 MAX_BUY = 1450
        with pytest.raises(TMSServerError):
            exchange("LOAD_BOOK", {
                "LOAD_ID": "LD00001", "MC_NUM": "MC123456", "AGREED_RATE": "9999.00",
            })

    def test_not_found_raises(self):
        with pytest.raises(TMSNotFoundError):
            exchange("LOAD_BOOK", {
                "LOAD_ID": "NOTEXIST", "MC_NUM": "MC123", "AGREED_RATE": "500.00",
            })


# ---------------------------------------------------------------------------
# Auth failure
# ---------------------------------------------------------------------------

class TestAuthFailure:
    def test_wrong_token_raises_tms_auth_error(self, monkeypatch):
        monkeypatch.setattr(settings, "tms_auth_token", "wrong-token")
        with pytest.raises(TMSAuthError):
            exchange("DEBUG_ECHO", {"MSG": "test"})


# ---------------------------------------------------------------------------
# Connection / timeout errors
# ---------------------------------------------------------------------------

class TestConnectionErrors:
    def test_connection_refused_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "tms_port",    19199)  # nothing listening here
        monkeypatch.setattr(settings, "max_retries", 0)
        with pytest.raises(TMSConnectionError):
            exchange("DEBUG_ECHO", {})

    def test_socket_timeout_raises(self, monkeypatch):
        """Server accepts the connection but never writes back — simulates TMS hang."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 19101))
        srv.listen(5)
        srv.settimeout(3)

        def _accept():
            try:
                conn, _ = srv.accept()
                threading.Event().wait(10)  # hold without responding
                conn.close()
            except OSError:
                pass

        threading.Thread(target=_accept, daemon=True).start()

        monkeypatch.setattr(settings, "tms_port",       19101)
        monkeypatch.setattr(settings, "socket_timeout", 0.3)
        monkeypatch.setattr(settings, "max_retries",    0)

        with pytest.raises(TMSTimeoutError):
            exchange("DEBUG_ECHO", {})

        srv.close()
