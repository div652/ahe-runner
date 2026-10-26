# Validation Results

## AHE-026 and AHE-027 recovery and campaign continuation

Validated at `2026-10-26T05:54:21Z` from source commit
`915bea3d351105c87d3992ab5e07b801c21ac713`:

```text
Focused stage-store and campaign contracts:  24 passed in 124.35s
Complete suite:                              152 passed in 206.83s
ruff check .:                                all checks passed
ruff format --check .:                       108 files already formatted
python -m compileall -q src tests:            exit 0
git diff --check:                             exit 0
uv build:                                     exit 0
```

Validation environment:

```text
Python:            3.14.7
ahe-runner:        0.7.0
NexAU:             0.3.9 (commit 35ee1861546db3cb280a6e17e38a74060d7c96c3)
ag-ui-protocol:    0.1.22
OpenAI Python:     3.29.0 (source venv); 3.37.0 (isolated wheel venv)
Pydantic:          2.14.1
PyYAML:            6.0.3
Pytest:            9.1.1
Ruff:              0.17.0
```

Clean release artifacts:

```text
ahe_runner-0.7.0-py3-none-any.whl
sha256: 4845d4abb18085a31d48215a53e16ac1412a974960c18040c298715372bbd1da

ahe_runner-0.7.0.tar.gz
sha256: 9fd23a6399371d5c5849ba3b87785e4c75b19a501ac57c0af7061f09157d8584
```

Dependency availability: the declared NexAU source,
`https://github.com/nex-agi/NexAU.git`, returned "Repository not found" on
2026-10-26, and NexAU is not on PyPI. The isolated wheel was therefore
installed with the `web` extra and a uv override pointing `nexau` at the
locally cached checkout of the pinned commit `35ee186`. A fresh machine
without that cache cannot currently install the runner as documented.

Using only the isolated wheel installation, run outside the source checkout:

- wheel metadata and `ahe_runner.__version__` both reported `0.7.0`, and the
  package resolved from the isolated environment's `site-packages`;
- offline `ahe conformance` returned `valid: true` with no warnings, and the
  runtime-backed configuration also conformed;
- the generated three-round offline campaign completed and selected `C3`;
- after `campaign.rounds` was raised to 5, `ahe extend-rounds
  --reopen-closed-evolution --apply` archived the closure to
  `superseded/closure-01`;
- `ahe resume` built C4 and C5, re-ran closure, validation, selection and
  final test, and finished with 50 stages;
- a second `ahe resume` added no attempt to any stage.

No live model endpoint was contacted. NexAU emitted its expected optional
`E2B SDK not installed` import message.

## AHE-025 trial isolation and tool timing

Validated at `2026-10-15T19:46:23Z` from source commit
`aa420cd9ea020ee6bfc4335ff817f13f2545e357`. The implementation commit was
`1bc1861`, followed by the deterministic process-slot test synchronization in
`aa420cd`:

```text
Focused process/runtime contracts:           59 passed in 88.34s
Complete suite:                              142 passed in 156.90s
ruff check .:                                all checks passed
ruff format --check .:                       108 files already formatted
python -m compileall -q src tests:            exit 0
git diff --check:                             exit 0
uv build:                                     exit 0
```

Validation environment:

```text
Python:            3.14.7
ahe-runner:        0.6.0
NexAU:             0.3.9
ag-ui-protocol:    0.1.22
OpenAI Python:     3.29.0
Pydantic:          2.14.1
PyYAML:            6.0.3
Pytest:            9.1.1
Ruff:              0.17.0
```

Clean release artifacts:

```text
ahe_runner-0.6.0-py3-none-any.whl
sha256: 05286f7214bfaa30675521307fb0d65a8bf2eeaea3d595f4c49c6aa5ffed0478

ahe_runner-0.6.0.tar.gz
sha256: 90dfd86828a53a9313de11ba554c5a29544d4ebc43d696bbde7b4495ae57c604
```

The wheel was installed with the `web` extra into an isolated Python 3.14
environment outside the source checkout. Using only that installation:

- wheel metadata and `ahe_runner.__version__` both reported `0.6.0`;
- the imported package resolved from the isolated environment's
  `site-packages`;
