"""Compare the offline mock interpretation with the configured real provider.

    python -m ghostflow.ai.compare ../docs/examples/demo-recording.json [--out evidence.json]

Always runs the mock. Runs Meta only when GHOSTFLOW_AI_PROVIDER=meta and a key are set, and
then sends exactly one request (retries disabled, request cap 1). Prints a side-by-side view and
can save it as JSON evidence (no input values, no prompts, no key).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from ghostflow.ai import MetaModelProvider, MockModelProvider, ModelProviderError, RecordingContext, annotate_workflow
from ghostflow.ai.annotate import execution_view
from ghostflow.config import Settings
from ghostflow.dsl.models import Workflow
from ghostflow.recording import RecordingCreate, compile_recording, sanitize_recording
from ghostflow.security import UrlPolicy

# Muse Spark 1.3, Standard mode, USD per 1M tokens (https://dev.meta.ai/docs/pricing-rate-limits).
# Reasoning tokens are billed as output. Estimate only; Meta's billing is authoritative.
PRICE_INPUT_PER_M = 1.25
PRICE_OUTPUT_PER_M = 4.25


def summarize(workflow: Workflow) -> dict:
    it = workflow.interpretation
    insights = {s.id: s for s in it.steps} if it else {}
    input_insights = {i.name: i for i in it.inputs} if it else {}
    return {
        "provider": it.provider if it else None,
        "model": it.model if it else None,
        "name": workflow.name,
        "purpose": it.purpose if it else None,
        "description": workflow.description,
        "expected_outcome": it.expected_outcome if it else None,
        "preconditions": it.preconditions if it else [],
        "steps": [
            {
                "id": s.id,
                "description": s.description,
                "explanation": insights[s.id].explanation if s.id in insights else None,
                "relevance": insights[s.id].relevance if s.id in insights else None,
            }
            for s in workflow.steps
        ],
        "inputs": [
            {
                "name": i.name,
                "label": i.label,
                **(input_insights[i.name].model_dump(exclude={"name"}) if i.name in input_insights else {}),
            }
            for i in workflow.inputs
        ],
        "review_notes": it.review_notes if it else [],
    }


def estimate_cost_usd(usage: dict) -> float:
    return round(
        usage.get("prompt_tokens", 0) / 1e6 * PRICE_INPUT_PER_M
        + usage.get("completion_tokens", 0) / 1e6 * PRICE_OUTPUT_PER_M,
        6,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", type=Path, help="Recording JSON (RecordingCreate, e.g. GET /api/recordings/{id} .recording)")
    parser.add_argument("--out", type=Path, help="Write the comparison as JSON evidence")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()
    data = json.loads(args.recording.read_text(encoding="utf-8"))
    recording = sanitize_recording(RecordingCreate.model_validate(data.get("recording", data)), UrlPolicy(settings.allowed_origin_list))
    workflow = compile_recording(recording)
    context = RecordingContext.from_recording(recording)

    result: dict = {"recording": recording.name, "mock": summarize(annotate_workflow(workflow, MockModelProvider(), context))}

    key = settings.meta_api_key.get_secret_value().strip() if settings.meta_api_key else ""
    if settings.ai_provider != "meta" or not key:
        result["meta"] = {"status": "skipped", "reason": "Set GHOSTFLOW_AI_PROVIDER=meta and GHOSTFLOW_META_API_KEY to compare"}
    else:
        meta = MetaModelProvider(
            api_key=settings.meta_api_key,
            base_url=settings.meta_api_base_url,
            model=settings.meta_model,
            timeout_s=settings.meta_timeout_s,
            max_output_tokens=settings.meta_max_output_tokens,
            reasoning_effort=settings.meta_reasoning_effort,
            max_retries=0,
            max_requests=1,
            allow_contributor_mode=settings.meta_allow_contributor_mode,
        )
        try:
            annotated = annotate_workflow(workflow, meta, context)
            result["meta"] = {"status": "ok", **summarize(annotated)}
            result["meta"]["execution_unchanged"] = execution_view(annotated) == execution_view(workflow)
        except ModelProviderError as exc:
            result["meta"] = {"status": "error", "error": str(exc)}
        finally:
            meta.close()
        result["meta"]["requests_sent"] = meta.requests_sent
        result["meta"]["usage"] = meta.usage_totals
        result["meta"]["estimated_cost_usd"] = estimate_cost_usd(meta.usage_totals)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if result["meta"].get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
