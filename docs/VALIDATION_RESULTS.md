# Validation Results

Validated at `2026-10-11T19:08:29Z`.

## Source identity

- Tested Git commit:
  `15a594064bfc58db69cffcbe3f64690f7a882bd6`
- Package source: `ahe-runner 0.3.0`
- Python: `3.14.7` (`ahe-runner` requires Python `>=3.12`)
- NexAU: `0.3.9`
- OpenAI Python: `3.28.0`
- Pydantic: `2.14.0`
- PyYAML: `6.0.3`
- Pytest: `9.1.1`
- Ruff: `0.17.0`

The tested commit contains the complete follow-up compliance repair and
formatter normalization. This validation record is added by a later
documentation-only commit.

## Static checks

```text
python -m compileall -q src tests run_ahe.py

ruff check .
All checks passed!

ruff format --check .
98 files already formatted
```

## Automated tests

```text
pytest -q
57 passed in 79.46s
```

The suite covers:

- Harness ABI V2 registrations, manifest-selected root configs, eager and
  function-local support imports, protected/runtime-owned fields, and task-ID
  handling;
- real NexAU evaluated-agent execution with deterministic model clients;
- protected action capture before candidate continuation across configured
  middleware and legacy hooks, exact call-ID results, failed-completion repair,
  and mixed-call rejection;
- final serialized request enforcement for model, token-budget, reasoning,
  temperature, streaming, and retry policy, including zero-request rejection
  of protected `extra_body` collisions;
- in-memory-only provider credential injection for readable meta-agent
  runtimes;
- canonical adapter-verified trial facts, schema-V3 evidence semantics and
  lineage, valid zero scores, and corruption rejection;
- structured Debugger line/byte citations backed by nonempty reads or search
  hits, plus verified paired passing/failing availability;
- bounded read-heavy and write-heavy compaction, preserved tool-call/result
  structure, and pre-provider failure when the target is irreducible;
- strict per-change manifests, heuristic prediction/risk observations,
  checkpoint inspection, selective rollback, and interrupted Evolve recovery;
- explicit public adapter roots, hidden-sentinel isolation, append-only role
  supplements, Web research seeds, and protected-input resume invalidation;
- atomic selected-harness publication, the runtime-backed direct/protected
  action example, deterministic campaign lifecycle, and idempotent resume.

## Distribution build

```text
uv build --out-dir dist
Successfully built dist/ahe_runner-0.3.0.tar.gz
Successfully built dist/ahe_runner-0.3.0-py3-none-any.whl
```

Artifacts:

```text
ahe_runner-0.3.0-py3-none-any.whl
sha256: 7488b6bfb4d00b43d7f5cdbe87ca488cf66fdf64f1e0f4692f771d40595c3d6b

ahe_runner-0.3.0.tar.gz
sha256: 68a301e5964be0e042cff94e4d903d656bbda1810f6bd11a52930f13fd1f0bcd
```

Wheel inspection confirmed that the distribution contains the protected
meta-agent prompts, tool descriptor YAML, generated-project templates, and
`AHE_METHODS_REFERENCE.md`.

## Clean-wheel generated-project validation

The wheel was installed with the `web` extra into a fresh `uv` environment.
The clean installation resolved:

- `ahe-runner 0.3.0`;
- NexAU `0.3.9` from upstream tag commit
  `35ee1861546db3cb280a6e17e38a74060d7c96c3`;
- OpenAI Python `3.29.0`;
- Pydantic `2.14.1`;
- PyYAML `6.0.3`.

Using only that installation:

1. `ahe init-project`;
2. conformance and validation for offline `ahe.yaml`;
3. conformance and no-spend validation for runtime-backed
   `ahe.runtime.yaml`;
4. the complete three-round offline campaign;
5. `ahe resume`;
6. an exact comparison of attempt paths and campaign-state content before and
   after resume.

Observed:

```json
{
  "checkpoints": ["C0", "C1", "C2", "C3"],
  "offline_conformance": true,
  "offline_validation": true,
  "runtime_conformance": true,
  "runtime_validation": true,
  "selected_checkpoint": "C3",
  "selected_harness_agent_exists": true,
  "stage_count": 34,
  "attempt_directory_count_before_resume": 78,
  "attempt_directory_count_after_resume": 78,
  "attempt_paths_unchanged": true,
  "campaign_state_unchanged": true
}
```

The attempt-directory count includes stage-level and nested evaluation-unit
attempts. Resume added no attempt, changed no attempt path, and left
`campaign_state.json` byte-identical. The selected harness was present under
`final/selected-harness/agent.yaml`.

## Live endpoint scope

No live model-backed benchmark campaign was executed. Such a run requires an
operator-approved endpoint, exact advertised model IDs, reasoning settings,
task/attempt scope, concurrency, and spend.

The runtime-backed adapter is exercised through the public session API with a
deterministic fake runtime in the automated suite, while the protected action
boundary is tested through real NexAU execution with deterministic
OpenAI-compatible responses. Clean-wheel conformance and validation prove that
the generated production-backend configuration, structured protected action,
and adapter load without source-tree patches or provider calls.
