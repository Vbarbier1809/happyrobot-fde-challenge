"""
Unit tests for protocol.py — no network, no fixtures.
All raw field values use real wire data from live TMS transcripts.
"""

import pytest
from app.protocol import (
    _parse_dt,
    _float_or_none,
    _int_or_none,
    search_fields,
    get_fields,
    book_fields,
    parse_load_summary,
    parse_load_detail,
    parse_booking,
    format_record,
)

# ---------------------------------------------------------------------------
# Real wire data (from live LOAD_GET transcript, line length 524)
# ---------------------------------------------------------------------------

_QUERY_FIELDS = {
    "LOAD_ID":    "LD00314     ",
    "ORIG_CITY":  "Salem                         ",
    "ORIG_STATE": "OR",
    "ORIG_ZIP":   "97301",
    "DEST_CITY":  "Dallas                        ",
    "DEST_STATE": "TX",
    "DEST_ZIP":   "75201",
    "PICKUP_DT":  "20260701144100",
    "EQTYPE":     "POWER_ONLY",
    "RATE":       "4080    ",
    "MILES":      "1632  ",
    "STATUS":     "OPEN    ",
}

_DETAIL_FIELDS = {
    **_QUERY_FIELDS,
    "DELIVERY_DT": "20260702224100",
    "WEIGHT":      "11422   ",
    "COMMODITY":   "Steel Coils                   ",
    "PIECES":      "8     ",
    "DIMS":        "40ft x 8ft x 9ft              ",
    "NOTES":       "                              ",
    "MAX_BUY":     "4780    ",
}


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_valid(self):
        assert _parse_dt("20260701144100") == "2026-07-01T14:41:00"

    def test_blank_string(self):
        assert _parse_dt("") is None

    def test_whitespace_only(self):
        assert _parse_dt("   ") is None

    def test_non_digits(self):
        assert _parse_dt("NOTADATE12345") is None

    def test_invalid_calendar_date(self):
        assert _parse_dt("20261399000000") is None  # month 13

    def test_midnight(self):
        assert _parse_dt("20260101000000") == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

class TestNumericHelpers:
    def test_float_valid(self):
        assert _float_or_none("4080    ") == 4080.0

    def test_float_blank(self):
        assert _float_or_none("   ") is None

    def test_float_invalid(self):
        assert _float_or_none("NOTNUM") is None

    def test_int_valid(self):
        assert _int_or_none("1632  ") == 1632

    def test_int_from_float_string(self):
        assert _int_or_none("8.0") == 8

    def test_int_blank(self):
        assert _int_or_none("") is None


# ---------------------------------------------------------------------------
# Request field builders
# ---------------------------------------------------------------------------

class TestSearchFields:
    def test_single_state_filter(self):
        assert search_fields(orig_state="IL") == {"ORIG_STATE": "IL"}

    def test_states_are_uppercased(self):
        f = search_fields(orig_state="il", dest_state="tx")
        assert f["ORIG_STATE"] == "IL"
        assert f["DEST_STATE"] == "TX"

    def test_eqtype_is_uppercased(self):
        assert search_fields(eqtype="flatbed") == {"EQTYPE": "FLATBED"}

    def test_city_case_is_preserved(self):
        f = search_fields(orig_city="Salem")
        assert f["ORIG_CITY"] == "Salem"

    def test_none_values_omitted(self):
        f = search_fields(orig_state="IL", dest_city=None, eqtype=None)
        assert "DEST_CITY" not in f
        assert "EQTYPE" not in f

    def test_all_none_returns_empty(self):
        assert search_fields() == {}

    def test_all_filters(self):
        f = search_fields("Chicago", "IL", "Dallas", "TX", "FLATBED")
        assert f == {
            "ORIG_CITY": "Chicago", "ORIG_STATE": "IL",
            "DEST_CITY": "Dallas",  "DEST_STATE": "TX",
            "EQTYPE": "FLATBED",
        }


class TestGetFields:
    def test_basic(self):
        assert get_fields("LD00314") == {"LOAD_ID": "LD00314"}


class TestBookFields:
    def test_basic(self):
        f = book_fields("LD00314", "MC123456", 1200.0)
        assert f == {"LOAD_ID": "LD00314", "MC_NUM": "MC123456", "AGREED_RATE": "1200.00"}

    def test_rate_formatted_to_two_decimals(self):
        f = book_fields("LD00001", "MC999", 850.5)
        assert f["AGREED_RATE"] == "850.50"


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

