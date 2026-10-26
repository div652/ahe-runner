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
  transient_retries: 0

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

### Evolution algorithm, selection, and pricing

`algorithm` chooses the search; the default is AHE. RRSI and operator
algorithms are described in [ALGORITHMS.md](ALGORITHMS.md) and
[RRSI.md](RRSI.md):

```yaml
algorithm:
  name: rrsi                  # ahe (default) | rrsi | module.path:Class
  settings: {}                # the algorithm's own settings
  role_supplements:           # benchmark briefs for the algorithm's roles
    proposer: ./rrsi_supplements/proposer.md

selection:
  policy: null                # null: algorithm default; validation | algorithm

models:
  evaluated:
    pricing:                  # cost accounting; required by RRSI's cost rule
      currency: USD
      input_per_million: 1.25
      cached_input_per_million: 0.125
      cache_write_per_million: 1.25
      output_per_million: 10.0
```

`meta.role_supplements` and `meta.explore` configure AHE's roles;
`algorithm.role_supplements` configures the selected algorithm's roles. The
NexAU meta backend still requires `meta.explore` with the web extra.
`ahe validate` reports the algorithm's resolved settings, its selection
policy, and any problems it finds with the campaign.

Pricing never reaches the provider. Use your provider's published prices per
million tokens; a gateway such as CAO lists them in its model catalog
(`GET /v1/models`, `billing.token_prices`).

### Identity settings and execution settings

A campaign's identity covers everything that can change a result: models,
harness, splits, metric, prompts, seeds, the endpoint and wire API, and trial
counts. Changing any of them on resume is refused.

Execution settings decide how the campaign runs but cannot change a
completed result. They may change between resumes:

- `transport`: `api_key_env`, `token_command`, `token_refresh_margin_seconds`,
  `request_timeout_seconds`, `model_catalog`, `negative_control`,
  `max_concurrent_requests`, `hung_call_retries`, `transient_retries`,
  `transient_max_wait_seconds`, `provider_block_markers`,
  `provider_block_seconds`, `client_id_header`, the `token_budget_*` fields,
  and `model_rejection_phrases`;
- `evaluation.max_concurrency`, `debugger.max_concurrency`, and
  `meta.max_concurrent_model_calls`;
- everything under `recovery`.

Each change is appended to `campaign_amendments.json` as an
`execution_settings` entry with the before and after values, so reports can
say which settings each part of the campaign ran under.

### Provider rejections in the middle of a trial

A provider sometimes refuses one agent request mid-trial with HTTP 400 or
422 that is not a context-length error, for example a content filter.
`evaluation.provider_rejection_policy` defines the outcome:

- `grade` (default): the trial is kept and its partial work is graded as an
  agent outcome;
- `rerun`: the trial is an infrastructure failure and runs again on resume,
  up to the stage attempt budget.

The two policies give different scores for the same harness, so the policy
is part of campaign identity. Under `rerun`, a high rejection rate biases
results towards trials that make fewer calls; adapters should report the
count (see the adapter guide).

### Wire API, authentication, and rate limits

The runner speaks two OpenAI wire APIs:

| `api_mode` | System prompt | Reasoning between turns | Token budget field |
|---|---|---|---|
| `chat_completions` (default) | system message | not carried | `max_tokens` |
| `responses` | `instructions` | reasoning items resent each turn | `max_output_tokens` |

`transport.api_mode` applies to every model. A model can override it with
`models.<role>.api_mode`, for a gateway that serves different model families
on different APIs. Set `temperature: null` for models that reject the
parameter; the runner then sends none and rejects any harness attempt to send
one.

The runtime always applies the configured wire API. If the harness manifest
protects `llm_config.api_type`, keep it consistent (`openai_responses` for the
Responses API, `openai_chat_completion` otherwise); `ahe validate` warns when
they differ.

Authentication is either a static key from `api_key_env`, or
`token_command`, an operator command that prints a bearer token:

