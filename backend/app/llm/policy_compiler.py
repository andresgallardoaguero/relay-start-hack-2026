"""Optional model path for policy setup, with the deterministic policy as fallback."""

import asyncio
import time

from pydantic import BaseModel, ConfigDict

from app.llm.breaker import CircuitBreaker
from app.llm.schemas import ModelCallRecord
from app.models.policy import Expectations, InternalPolicy


POLICY_SYSTEM_PROMPT = """Compile a customer's purchase instruction into Relay's policy JSON.
Return one JSON object matching the supplied schema exactly. Keep instruction verbatim.
Rules may constrain purchases but never grant permission beyond the customer's words.
Put ambiguity into open_questions. The customer will review and confirm this draft."""


ALWAYS_DETERMINISTIC_FIELDS = {"overshoot_tolerance_share", "split_order_window_minutes"}
PER_ORDER_FIELDS = {"per_order_limit_chf", "per_order_limit_inclusive", "per_order_limit_reading"}
PERIOD_FIELDS = {"period_limit_chf", "period_days", "period_limit_inclusive", "period_limit_reading"}


def merge_expectations(deterministic, suggested):
    """Fill only defaults; a value found by the rule reader is immutable."""
    merged = {}
    per_order_locked = deterministic.per_order_limit_reading != "not_stated"
    period_locked = deterministic.period_limit_reading != "not_stated"
    merchant_type_locked = bool(deterministic.required_merchant_categories)
    for field_name, field_info in Expectations.model_fields.items():
        deterministic_value = getattr(deterministic, field_name)
        suggested_value = getattr(suggested, field_name)
        locked = (
            field_name in ALWAYS_DETERMINISTIC_FIELDS
            or (field_name in PER_ORDER_FIELDS and per_order_locked)
            or (field_name in PERIOD_FIELDS and period_locked)
            or (field_name == "merchant_category_is_strict" and merchant_type_locked)
        )
        is_unset = deterministic_value is None or deterministic_value == () or deterministic_value == {} or deterministic_value == field_info.default
        merged[field_name] = suggested_value if is_unset and not locked else deterministic_value
    return Expectations(**merged)


def merge_policy_suggestion(deterministic, suggested):
    """Keep authoritative values and rules, adding only missing facts and questions."""
    questions = tuple(dict.fromkeys(deterministic.open_questions + suggested.open_questions))
    return deterministic.model_copy(update = {
        "hard_rules": deterministic.hard_rules,
        "expectations": merge_expectations(deterministic.expectations, suggested.expectations),
        "open_questions": questions,
    })


class PolicyCompilation(BaseModel):
    model_config = ConfigDict(frozen = True, extra = "forbid")
    policy: InternalPolicy
    call: ModelCallRecord
    used_model: bool


class PolicyModelCompiler:
    def __init__(self, transport, breaker = None, timeout_seconds = 8.0, max_tokens = 1200, clock = None):
        self.transport = transport
        self.breaker = breaker or CircuitBreaker()
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self._clock = clock or time.perf_counter

    def _record(self, status, started_at, error_type = None):
        return ModelCallRecord(
            purpose = "policy_compilation",
            status = status,
            provider = self.transport.provider,
            model = self.transport.model,
            elapsed_ms = round((self._clock() - started_at) * 1000, 3),
            error_type = error_type,
        )

    async def compile(self, instruction, uncertainty_policy, deterministic_policy):
        started_at = self._clock()
        if not self.breaker.allow_call():
            return PolicyCompilation(policy = deterministic_policy, call = self._record("circuit_open", started_at), used_model = False)

        schema = InternalPolicy.model_json_schema()
        user_prompt = (
            "Instruction:\n" + instruction
            + "\n\nDeterministic policy (existing non-empty values are authoritative):\n"
            + deterministic_policy.model_dump_json()
            + "\n\nRequired JSON schema:\n" + str(schema)
        )
        try:
            raw_output = await asyncio.wait_for(
                self.transport.complete_json(POLICY_SYSTEM_PROMPT, user_prompt, self.max_tokens),
                timeout = self.timeout_seconds,
            )
            policy = InternalPolicy.model_validate_json(raw_output)
            if policy.instruction != instruction:
                raise ValueError("The model changed the customer's instruction")
            if policy.uncertainty_policy != uncertainty_policy:
                raise ValueError("The model changed the customer's uncertainty policy")
            policy = merge_policy_suggestion(deterministic_policy, policy)
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return PolicyCompilation(policy = deterministic_policy, call = self._record("timeout", started_at, "TimeoutError"), used_model = False)
        except Exception as error:
            if getattr(error, "status_code", None) == 401:
                self.breaker.force_open()
                status = "provider_error"
            else:
                self.breaker.record_failure()
                status = "invalid_output" if isinstance(error, (ValueError, TypeError)) else "provider_error"
            return PolicyCompilation(policy = deterministic_policy, call = self._record(status, started_at, type(error).__name__), used_model = False)

        self.breaker.record_success()
        return PolicyCompilation(policy = policy, call = self._record("success", started_at), used_model = True)
