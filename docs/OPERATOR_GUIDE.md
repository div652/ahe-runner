# Operator Guide

This is the shortest complete procedure for running AHE on your own NexAU
harness.

## 1. Install the runner

From this repository:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[web]'
```

If the system Python cannot create virtual environments, `uv` is equivalent:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[web]'
```

The `web` extra is required by the production Web Explore Agent.

## 2. Create the operator project

```bash
ahe init-project /path/to/my-ahe-project
```

The project must have this boundary:

```text
my-ahe-project/
├── ahe.yaml
├── harness_manifest.yaml
├── benchmark_adapter.py
├── harness/
│   ├── agent.yaml
│   ├── systemprompt.md
│   ├── LongTermMEMORY.md
│   ├── ShortTermMEMORY.md
│   ├── tool_descriptions/
│   ├── tools/
│   ├── middleware/
│   ├── skills/
│   └── sub_agents/
└── campaign-artifacts/
```

Only the candidate harness belongs under `harness/`. Keep all of these outside
it:

- benchmark and evaluator code;
- split definitions;
- hidden task state;
- expected answers and scoring logic;
- model-server credentials;
- the AHE controller;
- environment or simulator implementations.

This separation is the protected benchmark boundary.

## 3. Put the base NexAU harness in `harness/`

The harness is file-backed. `agent.yaml` must load through the installed
NexAU `AgentConfig`.

The generated harness is an active ABI V2 example with a registered internal
tool, skill, reviewer sub-agent, and long-term-memory attachment. Replace its
files with your base harness while preserving relative references. Any tool,
middleware, skill, prompt, memory, or sub-agent that should evolve must live
inside this directory.

Do not put runtime state or task-specific knowledge in the seed.

## 4. Declare what may evolve

Edit `harness_manifest.yaml`.

V2 projects declare:

```yaml
schema_version: 2
abi_version: ahe.harness/v2

attachments:
  long_term_memory: system_prompt_suffix
```

The seven supported component classes are:

```yaml
components:
  system_prompt:
    - systemprompt.md
  tool_descriptions:
    - tool_descriptions/**/*.tool.yaml
  tool_implementations:
    - tools/**/*.py
  middleware:
    - middleware/**/*.py
  skills:
    - skills/*/**
  sub_agents:
    - sub_agents/**/agent.yaml
  long_term_memory:
    - LongTermMEMORY.md
```

Also declare:

- `required_files`: must exist in every candidate;
- `runtime_owned`: may be used at runtime but cannot change across checkpoints;
- `protected_agent_fields`: exact `agent.yaml` values Evolve cannot alter;
- optional `tool_schemas.allow_omitted_additional_properties: true` only when
  exact parity with an external benchmark schema requires omission;
- `context_accounting`: the protected `max_context_tokens`, optional NexAU
  `token_counter` import, human-readable counting unit, and optional provider
  input limit shared by every evaluated agent and sub-agent config;
- file and total-tree size limits.

Conformance checks that every matched file is actually registered at runtime.
Tool schema properties must match explicit Python callable parameters,
middleware constructors must accept their configured parameters, skills need
substantive frontmatter, every registered skill asset must be declared,
sub-agents recursively load, and the installed NexAU version must match.

Keep a seed `LongTermMEMORY.md` empty unless it already contains a real
reusable lesson. Its nonempty contents are appended to the evaluated system
prompt, so explanatory notes about the file belong in Evolve guidance instead.

For a live benchmark, replace NexAU's fallback character estimate with a
counter calibrated for the evaluated model whenever long prompts are possible:

```yaml
context_accounting:
  max_context_tokens: 911793
  token_counter: my_benchmark.token_count:count_prompt_tokens
  counting_unit: o200k_base tokens
  provider_max_input_tokens: 921793
```

Put the counter implementation in protected adapter/project code, not inside
the evolvable harness, and include its source in `adapter_dependency_paths` so
campaign provenance hashes it. Express `provider_max_input_tokens` in the same
declared counting unit. Apply the same `max_context_tokens` and `token_counter`
to every agent config. Recalibrate both when the evaluated model changes;
token counts from one model family are not portable to another.

The import must resolve to a callable with NexAU's request-counter shape:

