"""Burst load probe for the loopback GH Copilot Server judge path.

Replays saved Harvey judge requests (exact request kwargs from a finished
evaluation's grading logs) all at once, to check that the endpoint copes with
the concurrency a larger ``grading_parallel`` produces. Records only metadata
(status, latency, returned model label, verdict parse), never prompt text.

Exit status is 0 only when every call succeeds, carries the requested model
label, and returns a parseable pass/fail verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

VERDICT_PATTERN = re.compile(r'"verdict"\s*:\s*"(pass|fail)"', re.IGNORECASE)
SENT_KEYS = ("model", "messages", "max_tokens", "temperature", "stream")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _verdict(content: str | None) -> str | None:
    match = VERDICT_PATTERN.search(content or "")
    return match.group(1).lower() if match else None


def _collect(evaluation_dir: Path, per_model: int) -> dict[str, list[dict[str, Any]]]:
    pattern = "units/*/attempt-1/grading/grading-1/judge-*/model_calls/call-*"
    by_model: dict[str, list[dict[str, Any]]] = {}
    for call_dir in sorted(evaluation_dir.glob(pattern)):
        request = json.loads((call_dir / "request.json").read_text("utf-8"))
        response = json.loads((call_dir / "response.json").read_text("utf-8"))
        kwargs = request["kwargs"]
        model = kwargs["model"]
        bucket = by_model.setdefault(model, [])
        if len(bucket) >= per_model:
            continue
        message = response["response"]["choices"][0]["message"]
        baseline = (
            _parse_time(response["completed_at"]) - _parse_time(request["created_at"])
        ).total_seconds()
        bucket.append(
            {
                "source": str(call_dir.relative_to(evaluation_dir)),
                "kwargs": {key: kwargs[key] for key in SENT_KEYS if key in kwargs},
                "prompt_chars": sum(len(m["content"]) for m in kwargs["messages"]),
                "baseline_seconds": baseline,
                "baseline_verdict": _verdict(message.get("content")),
            }
        )
    return by_model


def _call(
    client: OpenAI, item: dict[str, Any], start: threading.Barrier
) -> dict[str, Any]:
    requested = item["kwargs"]["model"]
    record: dict[str, Any] = {
        "source": item["source"],
        "requested_model": requested,
        "prompt_chars": item["prompt_chars"],
        "baseline_seconds": item["baseline_seconds"],
        "baseline_verdict": item["baseline_verdict"],
    }
    start.wait()
    began = time.monotonic()
    try:
        completion = client.chat.completions.create(**item["kwargs"])
    except Exception as error:
        record.update(
            ok=False,
            seconds=time.monotonic() - began,
            error_type=type(error).__name__,
            status_code=getattr(error, "status_code", None),
            error=str(error)[:300],
        )
        return record
    verdict = _verdict(completion.choices[0].message.content)
    record.update(
        ok=completion.model == requested and verdict is not None,
        seconds=time.monotonic() - began,
        returned_model=completion.model,
        verdict=verdict,
        verdict_matches_baseline=verdict == item["baseline_verdict"],
    )
    return record


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 2),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 2),
        "max": round(ordered[-1], 2),
    }


def _summary(records: list[dict[str, Any]], wall: float) -> dict[str, Any]:
    per_model: dict[str, Any] = {}
    for model in sorted({r["requested_model"] for r in records}):
        rows = [r for r in records if r["requested_model"] == model]
        done = [r for r in rows if "returned_model" in r]
        per_model[model] = {
            "calls": len(rows),
            "ok": sum(r["ok"] for r in rows),
            "errors": sum("error_type" in r for r in rows),
            "label_mismatches": sum(r["returned_model"] != model for r in done),
            "unparsed_verdicts": sum(r["verdict"] is None for r in done),
            "verdict_agreement_with_baseline": sum(
                r["verdict_matches_baseline"] for r in done
            ),
            "probe_seconds": _stats([r["seconds"] for r in done]),
            "baseline_seconds_at_original_concurrency": _stats(
                [r["baseline_seconds"] for r in rows]
            ),
        }
    return {
        "calls": len(records),
        "ok": sum(r["ok"] for r in records),
        "wall_seconds": round(wall, 2),
        "per_model": per_model,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--per-model", type=int, default=60)
    parser.add_argument("--base-url", default="http://127.0.0.1:3141/v1")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.base_url.startswith("http://127.0.0.1:"):
        parser.error("--base-url must be the loopback endpoint")
    by_model = _collect(args.evaluation_dir, args.per_model)
    short = {m: len(v) for m, v in by_model.items() if len(v) < args.per_model}
    if len(by_model) != 2 or short:
        parser.error(f"need two judge models with {args.per_model} calls: {short}")

    catalog = httpx.get(f"{args.base_url}/models", timeout=20).json()
    advertised = {entry["id"] for entry in catalog["data"]}
    missing = sorted(set(by_model) - advertised)
    if missing:
        parser.error(f"judge models not advertised by the endpoint: {missing}")

    items = [item for model in sorted(by_model) for item in by_model[model]]
    client = OpenAI(
        base_url=args.base_url,
        api_key="local-placeholder",
        max_retries=0,
        timeout=args.timeout,
        http_client=httpx.Client(
            limits=httpx.Limits(max_connections=len(items)), timeout=args.timeout
        ),
    )
    start = threading.Barrier(len(items))
    started_at = datetime.now(UTC).isoformat()
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        records = list(pool.map(lambda item: _call(client, item, start), items))
    wall = time.monotonic() - began

    report = {
        "started_at": started_at,
        "base_url": args.base_url,
        "evaluation_dir": str(args.evaluation_dir),
        "concurrency": len(items),
        "client_retries": 0,
        "timeout_seconds": args.timeout,
        "summary": _summary(records, wall),
        "calls": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["ok"] == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
