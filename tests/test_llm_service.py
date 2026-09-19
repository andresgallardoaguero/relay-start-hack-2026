"""The web service uses the optional policy model without losing deterministic fallback."""

import asyncio

from app.llm.policy_compiler import PolicyModelCompiler
from platform_helpers import build_test_platform, build_test_service, build_test_settings


class FakeTransport:
    provider = "fake"
    model = "fake-model"

    def __init__(self, answer):
        self.answer = answer

    async def complete_json(self, system_prompt, user_prompt, max_tokens):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def build_service(tmp_path):
    settings = build_test_settings()
    platform = build_test_platform({}, settings = {"scenarios": []})
    return build_test_service(platform, tmp_path, settings = settings)


def test_service_reports_off_when_no_model_is_configured(tmp_path):
    service = build_service(tmp_path)
    assert service.describe_status()["llm_mode"] == "off"
    assert service.describe_status()["llm_model"] is None
    service.store.close()
    asyncio.run(service.client.close())


def test_service_uses_model_policy_and_records_the_call(tmp_path):
    service = build_service(tmp_path)
    deterministic = asyncio.run(service.compile_policy("Buy groceries below CHF 120", "ask"))

    # Build a valid model answer from the deterministic compiler's internal representation.
    from app.policyc.compiler import build_policy_from_mandate
    from app.webapi.policy_view import MandateContent
    internal = build_policy_from_mandate(MandateContent("Buy groceries below CHF 120", (), "ask"))
    improved = internal.model_copy(update = {"open_questions": ("Which shop do you prefer?",)})
    service.policy_model_compiler = PolicyModelCompiler(FakeTransport(improved.model_dump_json()))
    result = asyncio.run(service.compile_policy(internal.instruction, "ask"))
    assert result["open_questions"] == ["Which shop do you prefer?"]
    assert service.describe_status()["llm_mode"] == "ready"
    assert service.describe_status()["llm_last_call"]["status"] == "success"
    assert deterministic["instruction"] == internal.instruction
    service.store.close()
    asyncio.run(service.client.close())


def test_service_falls_back_and_reports_degraded_after_repeated_failures(tmp_path):
    service = build_service(tmp_path)
    service.policy_model_compiler = PolicyModelCompiler(FakeTransport(RuntimeError("down")))
    first = asyncio.run(service.compile_policy("Buy groceries below CHF 120", "ask"))
    second = asyncio.run(service.compile_policy("Buy groceries below CHF 120", "ask"))
    assert first["instruction"] == second["instruction"]
    assert service.describe_status()["llm_mode"] == "degraded"
    assert service.describe_status()["llm_last_call"]["status"] == "provider_error"
    service.store.close()
    asyncio.run(service.client.close())
