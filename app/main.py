import logging

from fastapi import Depends, FastAPI, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app import protocol
from app.auth import require_auth
from app.models import BookingConfirmation, BookRequest, ErrorResponse, Load, SearchRequest
from app.tms_client import (
    TMSAlreadyBookedError,
    TMSAuthError,
    TMSConnectionError,
    TMSError,
    TMSNotFoundError,
    TMSTimeoutError,
    exchange,
)

log = logging.getLogger(__name__)

app = FastAPI(
    title="TMS Integration Layer",
    version="1.0.0",
    description="REST/JSON façade over the legacy fixed-width TCP TMS.",
)

_AUTH = {"dependencies": [Depends(require_auth)]}

_ERR_RESPONSES = {
    401: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


def _tms_to_http(exc: TMSError) -> JSONResponse:
    """Map typed TMS exceptions to appropriate HTTP responses."""
    if isinstance(exc, TMSNotFoundError):
        return JSONResponse(status_code=404,
                            content={"error": str(exc), "code": "NOT_FOUND"})
    if isinstance(exc, TMSAlreadyBookedError):
        return JSONResponse(status_code=409,
                            content={"error": str(exc), "code": "ALREADY_BOOKED"})
    if isinstance(exc, TMSAuthError):
        return JSONResponse(status_code=502,
                            content={"error": str(exc), "code": "TMS_AUTH_FAILED"})
    if isinstance(exc, TMSTimeoutError):
        return JSONResponse(status_code=504,
                            content={"error": str(exc), "code": "TMS_TIMEOUT"})
    if isinstance(exc, TMSConnectionError):
        return JSONResponse(status_code=504,
                            content={"error": str(exc), "code": "TMS_UNAVAILABLE"})
    return JSONResponse(status_code=502,
                        content={"error": str(exc), "code": "TMS_ERROR"})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /loads/search
# ---------------------------------------------------------------------------

@app.post(
    "/loads/search",
    response_model=list[Load],
    tags=["loads"],
    responses={**_ERR_RESPONSES, 400: {"model": ErrorResponse}},
    **_AUTH,
)
def search_loads(body: SearchRequest) -> list[Load]:
    fields = protocol.search_fields(
        orig_city=body.orig_city,
        orig_state=body.orig_state,
        dest_city=body.dest_city,
        dest_state=body.dest_state,
        eqtype=body.equipment_type,
    )
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "At least one search filter is required", "code": "MISSING_FILTER"},
        )
    try:
        records = exchange(protocol.CMD_QUERY, fields)
    except TMSError as exc:
        return _tms_to_http(exc)

    return [Load(**protocol.parse_load_summary(r)) for r in records]


# ---------------------------------------------------------------------------
# GET /loads/{load_id}
# ---------------------------------------------------------------------------

@app.get(
    "/loads/{load_id}",
    response_model=Load,
    tags=["loads"],
    responses={**_ERR_RESPONSES, 404: {"model": ErrorResponse}},
    **_AUTH,
)
def get_load(
    load_id: str = Path(..., description="TMS load identifier, e.g. LD00314"),
) -> Load:
    try:
        records = exchange(protocol.CMD_GET, protocol.get_fields(load_id))
    except TMSError as exc:
        return _tms_to_http(exc)

    if not records:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Load {load_id} not found", "code": "NOT_FOUND"},
        )

    return Load(**protocol.parse_load_detail(records[0]))


# ---------------------------------------------------------------------------
# POST /loads/{load_id}/book
# ---------------------------------------------------------------------------

@app.post(
    "/loads/{load_id}/book",
    response_model=BookingConfirmation,
    tags=["loads"],
    responses={**_ERR_RESPONSES, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    **_AUTH,
)
def book_load(
    body: BookRequest,
    load_id: str = Path(..., description="TMS load identifier"),
) -> BookingConfirmation:
    try:
        records = exchange(
            protocol.CMD_BOOK,
            protocol.book_fields(load_id, body.mc_num, body.rate),
        )
    except TMSError as exc:
        return _tms_to_http(exc)

    if not records:
        raise HTTPException(
            status_code=502,
            detail={"error": "Empty booking response from TMS", "code": "TMS_ERROR"},
        )

    return BookingConfirmation(**protocol.parse_booking(records[0]))
