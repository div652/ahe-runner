# Validation Results

Validated at `2026-10-11T17:25:44Z`.

## Source identity

- Tested Git commit:
  `646429ba9a8aed2e327db86cc215423fb599ef11`
- Package: `ahe-runner 0.2.0`
- Python: `3.14.7` (`ahe-runner` requires Python `>=3.12`)
- NexAU: `0.3.9`
- OpenAI Python: `3.28.0`
- Pydantic: `2.14.0`
- PyYAML: `6.0.3`
- Pytest: `9.1.1`
- Ruff: `0.17.0`

The tested commit contains the complete compliance repair before this
documentation-only validation record was added.

## Static checks

```text
ruff check .
All checks passed!

ruff format --check .
94 files already formatted
```

## Automated tests

```text
pytest -q
43 passed in 64.02s
```

The suite covers:

- Harness ABI V2 registrations, root-config selection, transitive support
  modules, protected/runtime-owned fields, and short task-ID handling;
- real NexAU evaluated-agent execution with deterministic fake model clients;
- protected action capture before candidate continuation, exact call-ID
  results, failed-completion repair, and mixed-call rejection;
- exact model/reasoning/temperature/output/retry enforcement;
- strict per-change manifests, heuristic prediction/risk observations, and
  unattributed/unanticipated task changes;
- bounded Debugger reads/searches over large traces and mandatory paired
  analysis or explicit evidence limitations;
- checkpoint history/diff inspection and selective rollback preserving an
  unrelated improvement;
- evaluation request identity, exact task/trial coverage, artifact maps,
  independent adapter verification, evidence lineage, stale-metric rejection,
  and sanitized task IDs;
- explicit public adapter roots, hidden-sentinel isolation, append-only role
  supplements, Web research seeds, and protected-input resume invalidation;
- atomic selected-harness publication and interrupted Evolve recovery;
- the bundled runtime-backed direct task and adapter-owned protected action;
- deterministic campaign lifecycle and idempotent resume.

## Distribution build

```text
uv build --out-dir dist
Successfully built dist/ahe_runner-0.2.0.tar.gz
Successfully built dist/ahe_runner-0.2.0-py3-none-any.whl
```

Artifacts:

```text
ahe_runner-0.2.0-py3-none-any.whl
sha256: f3fc7ded03a91ae3c8af409cd3b632d6751973002f0e92158994c13e7ab0762f

ahe_runner-0.2.0.tar.gz
sha256: eb0ce69f1da38cd33ac154f33cf905a823d5615040820c4bea315aab895db858
```

## Clean-wheel generated-project validation

The wheel was installed with the `web` extra into a fresh temporary `uv`
environment. Using only that installation:

1. `ahe init-project`;
2. conformance for offline `ahe.yaml`;
3. conformance for runtime-backed `ahe.runtime.yaml`;
4. the complete three-round offline campaign;
5. `ahe resume`.

Observed:

```json
{
  "checkpoints": ["C0", "C1", "C2", "C3"],
  "offline_conformance": true,
  "runtime_conformance": true,
  "selected_checkpoint": "C3",
  "stage_count": 34,
  "resume_idempotent": true
}
```

The second run added no stage attempts, and the selected harness was present
under `final/selected-harness/agent.yaml`.

## Live endpoint scope

No live model-backed benchmark campaign was executed. Such a run requires an
operator-approved endpoint, exact advertised model IDs, reasoning settings,
task/attempt scope, concurrency, and spend.

The runtime-backed adapter was exercised through the public session API with a
deterministic fake runtime, while the protected action boundary itself was
tested through real NexAU execution with deterministic OpenAI-compatible
responses. Clean-wheel conformance proves that the generated live
configuration and adapter load without runner patches.