```python
# my_benchmark/token_count.py
from collections.abc import Sequence
from typing import Any

from nexau.core.messages import Message

from my_benchmark.provider_counter import count_nexau_request


def count_prompt_tokens(
    messages: Sequence[Message],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    return count_nexau_request(messages=messages, tools=tools)
```

Configure that function as
`my_benchmark.token_count:count_prompt_tokens`. The delegated implementation
must count the exact message/tool representation and model-specific unit
declared in the manifest; this interface example is not a universal tokenizer.
The callable receives NexAU `Message` objects, not legacy message dictionaries.

The exact per-file schemas and Python signatures are in `HARNESS_ABI.md`.

An omitted component pattern is not an invitation to edit protected code.
Only files inside `harness/` are candidate surfaces, and adapter validation may
impose stricter rules.

## 5. Implement the benchmark adapter

`benchmark_adapter.py` exports one `BenchmarkAdapterV2` subclass:

```python
from my_benchmark_adapter import MyAdapter

__all__ = ["MyAdapter"]
```

Configure it as:

```yaml
project:
  adapter: benchmark_adapter:MyAdapter
  adapter_dependency_paths:
    - ./benchmark_adapter.py
    - ./adapter_support
  meta_agent_source_roots:
    - ./benchmark_public
```

The adapter owns native evaluation, split secrecy, metrics, trace conversion,
infrastructure classification, official benchmark actions, and
benchmark-specific candidate checks. The runner supplies the evaluated NexAU
runtime. See `ADAPTER_GUIDE.md`.

For data/repository benchmarks, send tasks, files, and workspace paths through
the session input; a protected tool list may be empty. When a benchmark has an
official structured action API, declare its immutable schemas in
`BenchmarkInterface` and attach them through `AgentSessionRequest`.

Those schemas extend the in-memory NexAU config only. They are sent through the
structured model API `tools` field, not appended to prompt text, written into
`harness/`, or made editable by Evolve.

`adapter_dependency_paths` are provenance-only: they are hashed but not
readable by meta-agents. `meta_agent_source_roots` are the explicit public
documentation/source roots available to Source Explore and Evolve. Keep all
held-out task content and private verifier implementation outside those roots.

## 6. Configure the campaign

Important settings in `ahe.yaml`:

```yaml
campaign:
  name: my-campaign
  artifacts_dir: ./campaign-artifacts/my-campaign
  rounds: 10
  root_seed: 42

models:
  evaluated:
    model: exact-evaluated-model-id
    max_tokens: 16384
    reasoning_effort: provider-default
    temperature: 0.2
  meta:
    model: exact-meta-model-id
    max_tokens: 32000
    reasoning_effort: high
    temperature: 0.2

transport:
  base_url: http://127.0.0.1:3141/v1
  api_key_env: null
  api_mode: chat_completions
  stream: false
  request_timeout_seconds: 300
  observable_model_policy: exact
  model_catalog: required
  negative_control: required
  max_concurrent_requests: 4
  hung_call_retries: 0

meta:
  backend: nexau
  max_concurrent_model_calls: 2
  max_context_tokens: 200000
  token_counter: null
  context_counting_unit: NexAU fallback estimate (serialized characters divided by four)
  provider_max_input_tokens: null
  max_running_subagents: 5
  evolve_max_iterations: 500
  explore_max_iterations: 200
  explore:
    source_enabled: true
    web_enabled: true
    web_search_backend: ddgs
    research_seed_urls:
      - https://example.org/primary-agent-method
  role_supplements:
    source: ./meta_supplements/source.md
    web: ./meta_supplements/web.md
    debugger: ./meta_supplements/debugger.md
    evolve: ./meta_supplements/evolve.md
  deprecated_metric_terms: []
```

Role supplements are appended after the protected core procedures; they cannot
replace tool permissions, evidence boundaries, or deliverable contracts.
They are injected as literal text, so braces or other Jinja syntax in
benchmark documentation are not rendered by NexAU. Add obsolete metric labels
to `deprecated_metric_terms` when changing scoring semantics; local validation
will report matching supplement text.

All four roles receive the same resolved benchmark-family context. Web Explore
also receives the configured seed URLs before broader research.