class TestParseLoadSummary:
    def test_load_id_stripped(self):
        result = parse_load_summary(_QUERY_FIELDS)
        assert result["load_id"] == "LD00314"

    def test_origin_combines_city_and_state(self):
        result = parse_load_summary(_QUERY_FIELDS)
        assert result["origin"] == "Salem, OR"

    def test_destination_combines_city_and_state(self):
        result = parse_load_summary(_QUERY_FIELDS)
        assert result["destination"] == "Dallas, TX"

    def test_pickup_datetime_parsed(self):
        result = parse_load_summary(_QUERY_FIELDS)
        assert result["pickup_datetime"] == "2026-07-01T14:41:00"

    def test_rate_and_miles(self):
        result = parse_load_summary(_QUERY_FIELDS)
        assert result["loadboard_rate"] == 4080.0
        assert result["miles"] == 1632

    def test_blank_state_still_returns_city(self):
        raw = {**_QUERY_FIELDS, "ORIG_STATE": "  ", "DEST_STATE": "  "}
        result = parse_load_summary(raw)
        assert result["origin"] == "Salem"
        assert result["destination"] == "Dallas"

    def test_blank_rate_is_none(self):
        result = parse_load_summary({**_QUERY_FIELDS, "RATE": "   "})
        assert result["loadboard_rate"] is None


class TestParseLoadDetail:
    def test_all_fields_parsed(self):
        result = parse_load_detail(_DETAIL_FIELDS)
        assert result["load_id"] == "LD00314"
        assert result["delivery_datetime"] == "2026-07-02T22:41:00"
        assert result["max_rate"] == 4780.0
        assert result["weight"] == 11422.0
        assert result["commodity_type"] == "Steel Coils"
        assert result["num_of_pieces"] == 8
        assert result["dimensions"] == "40ft x 8ft x 9ft"

    def test_blank_notes_becomes_none(self):
        result = parse_load_detail(_DETAIL_FIELDS)
        assert result["notes"] is None

    def test_blank_commodity_becomes_none(self):
        result = parse_load_detail({**_DETAIL_FIELDS, "COMMODITY": "   "})
        assert result["commodity_type"] is None

    def test_max_rate_maps_to_max_buy_field(self):
        result = parse_load_detail({**_DETAIL_FIELDS, "MAX_BUY": "5000    "})
        assert result["max_rate"] == 5000.0


class TestParseBooking:
    def test_all_fields(self):
        result = parse_booking({
            "LOAD_ID":      "LD00001     ",
            "STATUS":       "BOOKED  ",
            "CONF_NUM":     "BK999888",
            "AGREED_RATE":  "1200.00 ",
        })
        assert result["load_id"] == "LD00001"
        assert result["status"] == "BOOKED"
        assert result["confirmation_number"] == "BK999888"
        assert result["agreed_rate"] == 1200.0

    def test_missing_conf_num(self):
        result = parse_booking({"LOAD_ID": "LD00001", "STATUS": "BOOKED"})
        assert result["confirmation_number"] == ""


# ---------------------------------------------------------------------------
# Mock-server formatter
# ---------------------------------------------------------------------------

class TestFormatRecord:
    def test_load_id_padded_to_12(self):
        result = format_record({"LOAD_ID": "LD00001"}, ["LOAD_ID"])
        # "LD00001" is 7 chars, padded to 12
        assert result == "LOAD_ID:LD00001     "

    def test_state_not_padded_beyond_2(self):
        result = format_record({"ORIG_STATE": "IL"}, ["ORIG_STATE"])
        assert result == "ORIG_STATE:IL"

    def test_multiple_fields_pipe_separated(self):
        data = {"LOAD_ID": "LD00001", "ORIG_STATE": "IL"}
        result = format_record(data, ["LOAD_ID", "ORIG_STATE"])
        parts = result.split("|")
        assert len(parts) == 2

    def test_value_truncated_to_width(self):
        data = {"ORIG_CITY": "A" * 50}  # wider than field width of 30
        result = format_record(data, ["ORIG_CITY"])
        value = result.split(":")[1]
        assert len(value) == 30

    def test_missing_field_emits_empty_padded(self):
        result = format_record({}, ["LOAD_ID"])
        # empty value padded to 12
        assert result == "LOAD_ID:            "
