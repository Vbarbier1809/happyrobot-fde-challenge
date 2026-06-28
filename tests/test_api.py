"""
REST endpoint tests — uses FastAPI TestClient with a mocked exchange().
No real TCP connections are made; the TMS client is patched at the boundary.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.tms_client import (
    TMSAlreadyBookedError,
    TMSConnectionError,
    TMSNotFoundError,
    TMSTimeoutError,
)

client = TestClient(app, raise_server_exceptions=False)
_AUTH = {"Authorization": "Bearer changeme"}

# ---------------------------------------------------------------------------
# Shared mock TMS records
# ---------------------------------------------------------------------------

_QUERY_RECORD = {
    "LOAD_ID":    "LD00001     ",
    "ORIG_CITY":  "Chicago                       ",
    "ORIG_STATE": "IL",
    "ORIG_ZIP":   "60601",
    "DEST_CITY":  "Dallas                        ",
    "DEST_STATE": "TX",
    "DEST_ZIP":   "75201",
    "PICKUP_DT":  "20260701080000",
    "EQTYPE":     "FLATBED   ",
    "RATE":       "1250    ",
    "MILES":      "921   ",
    "STATUS":     "OPEN    ",
}

_DETAIL_RECORD = {
    **_QUERY_RECORD,
    "DELIVERY_DT": "20260702200000",
    "WEIGHT":      "42000   ",
    "COMMODITY":   "Steel Coils                   ",
    "PIECES":      "8     ",
    "DIMS":        "48ft x 8ft x 9ft              ",
    "NOTES":       "                              ",
    "MAX_BUY":     "1450    ",
}

_BOOKING_RECORD = {
    "LOAD_ID":     "LD00001     ",
    "STATUS":      "BOOKED  ",
    "CONF_NUM":    "BK123456",
    "AGREED_RATE": "1200.00 ",
}


@pytest.fixture(autouse=True)
def force_api_token(monkeypatch):
    """Ensure the inbound API token is 'changeme' regardless of .env."""
    monkeypatch.setattr(settings, "api_auth_token", "changeme")


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_no_auth_required():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /loads/search
# ---------------------------------------------------------------------------

class TestSearchLoads:
    def test_success_returns_list(self):
        with patch("app.main.exchange", return_value=[_QUERY_RECORD]):
            r = client.post("/loads/search", json={"orig_state": "IL"}, headers=_AUTH)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["load_id"] == "LD00001"
        assert data[0]["origin"] == "Chicago, IL"
        assert data[0]["destination"] == "Dallas, TX"
        assert data[0]["loadboard_rate"] == 1250.0
        assert data[0]["miles"] == 921

    def test_empty_results_returns_empty_list(self):
        with patch("app.main.exchange", return_value=[]):
            r = client.post("/loads/search", json={"orig_state": "ZZ"}, headers=_AUTH)
        assert r.status_code == 200
        assert r.json() == []

    def test_no_filters_returns_400(self):
        r = client.post("/loads/search", json={}, headers=_AUTH)
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "MISSING_FILTER"

    def test_missing_auth_header_returns_403(self):
        r = client.post("/loads/search", json={"orig_state": "IL"})
        assert r.status_code == 403

    def test_wrong_token_returns_401(self):
        r = client.post(
            "/loads/search",
            json={"orig_state": "IL"},
            headers={"Authorization": "Bearer bad-token"},
        )
        assert r.status_code == 401

    def test_tms_timeout_returns_504(self):
        with patch("app.main.exchange", side_effect=TMSTimeoutError("timed out")):
            r = client.post("/loads/search", json={"orig_state": "IL"}, headers=_AUTH)
        assert r.status_code == 504
        assert r.json()["code"] == "TMS_TIMEOUT"

    def test_tms_unavailable_returns_504(self):
        with patch("app.main.exchange", side_effect=TMSConnectionError("refused")):
            r = client.post("/loads/search", json={"orig_state": "IL"}, headers=_AUTH)
        assert r.status_code == 504
        assert r.json()["code"] == "TMS_UNAVAILABLE"

    def test_multiple_results(self):
        with patch("app.main.exchange", return_value=[_QUERY_RECORD, _QUERY_RECORD]):
            r = client.post("/loads/search", json={"dest_state": "TX"}, headers=_AUTH)
        assert r.status_code == 200
        assert len(r.json()) == 2


# ---------------------------------------------------------------------------
# GET /loads/{load_id}
# ---------------------------------------------------------------------------

class TestGetLoad:
    def test_success_returns_full_detail(self):
        with patch("app.main.exchange", return_value=[_DETAIL_RECORD]):
            r = client.get("/loads/LD00001", headers=_AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["load_id"] == "LD00001"
        assert data["delivery_datetime"] == "2026-07-02T20:00:00"
        assert data["max_rate"] == 1450.0
        assert data["weight"] == 42000.0
        assert data["commodity_type"] == "Steel Coils"
        assert data["num_of_pieces"] == 8
        assert data["dimensions"] == "48ft x 8ft x 9ft"
        assert data["notes"] is None

    def test_not_found_returns_404(self):
        with patch("app.main.exchange", side_effect=TMSNotFoundError("not found")):
            r = client.get("/loads/LD99999", headers=_AUTH)
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "NOT_FOUND"

    def test_missing_auth_returns_403(self):
        r = client.get("/loads/LD00001")
        assert r.status_code == 403

    def test_wrong_token_returns_401(self):
        r = client.get(
            "/loads/LD00001",
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_tms_timeout_returns_504(self):
        with patch("app.main.exchange", side_effect=TMSTimeoutError("timed out")):
            r = client.get("/loads/LD00001", headers=_AUTH)
        assert r.status_code == 504


# ---------------------------------------------------------------------------
# POST /loads/{load_id}/book
# ---------------------------------------------------------------------------

class TestBookLoad:
    def test_success(self):
        with patch("app.main.exchange", return_value=[_BOOKING_RECORD]):
            r = client.post(
                "/loads/LD00001/book",
                json={"mc_num": "MC123456", "rate": 1200},
                headers=_AUTH,
            )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "BOOKED"
        assert data["confirmation_number"] == "BK123456"
        assert data["agreed_rate"] == 1200.0

    def test_already_booked_returns_409(self):
        with patch("app.main.exchange", side_effect=TMSAlreadyBookedError("booked")):
            r = client.post(
                "/loads/LD00001/book",
                json={"mc_num": "MC123456", "rate": 1200},
                headers=_AUTH,
            )
        assert r.status_code == 409
        assert r.json()["code"] == "ALREADY_BOOKED"

    def test_not_found_returns_404(self):
        with patch("app.main.exchange", side_effect=TMSNotFoundError("not found")):
            r = client.post(
                "/loads/LD99999/book",
                json={"mc_num": "MC123456", "rate": 1200},
                headers=_AUTH,
            )
        assert r.status_code == 404

    def test_missing_mc_num_returns_422(self):
        r = client.post(
            "/loads/LD00001/book",
            json={"rate": 1200},
            headers=_AUTH,
        )
        assert r.status_code == 422

    def test_missing_rate_returns_422(self):
        r = client.post(
            "/loads/LD00001/book",
            json={"mc_num": "MC123456"},
            headers=_AUTH,
        )
        assert r.status_code == 422

    def test_missing_auth_returns_403(self):
        r = client.post(
            "/loads/LD00001/book",
            json={"mc_num": "MC123456", "rate": 1200},
        )
        assert r.status_code == 403

    def test_tms_timeout_returns_504(self):
        with patch("app.main.exchange", side_effect=TMSTimeoutError("timed out")):
            r = client.post(
                "/loads/LD00001/book",
                json={"mc_num": "MC123456", "rate": 1200},
                headers=_AUTH,
            )
        assert r.status_code == 504