`transport.max_concurrent_requests` is the runner-wide queue for recorded
model calls to that endpoint, including children created through the public
evaluated-trial process helper. Adapter-owned provider calls participate only
when wrapped with `ahe_runner.model_requests.model_request_slot`; see the
adapter guide.

`evaluation.max_concurrency` is only the maximum parallelism passed to the
adapter. It does not choose threads or processes. When concurrent trials run
CPU-bound harness tools on the host, review
[`ADAPTER_GUIDE.md`](ADAPTER_GUIDE.md#threaded-versus-process-isolated-trials)
before selecting the adapter's worker model.

`transport.hung_call_retries` controls extra attempts for a request that
timed out before any response. It defaults to `0` and is capped at `5`. The
runner resends the identical request only for timeout-class hangs, preserves
each failed attempt in the model-call folder, and reports aggregate resend
counts through `ahe status`. Overload, validation, context-length, and other
errors are not resent.

The `meta` context fields describe the unit used by the meta-agent
`max_context_tokens` check. A custom NexAU `token_counter` should live in
protected project code. If `provider_max_input_tokens` is set, configuration
loading rejects a larger `max_context_tokens` value.

Optional per-role token/temperature overrides and the protected meta-agent
shell are configured under:

```yaml
meta:
  roles:
    debugger: {temperature: 0.2, max_tokens: 32000}
    evolve: {temperature: 0.2, max_tokens: 64000}
    explorers: {temperature: 0.2, max_tokens: 32000}
  shell:
    backend: disabled  # or docker
    image: null        # required when backend is docker
```

The shell is disabled by default. Docker mode validates the configured image
and runs commands in the protected tool sandbox; do not describe host-imported
middleware or the local NexAU sandbox as an OS security boundary.

Model IDs sent to the endpoint are exact and unprefixed. Do not use
`openai/<id>` or `copilot/<id>` in this file.

### GH Copilot Server

Before a live run, explicitly confirm:

1. the exact loopback endpoint instance;
2. the exact model ID currently returned by `GET /v1/models`;
3. reasoning effort, including an intentional provider default;
4. `chat_completions` and non-streaming mode;
5. task/attempt scope and model-call concurrency;
6. start-of-prompt recall near the configured input limit;
7. a comparative probe showing that requested reasoning effort changes
   observable behavior or latency.

Keep the unauthenticated extension on loopback. The runner performs a fresh
positive probe and nonexistent-model negative control for every model-backed
stage attempt, then verifies the observable response label on every call.

The standard API cannot prove that a proxy retained the beginning of a long
prompt or honored a reasoning setting. Run those two behavioral probes for
every distinct model/role transport contract and preserve the inputs, outputs,
server version, and result with the campaign. Do not start a live campaign
when the near-limit sentinel is lost or effort settings are observationally
identical without an endpoint-specific explanation.

That label may be synthesized by a proxy from its selected model object.
Without independent upstream telemetry, transparent upstream substitution
cannot be ruled out. Recorded model-call artifacts include deterministic
request byte and message/tool character counts so a missing-usage transport
can be audited later; those counts are not substitutes for model tokens.

## 7. Validate before spending model calls

```bash
ahe conformance --config /path/to/my-ahe-project/ahe.yaml
ahe validate --config /path/to/my-ahe-project/ahe.yaml
```

`conformance` reports the adapter API generation, frozen benchmark interface,
active ABI registrations, and NexAU load smoke. `validate` additionally
returns the full resolved project and split report.

### Route A: offline lifecycle rehearsal

For a server-free lifecycle rehearsal, use the generated `ahe.yaml` unchanged:

```yaml
meta:
  backend: scripted
```

Run the deterministic example first:

```bash
ahe run --config /path/to/my-ahe-project/ahe.yaml
```

This phrase-scoring fixture does not call the evaluated agent. Its purpose is
to verify controller lifecycle, recovery, evidence, selection, export, and
idempotent resume.

### Route B: runtime-backed integration example

Use the separate generated configuration:

```bash
ahe conformance --config /path/to/my-ahe-project/ahe.runtime.yaml
ahe validate --config /path/to/my-ahe-project/ahe.runtime.yaml
ahe run --config /path/to/my-ahe-project/ahe.runtime.yaml
```

Before running, replace the exact endpoint/model placeholders and configure
credentials. This adapter opens actual evaluated-agent sessions for a direct
task and a protected `lookup_code` action. It requires the `web` extra because
the production meta backend uses `ddgs`.

### Route C: research campaign

Replace the runtime example with your benchmark adapter, native task splits,
reward verifier, trace conversion, candidate checks, public source roots,
adapter dependency paths, role supplements, and research seeds. Use a new
campaign artifacts directory whenever a frozen protected input changes.

See `RUNTIME_BACKED_EXAMPLE.md` for the complete Route B walkthrough.

### Common validation and import messages

`E2B SDK not installed. Install it with: pip install e2b` is an optional NexAU
import notice. The generated AHE routes use a local sandbox for evaluated
sessions and either a disabled or Docker-backed meta shell, so this message
does not block those routes. Install E2B only if your own project explicitly
selects an E2B integration.

The warning that host-imported candidate middleware is not isolated by an
operating-system sandbox is also non-blocking: conformance may still be valid
and the campaign can run. It means static validation is the protection
boundary for imported middleware. Use only trusted middleware code; enabling
the Docker meta shell does not sandbox Python imported by the evaluated
harness.

Warnings do not replace the `valid` field or command exit status. Read the
specific warning, decide whether its stated boundary is acceptable for the
experiment, and fix it before spending model calls when it is not.

## 8. Run or resume

Full campaign:

```bash
ahe run --config /path/to/my-ahe-project/ahe.yaml
```

Equivalent explicit script:

```bash
python /path/to/ahe-runner/run_ahe.py run \
  --config /path/to/my-ahe-project/ahe.yaml
```

Status:

```bash
ahe status --config /path/to/my-ahe-project/ahe.yaml
```

After a timeout, model-server restart, network failure, or adapter-classified
infrastructure failure:

```bash
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

Resume verifies and skips completed stages. Evaluation resumes only incomplete
units. Evolve starts from the interrupted attempt's last committed Git `HEAD`
and exposes archived dirty state as recovery evidence.

Do not delete a failed attempt to make resume proceed. Its identity and
artifacts are part of the recovery contract.

If a deterministic stage is `blocked`, first fix the external cause, then
explicitly authorize another attempt:

```bash
ahe retry-stage --config /path/to/my-ahe-project/ahe.yaml \
  --stage round.2.evolve \
  --reason "Corrected the adapter-side parser"
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

The reason and prior error remain in `stage.json`; no attempt is removed or
rewritten. The command only reopens a blocked stage while its configured
attempt budget still has capacity. Exhausted stages remain blocked and require
a new campaign rather than an identity-changing amendment.

## 9. Optional phase control

```bash
ahe run-round --config ahe.yaml --round 3
ahe close-evolution --config ahe.yaml
ahe run-validation --config ahe.yaml
ahe run-final-test --config ahe.yaml
```

Validation evaluates every checkpoint, then the adapter selects one through
its deterministic native key. Final test evaluates C0 and the selected
checkpoint only. Test results never trigger further evolution.

## 10. Read the outputs

```text
campaign-artifacts/<campaign>/
├── campaign_manifest.json
├── campaign_state.json
├── checkpoints/C0..CN/
├── workspaces/
├── evaluations/
├── evidence/
├── debugger/
├── evolve/
├── explore_skills/
├── history/
├── attribution/
├── stages/
├── selection/selection.json
└── final/
    ├── final_test.json
    └── selected-harness/
```

The harness to deploy or inspect is:

```text
final/selected-harness/
```

The complete lineage remains available:

- immutable intermediate harnesses;
- native evaluation artifacts and stable unit attempts;
- training-only evidence bundles;
- Source and Web `SKILL.md` outputs;
- every Debugger result;
- every Evolve attempt, call record, trace, recovery archive, and commit;
- validation, attribution, selection, and final-test decisions.

## Failure interpretation

- `interrupted`: infrastructure failed and `resume` may continue.
- `blocked`: deterministic failure, policy violation, invalid candidate, or
  exhausted attempts; inspect the stage's `error.json`. If capacity remains
  after correcting the cause, use `retry-stage`; it never erases the failed
  attempt.
- valid low or zero reward: completed benchmark evidence, never an
  infrastructure retry.
- campaign identity changed: use a new `artifacts_dir`; immutable inputs cannot
  change in place.