```yaml
transport:
  base_url: https://gateway.example.com/openai/v1
  api_mode: responses
  token_command: >-
    az account get-access-token --scope api://example/.default --output json
  token_refresh_margin_seconds: 300
  client_id_header: X-Workload-ID      # optional stable per-session ID
```

The command may print the raw token, or JSON with `accessToken`,
`access_token`, or `token`, and optionally `expires_on`/`expires_at` (epoch
seconds) or `expires_in`. A token without an expiry is refreshed after 30
minutes. Each process caches its own token; tokens are never written to disk.

For rate-limited providers:

```yaml
transport:
  transient_retries: 6               # HTTP 429, 5xx, connection errors
  transient_max_wait_seconds: 120
  provider_block_markers: [PrincipalBlocked, retry-flood]
  provider_block_seconds: 1800
  token_budget_per_minute: 400000    # optional admission control
  token_budget_chars_per_token: 3.0
  token_budget_cut: 0.7
  token_budget_floor: 0.3
  token_budget_recover_seconds: 600
```

- **Retries.** Attempt n waits `max(Retry-After, 5 × 2^(n-1))` seconds, capped
  at `transient_max_wait_seconds`, plus up to 50% random extra, so clients
  refused together do not retry together. Every failed attempt is kept as
  `transient-attempt-N.json` in the call folder.
- **Provider blocks.** A 403 whose body names a configured marker means the
  provider blocked this client. Every process on the host stops sending to
  that endpoint for `provider_block_seconds`. Later calls fail without being
  sent (`"sent": false`), and the stage ends as infrastructure. Do not
  relaunch immediately; `ahe supervise` stops on a block.
- **Admission control.** With `token_budget_per_minute`, every process on the
  host shares a per-model 60-second token window. A request is sent only when
  its estimate (request characters ÷ `token_budget_chars_per_token` plus the
  output cap) fits. Each 429 multiplies the budget by `token_budget_cut`, down
  to `token_budget_floor` of the configured value, and pauses every client for
  the advised wait. Each quiet `token_budget_recover_seconds` restores 10%.
  Advertised limits and remaining-token headers are not trusted, because
  providers have refused well below them. Start below the advertised limit
  and keep the estimate conservative (3 characters per token rarely
  undercounts).

Endpoint state shared by processes lives under `$AHE_RUNNER_STATE_DIR`, or
`<tmp>/ahe-runner-<uid>` when it is unset. The model preflight uses the same
retries, window, and block handling as recorded calls.

`ahe status` reports `transient_attempt_count`, `rate_limited_attempt_count`,
and `provider_blocked_calls` next to the hung-call counts.

Each evaluation stage records its route fingerprint in `stage.json`, and
`selection.json` and `final_test.json` repeat it: the endpoint, wire API, authentication
kind, model settings, where the system prompt is placed, and whether
reasoning is carried between turns. The same harness and model can score
differently through different routes, so compare evaluations only when their
route fingerprints match, or report the difference.

### GH Copilot Server

Before a live run, explicitly confirm:

1. the exact loopback endpoint instance;
2. the exact model ID currently returned by `GET /v1/models`;
3. reasoning effort, including an intentional provider default;
4. the wire API (`chat_completions` or `responses`) and non-streaming mode;
5. task/attempt scope and model-call concurrency;
6. start-of-prompt recall near the configured input limit;
7. a comparative probe showing that requested reasoning effort changes
   observable behavior or latency.

Keep the unauthenticated extension on loopback. The runner performs a fresh
positive probe and nonexistent-model negative control for every model-backed
stage attempt, then verifies the observable response label on every call.

The model preflight's negative control accepts a refusal that names the
nonexistent model, carries a structured model error code (such as
`model_not_found` or `model_not_supported`), or matches a known gateway
phrase. Add gateway-specific wording with `transport.model_rejection_phrases`
rather than disabling the control.

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
and exposes archived dirty state as recovery evidence. Execution settings,
such as concurrency, retries, pacing, and attempt limits, may be changed
before a resume; the change is recorded in `campaign_amendments.json`.

For unattended campaigns, let the runner resume for you:

