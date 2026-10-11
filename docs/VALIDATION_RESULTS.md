# Validation Results

Validated at `2026-10-11T14:10:17Z`.

## Source identity

- Tested Git commit: `eb7ec9838468c5f8d9ded553174ddc5b28691099`
- Package: `ahe-runner 0.2.0`
- Python: `3.14.7` (`ahe-runner` requires Python `>=3.12`)
- NexAU: `0.3.9`, pinned upstream commit
  `35ee1861546db3cb280a6e17e38a74060d7c96c3`
- OpenAI Python: `3.28.0`
- Pydantic: `2.14.0`
- PyYAML: `6.0.3`
- Pytest: `9.1.1`
- Ruff: `0.17.0`

The tested commit is the complete V2 implementation before this
documentation-only validation record was added.

## Static checks

```text
ruff check src tests run_ahe.py
All checks passed!

ruff format --check src tests run_ahe.py
57 files already formatted

python -m compileall -q src tests run_ahe.py
exit 0
```

## Automated tests

```text
pytest -q
20 passed in 26.22s
```

The suite covers:

- schema-2 manifest and Harness ABI V2 registration checks;
- rejection of inactive components and tool schema/callable mismatches;
- protected and runtime-owned candidate fields;
- real NexAU `Agent`/`Executor` execution with a deterministic fake model
  client;
- recursive root/sub-agent evaluated-model overrides;
- long-term-memory attachment;
- internal harness-tool execution;
- protected structured-action schema injection without prompt injection;
- exact protected result call-ID matching;
- fail-closed rejection of mixed protected and harness calls;
- state preservation within a session and isolation across sessions;
- local sandbox binding to an explicit per-trial workspace;
- checkpoint-pure imports with no bytecode mutation;
- deterministic campaign lifecycle, Git recovery, and idempotent resume.

## Distribution build

```text
uv build
Successfully built dist/ahe_runner-0.2.0.tar.gz
Successfully built dist/ahe_runner-0.2.0-py3-none-any.whl
```

Artifacts:

```text
ahe_runner-0.2.0-py3-none-any.whl
sha256: fcda2d914152f3f558aac358f7bf892d5c1f3217c3269af95590403bf9619aa9

ahe_runner-0.2.0.tar.gz
sha256: 62b401b038af360faf6262ac1db52c17ef4a63f5a28e42ea7361674448bb5135
```

Distribution inspection verified:

- V2 runtime and conformance modules are packaged;
- the ABI V2 generated project, internal tool, skill, and reviewer sub-agent
  are packaged;
- Python bytecode and `__pycache__` artifacts are absent.

## Clean-wheel generated-project campaign

The wheel was installed with the `web` extra into a fresh `uv` environment.
Using only that installation:

1. `ahe init-project`
2. set `campaign.rounds: 1`
3. `ahe conformance`
4. `ahe run`
5. `ahe resume`

Observed:

- adapter API generation `v2`;
- harness ABI `ahe.harness/v2`;
- conformance valid;
- checkpoints `C0`, `C1`;
- selected checkpoint `C1`;
- 18 completed stages;
- no blocked or interrupted stages;
- `resume` returned an identical campaign status without adding work.

NexAU emitted its informational message that the optional E2B SDK was not
installed. This deterministic campaign used NexAU's local execution path and
did not require E2B.

## Live endpoint scope

No live model-backed benchmark campaign was executed. That requires an
operator-approved endpoint, exact advertised model ID, reasoning effort,
task/attempt scope, and concurrency.

The protected action boundary was validated against real NexAU execution and
OpenAI-compatible response objects with a deterministic fake client. Existing
model-access tests cover exact observable-model checks and negative controls.