- the public spawned-trial helper rebuilt an evaluated runtime in a fresh
  process and reused a parent-created model-preflight artifact;
- the child runtime manifest recorded `execution_process.mode:
  spawned_child`, its own PID, and the parent runtime PID;
- `RuntimeProcessSpec.model_dump()` excluded the resolved API key;
- the child created the protected empty `tool_timings.json` artifact and its
  runtime manifest declared the timing schema and path;
- the focused regression proved that parent and child model calls share the
  configured endpoint request slot;
- generated retry/context-accounting defaults and empty long-term memory
  remained intact;
- offline `ahe conformance` returned `valid: true` with no warnings;
- offline `ahe validate` passed and retained the expected warning that
  host-imported candidate middleware is not isolated by an operating-system
  sandbox.

No live model endpoint was contacted. NexAU emitted its expected optional
`E2B SDK not installed` import message. The Harvey-local `trial_processes`
setting, document corpus, sandbox policy, and worker count were not imported;
the upstream seam remains adapter-selected and benchmark-independent.

## AHE-021 through AHE-024 transport and context hardening

Validated at `2026-10-15T09:49:43Z` from source commit
`34cc7e7dac2e39baf3bea657cb9da720d2165959`. The implementation commit was
`904b553`, followed by the test-regex lint repair in `34cc7e7`:

```text
Focused transport/context contracts:         81 passed in 84.17s
Complete suite:                              136 passed in 129.43s
ruff check .:                                all checks passed
ruff format --check .:                       106 files already formatted
python -m compileall -q src tests:            exit 0
git diff --check:                             exit 0
uv build:                                     exit 0
```

Validation environment:

```text
Python:            3.14.7
ahe-runner:        0.5.0
NexAU:             0.3.9
ag-ui-protocol:    0.1.22
OpenAI Python:     3.29.0
Pydantic:          2.14.1
PyYAML:            6.0.3
Pytest:            9.1.1
Ruff:              0.17.0
```

Clean release artifacts:

```text
ahe_runner-0.5.0-py3-none-any.whl
sha256: d2295706173e394f2febe420a6aee62e37e447acbd5a5896f9d33e498c9761f6

ahe_runner-0.5.0.tar.gz
sha256: 387b9ee6ab974d584dea55d6518bb5bed0e8c587ffb685b2b0beeb1edac0cf6d
```

The wheel was installed with the `web` extra into an isolated Python 3.14
environment outside the source checkout. Using only that installation:

- wheel metadata and `ahe_runner.__version__` both reported `0.5.0`;
- the imported package resolved from the isolated environment's
  `site-packages`;
- `ahe init-project` generated `hung_call_retries: 0`;
- the generated manifest protected `max_context_tokens: 200000` and declared
  the NexAU serialized-character fallback unit, with no false exact-token
  claim;
- generated and packaged `LongTermMEMORY.md` files were both zero bytes;
- the public context-overflow classifier recognized a provider rejection
  wrapped as an HTTP 500-style error;
- the packaged methods reference, Evolve prompt, NexAU evolution skill, and
  project templates were present;
- offline `ahe conformance` returned `valid: true` with no warnings;
- offline `ahe validate` passed and retained the expected warning that
  host-imported candidate middleware is not isolated by an operating-system
  sandbox.

No live model endpoint was contacted. NexAU emitted its expected optional
`E2B SDK not installed` import message. Near-limit prompt retention and
reasoning-effort application therefore remain operator-run transport-fidelity
probes, as documented; this offline validation does not claim that an endpoint
honours either behavior.

## AHE-019 and AHE-020 upstream closure

Validated at `2026-10-14T20:09:55Z` from source commit
`6b71092743f9ddf22cc7c288da9000952bbf2f95`:

```text
Focused memory/schema contracts:             30 passed in 20.27s
Complete suite:                              127 passed in 127.04s
ruff check .:                                all checks passed
ruff format --check .:                       106 files already formatted
python -m compileall -q src tests:            exit 0
git diff --check:                             exit 0
uv build:                                     exit 0
```

Validation environment:

```text
Python:            3.14.7
ahe-runner:        0.4.1
NexAU:             0.3.9
ag-ui-protocol:    0.1.22
OpenAI Python:     3.29.0
Pydantic:          2.14.1
PyYAML:            6.0.3
Pytest:            9.1.1
Ruff:              0.17.0
```