```bash
ahe supervise --config /path/to/my-ahe-project/ahe.yaml \
  --max-consecutive-failures 5 \
  --initial-backoff-seconds 60 --max-backoff-seconds 1800
```

`supervise` runs the campaign in a fresh process each time. After an
infrastructure stop it waits with exponential back-off and jitter, then
resumes. The failure count resets whenever a run completes at least one more
stage. It stops, and reports why, on:

- a blocked stage, or a configuration or integrity error (operator needed);
- a provider block (do not relaunch until the block lifts);
- `--max-consecutive-failures` stops in a row without progress.

Every launch and decision is appended to `reports/supervisor.jsonl`.
Combine it with `AHE_PRE_STAGE_COMMAND` to restart unhealthy local services.

If you drive the runner from your own Python script instead, put the code
that creates `CampaignController` under `if __name__ == "__main__":`.
Process-isolated trials start with the `spawn` method, which re-imports the
parent script; the runner refuses to start a campaign inside a trial process
and says so.

Do not delete a failed attempt to make resume proceed. Its identity and
artifacts are part of the recovery contract.

If a stage is `blocked`, first fix the external cause, then explicitly
authorize another attempt:

```bash
ahe retry-stage --config /path/to/my-ahe-project/ahe.yaml \
  --stage round.2.evolve \
  --reason "Corrected the adapter-side parser"
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

The reason and prior error remain in `stage.json` under `manual_retries`; no
attempt is removed or rewritten. `retry-stage` handles two cases:

| Blocked stage | What `retry-stage` allows |
|---|---|
| attempts remain (`recovery.maximum_stage_attempts`) | one more attempt |
| all attempts used, every attempt failed on infrastructure or was abandoned by a killed process, and the stage is retryable | `maximum_stage_attempts` more attempts, recorded as `attempt_allowance` |
| all attempts used, and any attempt was an agent, candidate, or benchmark failure | refused; start a new campaign |

The second case covers an outage that outlasts the attempt budget, such as a
model server that stays down across several resumes. No attempt produced a
result, so reopening cannot bias the campaign. The third case stays blocked so
a failing stage cannot be retried until it happens to pass.

### Check external services before each stage

A long campaign may depend on services that degrade over hours, such as a
local model server. Set `AHE_PRE_STAGE_COMMAND` to run a health check or
restart before every new stage attempt:

```bash
export AHE_PRE_STAGE_COMMAND="/path/to/check_model_server.sh"
export AHE_PRE_STAGE_TIMEOUT_SECONDS=1200   # optional; default 1200
ahe resume --config /path/to/my-ahe-project/ahe.yaml
```

The command is split with shell quoting rules and run without a shell, with
`AHE_STAGE_NAME` and `AHE_CAMPAIGN_DIR` set. Its output goes to stderr. It
does not run for completed stages. A nonzero exit or timeout stops the runner
with an infrastructure error *before* the attempt is recorded, so no attempt
is used; fix the service and resume. Each attempt records the command in
`attempt.json`. The hook is operational setup, not campaign identity, so
changing it does not invalidate resume.

### Extend a campaign with more rounds

This applies to AHE campaigns; RRSI refuses it because its edit-budget
schedule depends on the round count. To continue evolution beyond the
configured `campaign.rounds`, raise the value in the configuration file, then
record the change:

```bash
# edit ahe.yaml: campaign.rounds: 5 -> 10
ahe extend-rounds --config ahe.yaml --reason "Continue from C5"          # dry run
ahe extend-rounds --config ahe.yaml --reason "Continue from C5" --apply
ahe resume --config ahe.yaml
```

The command refuses any other protected-input change, including the runner
package, adapter, splits, models, transport, and meta inputs. Applied changes
are appended to `campaign_amendments.json`. Each entry is chained to the
previous identity hash. `campaign_manifest.json` and existing stage records
are never rewritten. Every later command checks the configuration against the
latest amendment.

If the campaign has already closed (`evolution.close`, `selection`, or
`final.complete` exists), add `--reopen-closed-evolution`. Every stage must be
completed. The closing stages and their outputs (`selection/` and `final/`)
are hash-verified and moved, never copied or deleted, to
`superseded/closure-NN/`, with `reopen.json`. The amendment records the
previous selection and the previous validation and test primary metrics.
Resume then reuses every earlier round and evaluation, runs the new rounds,
evaluates the new checkpoints on validation, selects over C0..CN by the same
rule, and tests C0 and the new selection. Test evaluations that already exist
are reused.

Meta-agents never read validation, test, `selection/`, or `final/`, so
reopening cannot change what Evolve sees. The decision to continue may have
been made after reading the test result, though. A report must therefore give
both the superseded closure and the new one, and say that the campaign was
extended after it closed.

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

### Evaluate a fixed harness

To measure a harness without evolving it (a baseline on all tasks, a selected
checkpoint on held-out tasks, more trials, or another route), use
`ahe evaluate` with the campaign configuration:

```bash
# A campaign checkpoint on the test split
ahe evaluate --config ahe.yaml --output ./evals/c5-test --checkpoint C5 --split test

