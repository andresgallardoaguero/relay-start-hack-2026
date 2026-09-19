"""Measure five bounded fact-extraction calls without touching the Viseca API."""

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
BACKEND_FOLDER = REPOSITORY_FOLDER / "backend"
if str(BACKEND_FOLDER) not in sys.path:
    sys.path.insert(0, str(BACKEND_FOLDER))

from app.config import REPOSITORY_FOLDER, get_settings
from app.llm.client import OpenAICompatibleTransport
from app.llm.extractor import FactExtractor
from app.models.events import read_purchase_message


FIXTURE = REPOSITORY_FOLDER / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_fixtures" / "example_authorization_request.json"


async def run_probe(calls):
    settings = get_settings()
    if settings.llm_base_url == "" or settings.llm_model == "":
        raise RuntimeError("Set LLM_BASE_URL and LLM_MODEL before running the latency probe")
    event = read_purchase_message(FIXTURE.read_text(encoding = "utf-8"))
    provider = "swisscom-apertus" if "swisscom" in settings.llm_base_url.casefold() else "openai-compatible"
    transport = OpenAICompatibleTransport(
        settings.llm_base_url,
        settings.llm_model,
        provider = provider,
        initial_api_key = settings.llm_api_key,
    )
    latencies = []
    statuses = []
    for _ in range(calls):
        result = await FactExtractor(transport, timeout_seconds = 2.0).extract(event, ["product_kind", "size", "return_days", "final_sale"])
        latencies.append(result.call.elapsed_ms)
        statuses.append(result.call.status)
    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered) + 0.5) - 1))
    report = {
        "provider": transport.provider,
        "model": transport.model,
        "calls": calls,
        "statuses": statuses,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": ordered[p95_index],
        "recommendation": "decision_path" if all(status == "success" for status in statuses) and ordered[p95_index] <= 2000 else "policy_only",
    }
    print(json.dumps(report, indent = 2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type = int, default = 5, choices = range(1, 21))
    arguments = parser.parse_args()
    asyncio.run(run_probe(arguments.calls))


if __name__ == "__main__":
    main()
