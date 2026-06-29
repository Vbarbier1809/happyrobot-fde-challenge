# TMS Integration Layer

REST/JSON façade over a legacy fixed-width TCP TMS, built for the HappyRobot FDE challenge.

```
Carrier (call) → HappyRobot voice agent → [THIS SERVICE] → Legacy TMS (TCP/fixed-width)
```

The legacy TMS speaks a line-oriented, pipe-delimited, fixed-width ASCII protocol over raw TCP.
This service translates it into a clean, authenticated REST API that the HappyRobot voice agent
can call over HTTP.

---

## Architecture

```
app/
  main.py        FastAPI app — 3 REST endpoints
  auth.py        Bearer-token auth dependency
  tms_client.py  TCP socket client (connect · send · recv · retry)
  protocol.py    Fixed-width encoding/parsing — only file that knows the wire format
  models.py      Pydantic request/response models
  config.py      All config via environment variables

mock_tms/
  server.py      Fake TCP TMS server for local dev & tests (--chaos flag available)

tests/
  test_protocol.py   Unit tests — parser/encoder (no network)
  test_client.py     Integration tests — real TCP against mock server
  test_api.py        REST endpoint tests — mocked TMS client
```

### Key design decisions

| Concern | Approach |
|---|---|
| **Robustness** | Exponential-backoff retries; typed exceptions per failure mode; malformed lines are skipped |
| **Error semantics** | TMS errors map to exact HTTP codes (404 / 409 / 502 / 504) with a JSON body |
| **Isolation** | `protocol.py` is the only spec-dependent module; the rest is spec-agnostic |
| **Security** | Bearer token on every endpoint; `max_rate` (`MAX_BUY`) returned to agent layer only, never logged |

---

## Endpoints

All endpoints require `Authorization: Bearer <API_AUTH_TOKEN>`.

### `POST /loads/search`

Search open loads. At least one filter required.

**Request body:**
```json
{
  "orig_city":      "Salem",
  "orig_state":     "OR",
  "dest_city":      "Dallas",
  "dest_state":     "TX",
  "equipment_type": "FLATBED"
}
```

Filters are combined with AND. Returns an array of load summaries.

---

### `GET /loads/{load_id}`

Full detail for one load (includes `max_rate`, `weight`, `commodity_type`, etc.).

```
GET /loads/LD00314
```

---

### `POST /loads/{load_id}/book`

Book a load. Requires the carrier MC number and the agreed rate.

```json
{ "mc_num": "MC123456", "rate": 3800 }
```

Returns a booking confirmation with a confirmation number.

---

### Error responses

All errors return JSON:
```json
{ "error": "human-readable message", "code": "MACHINE_CODE" }
```

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `MISSING_FILTER` | No search filter provided |
| 401 | `AUTH_FAILED` | Wrong or missing API token |
| 404 | `NOT_FOUND` | Load does not exist |
| 409 | `ALREADY_BOOKED` | Load already reserved |
| 422 | — | Invalid request body (Pydantic) |
| 504 | `TMS_TIMEOUT` | TMS did not respond in time |
| 504 | `TMS_UNAVAILABLE` | Cannot connect to TMS |
| 502 | `TMS_ERROR` | Unexpected TMS error |

---

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `TMS_HOST` | TMS server hostname | `localhost` |
| `TMS_PORT` | TMS TCP port | `9000` |
| `TMS_AUTH_TOKEN` | Credential sent to the TMS on every request | *(empty)* |
| `API_AUTH_TOKEN` | Bearer token HappyRobot sends to this service | `changeme` |
| `SOCKET_TIMEOUT` | Per-connection timeout in seconds | `10.0` |
| `MAX_RETRIES` | Retry count before giving up | `3` |
| `RETRY_BACKOFF_BASE` | Base for exponential backoff (seconds) | `0.5` |

---

## Running with Docker (single command)

### Against the mock TMS (local dev)

```bash
docker compose up --build
```

This starts two containers:
- `mock-tms` — fake TCP TMS on port 9000, seeded with sample loads
- `api` — REST API on port 8000, pointed at `mock-tms`

The API waits for the mock TMS to be healthy before starting.

### Against the real TMS

Set your real TMS credentials in `.env`, then override the TMS address at runtime:

```bash
TMS_HOST=tms.example.com TMS_PORT=17159 docker compose up api
```

Or edit `.env` directly and run `docker compose up api` (skips the mock).

---

## Running locally (without Docker)

```bash
pip install -r requirements.txt

# Terminal 1 — mock TMS
python -m mock_tms.server --token test-token

# Terminal 2 — API
TMS_HOST=localhost TMS_PORT=9000 TMS_AUTH_TOKEN=test-token API_AUTH_TOKEN=changeme \
  uvicorn app.main:app --reload
```

### Chaos mode (simulate TMS faults)

```bash
python -m mock_tms.server --token test-token --chaos --chaos-rate 0.4
```

30–40 % of requests will randomly receive either a timeout or a malformed response line,
exercising the retry and error-handling paths.

---

## Running tests

```bash
pytest -v
```

| Suite | What it covers |
|---|---|
| `test_protocol.py` | Fixed-width parser/encoder — pure unit tests, no network |
| `test_client.py` | TCP client against a live mock server in a thread |
| `test_api.py` | REST endpoints with mocked TMS client |

---

## Raw TMS probe

`probe.py` is a diagnostic script that connects directly to the TMS and prints raw bytes.
Useful for verifying connectivity and inspecting the wire format without starting the full service.

```bash
python probe.py
```

Reads `TMS_HOST`, `TMS_PORT`, and `TMS_AUTH_TOKEN` from `.env`.

---

## Wire protocol (reference)

The TMS uses a line-oriented ASCII protocol over TCP (one request per connection):

**Request:**
```
CMD:LOAD_QUERY|AUTH:<token>|ORIG_STATE:OR\r\n
```

**Success response:**
```
FIELD:VALUE|FIELD:VALUE|...\r\n
END\r\n
```

**Error response:**
```
ERR|CODE:NOT_FOUND|MSG:Load not found\r\n
```

All parsing and encoding is isolated in `app/protocol.py`. Field widths and names were derived
from live transcripts against the real TMS — see inline comments in that file.