Clean release artifacts:

```text
ahe_runner-0.4.1-py3-none-any.whl
sha256: 253926a298f373fddf7bc9e678d7d05d27cb580a3c182c0c41384c4152ee20cc

ahe_runner-0.4.1.tar.gz
sha256: b5322cd1b18fdefd56c66b833a0586e8f3db95e048b3c184b2dfe0eb8a61319c
```

The wheel was installed with the `web` extra into an isolated Python 3.14
environment outside the source checkout. Using only that installation:

- wheel metadata and `ahe_runner.__version__` both reported `0.4.1`;
- the imported package resolved from the isolated environment's
  `site-packages`;
- packaged and freshly generated `LongTermMEMORY.md` files were both zero
  bytes;
- the packaged Evolve prompt contained the long-term-memory prompt-safety
  instructions;
- an omitted `tool_schemas` block resolved to the strict
  `allow_omitted_additional_properties: false` default;
- `ahe init-project`, offline `ahe conformance`, and offline `ahe validate`
  all passed with no structured warnings.

No live model endpoint was contacted. NexAU emitted its expected optional
`E2B SDK not installed` import message; the generated local example and its
NexAU load smoke remained available.

## AHE-001–018 upstream hardening

Validated at `2026-10-14T12:18:33Z` from source commit `5eff569`:

```text
Focused hardening contracts:                 117 passed in 124.58s
Complete suite:                              125 passed in 130.69s
ruff check .:                                all checks passed
ruff format --check .:                       106 files already formatted
python -m compileall -q src tests:            exit 0
git diff --check:                             exit 0
uv build:                                     exit 0
```

Validation environment:

```text
Python:            3.14.7
ahe-runner:        0.4.0
NexAU:             0.3.9
ag-ui-protocol:    0.1.22
OpenAI Python:     3.29.0
Pydantic:          2.14.1
```

Clean release artifacts:

```text
ahe_runner-0.4.0-py3-none-any.whl
sha256: 55e136f44f9cb4f4218749de7265f1b8372e3d2a0fd5f17a8835f735b4599c4c

ahe_runner-0.4.0.tar.gz
sha256: 5d578bb9d9756088791529cc2515c3f9ab01a88aa79eb723e4fc91e05e48a92b
```

The wheel was installed into a fresh Python 3.14 environment outside the
source checkout. The smoke check confirmed:

- wheel metadata reports `0.4.0`;
- the packaged `nexau-evolution-guide` contains `SKILL.md` and its license;
- `init-project` emits the skill-asset manifest glob;
- the runtime sample emits the endpoint request limit and metric-warning
  configuration.

No live model endpoint was contacted. NexAU emitted its expected optional
`E2B SDK not installed` warning during import; the runner's local and optional
Docker paths remained available. The reviewed dispositions are in
[`HARVEY_INTEGRATION_UPSTREAM_DISPOSITION.md`](HARVEY_INTEGRATION_UPSTREAM_DISPOSITION.md).

## F05, F06, and version closure

Validated from implementation commit
`fe9c03e` after the release 0.3.0 closure audit:

```text
Focused citation/import/version regressions: 6 passed in 5.68s
Complete suite:                              63 passed in 83.56s
ruff check .:                                all checks passed
ruff format --check .:                       100 files already formatted
python -m compileall -q src tests run_ahe.py: exit 0
```

Clean release artifacts:

```text
ahe_runner-0.3.0-py3-none-any.whl
sha256: 3fa5ea8d924f5f7aa1e39bc127c3aa9f6b87039c11d47a6df1559d6bf2149b1b

ahe_runner-0.3.0.tar.gz
sha256: 6ab493bba29f33993aa96583df974accff7a22f98bc16dc81c35524cb06c7d9b
```

The wheel was installed into a clean Python 3.14.7 environment. A fresh
zero-round scripted campaign confirmed that wheel metadata,
`ahe_runner.__version__`, and `campaign_manifest.json` all report `0.3.0`.
The exact procedure and repaired contracts are recorded in
[`F05_F06_VERSION_REPAIR.md`](F05_F06_VERSION_REPAIR.md). No live model
endpoint was contacted.

## Earlier 0.3.0 validation

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
