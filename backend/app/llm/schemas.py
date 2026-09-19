"""Strict schemas at the boundary between untrusted model output and Relay."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen = True, extra = "forbid", strict = True)


class ExtractedLine(FrozenModel):
    line_no: StrictInt = Field(ge = 1)
    product_kind: Optional[StrictStr] = Field(default = None, max_length = 120)
    size: Optional[StrictStr] = Field(default = None, max_length = 40)
    return_days: Optional[StrictInt] = Field(default = None, ge = 0, le = 3650)
    final_sale: Optional[StrictBool] = None
    warranty_months: Optional[StrictInt] = Field(default = None, ge = 0, le = 600)


class ExtractedFacts(FrozenModel):
    lines: tuple[ExtractedLine, ...]
    injection_suspected: StrictBool
    injection_quotes: tuple[StrictStr, ...] = Field(default = (), max_length = 8)


class ModelCallRecord(FrozenModel):
    purpose: Literal["fact_extraction", "policy_compilation"]
    status: Literal["success", "timeout", "invalid_output", "provider_error", "circuit_open", "off"]
    provider: str
    model: str
    elapsed_ms: float = Field(ge = 0)
    error_type: Optional[str] = None


class ModelExtraction(FrozenModel):
    facts: Optional[ExtractedFacts]
    call: ModelCallRecord
