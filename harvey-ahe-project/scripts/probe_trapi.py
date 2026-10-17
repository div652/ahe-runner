"""Send exactly ONE small TRAPI Responses request and classify the outcome.

Exit codes: 0 ok, 3 principal blocked (403 PrincipalBlocked), 4 rate limited
(429), 5 any other error. Prints one JSON line. No retries, ever.

Run from ~/AHE_Harvey_trapi with AHE_AZURE_BASE_URL, AHE_AZURE_TOKEN_SCOPE and
PYTHONPATH=src set.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime

import openai

from ahe_runner.config import TransportSettings
from ahe_runner.meta.model_client import build_openai_client, is_principal_blocked

MODEL = "gpt-5.6-sol_2026-07-09"


def main() -> int:
    transport = TransportSettings(
        provider="azure_entra",
        base_url=os.environ["AHE_AZURE_BASE_URL"],
        token_scope=os.environ["AHE_AZURE_TOKEN_SCOPE"],
        api_mode="responses",
        model_catalog="disabled",
        negative_control="disabled",
        request_timeout_seconds=120,
    )
    client = build_openai_client(transport, "", workload_id="ahe-runner-c13b-probe")
    started = time.monotonic()
    row: dict[str, object] = {"time": datetime.now(UTC).isoformat(), "model": MODEL}
    code = 0
    try:
        raw = client.responses.with_raw_response.create(
            model=MODEL,
            input="Reply with OK only.",
            max_output_tokens=64,
            reasoning={"effort": "low"},
        )
        response = raw.parse()
        row.update(
            outcome="ok",
            status_code=raw.status_code,
            returned_model=response.model,
            text=(response.output_text or "")[:40],
        )
    except openai.APIStatusError as exc:
        code = 3 if is_principal_blocked(exc) else 4 if exc.status_code == 429 else 5
        row.update(
            outcome={3: "principal_blocked", 4: "rate_limited", 5: "error"}[code],
            status_code=exc.status_code,
            retry_after=exc.response.headers.get("retry-after"),
            message=str(exc)[:400],
        )
    except Exception as exc:
        code = 5
        row.update(outcome="error", type=type(exc).__name__, message=str(exc)[:400])
    row["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(row), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
