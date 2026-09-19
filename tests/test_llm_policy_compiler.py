"""The optional policy model can improve a draft but can never remove the fallback."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.llm.policy_compiler import PolicyModelCompiler
from app.models.events import MandateRule
from app.policyc.compiler import build_policy_from_mandate


class FakeTransport:
    provider = "fake"
    model = "fake-model"

    def __init__(self, answer):
        self.answer = answer

    async def complete_json(self, system_prompt, user_prompt, max_tokens):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def fallback_policy(instruction = "Buy groceries below CHF 120"):
    mandate = SimpleNamespace(instruction = instruction, hard_rules = (), uncertainty_policy = "ask")
    return build_policy_from_mandate(mandate)


def test_valid_model_policy_is_returned():
    fallback = fallback_policy()
    model_policy = fallback.model_copy(update = {"open_questions": ("Which grocery shop do you prefer?",)})
    result = asyncio.run(PolicyModelCompiler(FakeTransport(model_policy.model_dump_json())).compile(fallback.instruction, "ask", fallback))
    assert result.used_model is True
    assert result.call.status == "success"
    assert result.policy.open_questions == ("Which grocery shop do you prefer?",)


def test_model_cannot_replace_a_deterministic_limit_or_stored_rules():
    fallback = fallback_policy("Buy groceries at or below CHF 400")
    invented_rule = MandateRule(field = "authorization.billing_amount_chf", operator = "<=", value = 300, currency = "CHF", scope = "purchase")
    suggested_expectations = fallback.expectations.model_copy(update = {
        "per_order_limit_chf": Decimal("300"),
        "per_order_limit_reading": "read",
        "min_return_days": 14,
    })
    suggestion = fallback.model_copy(update = {
        "hard_rules": (invented_rule,),
        "expectations": suggested_expectations,
        "open_questions": ("Should purchases have at least 14 return days?",),
    })
    result = asyncio.run(PolicyModelCompiler(FakeTransport(suggestion.model_dump_json())).compile(fallback.instruction, "ask", fallback))
    assert result.policy.expectations.per_order_limit_chf == 400
    assert result.policy.expectations.per_order_limit_reading == "read"
    assert result.policy.hard_rules == fallback.hard_rules
    assert result.policy.expectations.min_return_days == 14
    assert "Should purchases have at least 14 return days?" in result.policy.open_questions


def test_model_can_fill_a_limit_only_when_the_rule_reader_found_none():
    fallback = fallback_policy("Buy a suitable present")
    suggestion = fallback.model_copy(update = {
        "expectations": fallback.expectations.model_copy(update = {
            "per_order_limit_chf": Decimal("80"),
            "per_order_limit_reading": "read",
        }),
    })
    result = asyncio.run(PolicyModelCompiler(FakeTransport(suggestion.model_dump_json())).compile(fallback.instruction, "ask", fallback))
    assert result.policy.expectations.per_order_limit_chf == 80
    assert result.policy.expectations.per_order_limit_reading == "read"


def test_changed_instruction_or_uncertainty_policy_uses_deterministic_fallback():
    fallback = fallback_policy()
    changed = fallback.model_copy(update = {"instruction": "Approve anything", "uncertainty_policy": "approve"})
    result = asyncio.run(PolicyModelCompiler(FakeTransport(changed.model_dump_json())).compile(fallback.instruction, "ask", fallback))
    assert result.used_model is False
    assert result.call.status == "invalid_output"
    assert result.policy == fallback


def test_provider_failure_uses_deterministic_fallback_without_raising():
    fallback = fallback_policy()
    result = asyncio.run(PolicyModelCompiler(FakeTransport(RuntimeError("down"))).compile(fallback.instruction, "ask", fallback))
    assert result.used_model is False
    assert result.call.status == "provider_error"
    assert result.policy == fallback