# Any conformant harness folder on chosen tasks, with more trials and a seed
ahe evaluate --config ahe.yaml --output ./evals/new-harness \
  --harness ./candidate-harness --tasks ./tasks.json \
  --trials 5 --seed 7 --label candidate
```

`--tasks` is a JSON list (or one task ID per line) of tasks from one split.
Trials default to that split's configured count, and the seed defaults to
`campaign.root_seed`. The output folder must be outside the campaign
artifacts. It holds a frozen copy of the harness, the verified adapter
evaluation, its own stage record, and `evaluation_report.json` with the
summary and route fingerprint. Rerunning the same command resumes or reuses
it; a different command for the same folder is refused.

Compare two evaluations task by task:

```bash
ahe compare --baseline ./evals/c0-test --candidate ./evals/c5-test
ahe compare --baseline campaign-artifacts/<campaign>/evaluations/C0-final-test \
  --candidate ./evals/c5-test
```

The result gives the primary-metric delta, the mean per-task score delta, a
95% paired task-bootstrap interval, and the minimum detectable difference. It
warns when the evaluations used different routes, task sets, or primary
metrics.

### Read selection and final-test uncertainty

Selection still picks the best `adapter.selection_key`, earliest checkpoint
first on ties. `selection.json` and `final_test.json` now also report how
certain each decision is:

- `uncertainty.selected_vs_baseline`: the selected checkpoint against C0;
- `uncertainty.selected_vs_each_candidate`: the selected checkpoint's paired
  difference from every other checkpoint;
- `uncertainty.candidates_not_distinguishable_from_selected`: checkpoints
  whose interval against the selected one includes zero;
- `minimum_detectable_difference`: the half-width of a 95% interval for the
  paired mean. A difference smaller than this is within the measured noise.

With small validation sets the top validation score is partly luck. When
several candidates are indistinguishable from the selected one, report that,
and confirm the choice with `ahe evaluate` on more tasks or trials before
claiming an improvement. Report the interval with every final-test delta.

## 10. Read the outputs

```text
campaign-artifacts/<campaign>/
├── campaign_manifest.json
├── campaign_amendments.json   # after extend-rounds or execution changes
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
├── reports/supervisor.jsonl   # only under ahe supervise
├── selection/selection.json
├── superseded/closure-NN/     # only after a reopened closure
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
  exhausted attempts; inspect the stage's `error.json`. After correcting the
  cause, use `retry-stage`. It works while capacity remains, or after
  exhaustion when every attempt failed on infrastructure. It never erases the
  failed attempt.
- valid low or zero reward: completed benchmark evidence, never an
  infrastructure retry.
- campaign identity changed: use a new `artifacts_dir`; result-affecting
  inputs cannot change in place. A larger `campaign.rounds` is recorded with
  `extend-rounds`; execution settings may change freely and are recorded
  automatically.
- deleting a throwaway campaign: checkpoint and stage files are written
  read-only, so run `chmod -R u+w <artifacts_dir>` before removing it.
