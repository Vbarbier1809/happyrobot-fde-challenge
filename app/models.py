from typing import Optional
from pydantic import BaseModel, Field


class Load(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: Optional[str] = None
    delivery_datetime: Optional[str] = None
    equipment_type: Optional[str] = None
    loadboard_rate: Optional[float] = None
    max_rate: Optional[float] = None        # internal — never disclosed to carrier
    weight: Optional[float] = None
    commodity_type: Optional[str] = None
    num_of_pieces: Optional[int] = None
    miles: Optional[int] = None
    dimensions: Optional[str] = None
    notes: Optional[str] = None


class SearchRequest(BaseModel):
    orig_city: Optional[str]  = Field(None, description="Origin city (e.g. 'Salem')")
    orig_state: Optional[str] = Field(None, description="Origin state code (e.g. 'OR')")
    dest_city: Optional[str]  = Field(None, description="Destination city (e.g. 'Dallas')")
    dest_state: Optional[str] = Field(None, description="Destination state code (e.g. 'TX')")
    equipment_type: Optional[str] = Field(None, description="FLATBED | DRY_VAN | POWER_ONLY | REEFER")


class BookRequest(BaseModel):
    mc_num: str   = Field(..., description="Carrier MC number")
    rate: float   = Field(..., description="Agreed rate (must not exceed max_rate)")


class BookingConfirmation(BaseModel):
    load_id: str
    status: str
    confirmation_number: str
    agreed_rate: Optional[float] = None


class ErrorResponse(BaseModel):
    error: str
    code: str
