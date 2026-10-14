# AHE Runner Architecture

## Purpose

This repository turns the current one-off Tau2/NexAU evolution controller into
a reusable operator tool. A user supplies:

1. a file-backed NexAU harness;
2. a typed Python benchmark adapter;
3. a campaign YAML file;
4. exact model and resource settings.

One command then performs:

```text
freeze C0
-> evaluate C0 on training tasks
-> Explore once
-> repeat N evidence-driven evolution rounds
-> close evolution
-> evaluate C0..CN on held-out validation
-> select one checkpoint
-> evaluate C0 and the selected checkpoint on final test
```

The output is not only a final harness. It is an auditable campaign containing
every immutable checkpoint, evaluation, evidence bundle, Debugger report,
Evolve workspace, model call, hypothesis, attribution result, validation
decision, and recovery event.

## Scope

Version 2 supports:

- NexAU harnesses;
- runner-owned evaluated NexAU `Agent`/`Executor` sessions;
- typed Python `BenchmarkAdapterV2` plugins;
- runtime-only protected structured benchmark actions;
- executable harness ABI conformance;
- configurable OpenAI-compatible chat-completions endpoints;
- a complete evolution, validation-selection, and final-test lifecycle;
- durable stage recovery from the last verified checkpoint.

The current evaluated-session ABI is text-interactive. It supports stateful
turns and runtime-injected structured actions, but does not yet preserve typed
image/audio blocks, dynamic tool-set changes, asynchronous interruptions, or a
benchmark event stream.

Legacy `BenchmarkAdapter` plugins remain loadable. Version 2 intentionally
does not claim to support arbitrary agent frameworks. The benchmark protocol
is pluggable; the harness runtime is NexAU-specific.

## Core AHE invariants

The runner preserves the following causal rule:

```text
fixed evaluated model + checkpoint Ci
-> completed training evaluation of Ci
-> immutable evidence derived from that evaluation
-> one global logical Evolve update Ui+1
-> immutable checkpoint Ci+1
-> evaluation of Ci+1
```

In particular:

1. A checkpoint is shared across all tasks in an evaluation.
2. An update may use only completed training evidence from its parent.
3. Every produced checkpoint is evaluated.
4. Validation and final-test evidence are unavailable during evolution.
5. Infrastructure failures are not converted into candidate failures.
6. Every change is a falsifiable hypothesis recorded in a change manifest.
7. Git commits and content hashes are the audit and rollback substrate.

## Operator project layout

The operator creates a separate project repository with this layout:

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
│   │   └── *.tool.yaml
│   ├── tools/
│   │   └── *.py
│   ├── middleware/
│   │   └── *.py
│   ├── skills/
│   │   └── <skill-name>/SKILL.md
│   └── sub_agents/
│       └── <agent-name>/agent.yaml
└── campaign-artifacts/                 # generated; never an Evolve write root
```

Protected benchmark code, hidden tasks, evaluators, official external-action
execution, secrets, and model routing must live outside `harness/`.

## Harness manifest

`harness_manifest.yaml` defines the observable evolution surface:

```yaml
schema_version: 2
abi_version: ahe.harness/v2

framework:
  name: nexau
  version: "0.3.9"

agent_config: agent.yaml

components:
  system_prompt:
    - systemprompt.md
    - sub_agents/**/systemprompt.md
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

attachments:
  long_term_memory: system_prompt_suffix

runtime_owned:
  - ShortTermMEMORY.md

required_files:
  - agent.yaml
  - systemprompt.md
  - LongTermMEMORY.md

limits:
  maximum_file_bytes: 1000000
  maximum_tree_bytes: 8000000
```

The manifest itself is outside the candidate harness. The Evolve Agent cannot
change the component boundaries, required files, framework version, or limits.
All regular harness files are hashed; symlinks and path escapes are rejected.

The runner verifies that every registered prompt, tool descriptor, tool
implementation, middleware, skill, sub-agent, and memory path resolves inside
the harness. A file is not considered an active component merely because it
exists; every declared file must be attached, and every attached evolvable
file must be declared.

Tool schemas must be strict JSON Schema objects, schema properties and required
fields must match explicit keyword-compatible Python parameters, middleware
constructor arguments must bind, skills need valid substantive frontmatter,
and sub-agent configs recursively conform. The installed NexAU version must
equal the manifest version.

Strict tool schemas require `additionalProperties: false`. A protected
manifest can opt into allowing that keyword to be omitted for exact
benchmark-schema parity; explicit open schemas remain invalid and candidate
bindings still cannot accept `**kwargs`.

Long-term memory is injected by the runner into the root
`system_prompt_suffix` at session construction. Empty memory adds nothing;
nonempty memory receives a blank-line separator because NexAU concatenates
suffix text directly. `ShortTermMEMORY.md` is runtime-owned example state;
actual short-term agent memory is NexAU session history.

## Campaign configuration

`ahe.yaml` is the immutable experiment declaration:

```yaml
schema_version: 1
campaign:
  name: example-campaign
  artifacts_dir: ./campaign-artifacts/example-campaign
  rounds: 5
  root_seed: 42

project:
  harness_dir: ./harness
  harness_manifest: ./harness_manifest.yaml
  adapter: benchmark_adapter:ExampleAdapter

models:
  evaluated:
    model: exact-agent-model-id
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
  api_key_env: AHE_API_KEY
  api_mode: chat_completions
  stream: false
  request_timeout_seconds: 300
  observable_model_policy: exact

evaluation:
  train_trials: 2
  validation_trials: 2
  test_trials: 3
  max_concurrency: 8

debugger:
  max_concurrency: 8
  max_iterations: 80

recovery:
  maximum_stage_attempts: 3
  archive_incomplete_attempts: true

meta:
  backend: nexau
  max_concurrent_model_calls: 2
  max_context_tokens: 200000
  max_running_subagents: 5
  evolve_max_iterations: 500
  explore_max_iterations: 200
  explore:
    source_enabled: true
    web_enabled: true
    web_search_backend: ddgs
    research_seed_urls: []
  role_supplements:
    source: ./meta_supplements/source.md
    web: ./meta_supplements/web.md
    debugger: ./meta_supplements/debugger.md
    evolve: ./meta_supplements/evolve.md
```

The resolved config, complete runner package, protected prompts and tool
resources, packaged method reference, resolved benchmark-family context,
role supplements, research seed URLs, explicit public adapter roots, declared
adapter dependencies, installed NexAU version, adapter identity, benchmark
identity, endpoint preflights, and repository commits are frozen in
`campaign_manifest.json`.

The package pins NexAU to the upstream `v0.3.9` Git tag because that release is
not distributed as a resolvable `nexau==0.3.9` registry artifact. It also
declares `greenlet` directly because NexAU imports SQLAlchemy's async layer at
package import time without declaring that required extra transitively.

Changing any frozen protected input requires a new campaign directory. Resume
reports which top-level identity inputs changed and never silently adopts a
changed model, split, seed, metric, prompt, supplement, safe source root,
adapter dependency, harness manifest, method reference, tool implementation,
or resource limit.

## Typed benchmark adapter and evaluated runtime

The adapter is loaded from the configured `module:Class` reference and must
implement `BenchmarkAdapterV2`.

Its responsibilities are deliberately benchmark-native:

```python
class BenchmarkAdapterV2(ABC):
    def identity(self) -> AdapterIdentity: ...
    def split(self, name: SplitName) -> SplitDefinition: ...
    def benchmark_interface(self) -> BenchmarkInterface: ...
    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult: ...
    def verify_evaluation(self, request: VerificationRequest) -> EvaluationResult: ...
    def materialize_training_evidence(
        self, request: EvidenceRequest
    ) -> EvidenceBundle: ...
    def validate_candidate(
        self, request: CandidateValidationRequest
    ) -> ValidationResult: ...
    def compare(
        self, before: MetricSummary, after: MetricSummary
    ) -> TransitionSummary: ...
    def selection_key(self, summary: MetricSummary) -> tuple: ...
```

The adapter owns:

- protected task selection and hidden state;
- official external-action execution;
- rollout orchestration specific to the benchmark;
- native reward and aggregate metric semantics;
- trace and evaluator artifact interpretation;
- classification of candidate outcomes versus infrastructure failures;
- conversion to the standard training-evidence manifest;
- benchmark-specific candidate validation;
- held-out checkpoint tie-breaking after evolution closes.

The runner owns:

- construction of the evaluated NexAU `Agent` and native `Executor`;
- one isolated agent/history per trial and stateful repeated activations;
- exact evaluated-model routing for root and sub-agents;
- runtime-only protected benchmark tool schemas and call-ID integrity;
- structured action capture before NexAU tool execution;
- immutable checkpoint identity;
- stage identity and recovery;
- exact meta-model transport checks;
- the Explore, Debugger, and Evolve lifecycle;
- read/write roots exposed to meta-agents;
- validation/test secrecy;
- evidence and output hashing;
- campaign history and attribution;
- final checkpoint export.

Before accepting an adapter result, the runner binds it to the exact evaluation
request and verifies a common unit envelope: checkpoint/split identity,
task-by-trial coverage, seeds, completion paths and hashes, task-state/rollout
coverage, primary-metric consistency, one canonical trial fact per unit, and
the complete artifact map. The adapter independently recomputes each fact's
native outcome, optional score, termination reason, and diagnostic
pass/fail/other class.

The immutable `BenchmarkInterface` declares prompt/data/repository/environment
inputs and the complete protected structured-action surface. It is frozen into
campaign identity and shown to Source Explore, Debugger, and Evolve.

Protected action schemas extend only the in-memory NexAU config. In OpenAI
structured mode they are sent through the API `tools` field, not appended to
prompt text or written into the base harness. The adapter executes official
actions and resumes the same session with exact `ToolResultBlock` call IDs.
Evolve may improve how the harness chooses actions but cannot modify or
reimplement the benchmark interface.

String `context` remains NexAU template input. Object-valued
`session_resources` are instead placed in a new per-session `GlobalStorage`
shared only by that root agent and its sub-agents. This avoids accidental
process-global cross-trial routing, but adapters may expose only intentional
harness capabilities through it; it is not a security boundary for hidden
benchmark state.

A trusted capture hook runs before candidate after-model middleware. NexAU
legacy `after_model_hooks` are normalized into the same guarded chain before
executor construction. Once an official action is present, candidate
after-model processing is intentionally skipped and cannot request another
model turn; control returns to the adapter immediately and resumes only after
the exact official results are supplied.

## Standard evidence bundle

Every adapter converts native training artifacts into:

```text
evidence/Ci-train/
├── evidence_manifest.json
├── overview.md
├── metrics.json
└── tasks/
    └── <stable-task-key>.json
```

`evidence_manifest.json` records:

- checkpoint, source evaluation ID/path/hash, and source request identity;
- supervision policy (`dense` or `sanitized`);
- native primary metric name, value, and direction;
- secondary metrics;
- task count and rollout count;
- hashes of every source and generated artifact;
- whether true task IDs are exposed;
- paths the Debugger may read.

Each schema-version-3 task document contains the adapter-approved task identity
and one common envelope per expected trial: evaluation unit ID, trial index,
seed, native outcome/score, termination reason, diagnostic class, and hashed
evaluation-relative trace plus result/evaluator artifacts. Benchmark-native
events remain nested under that envelope. The runner rejects missing trials,
invented artifacts, foreign source evaluations, stale metrics, and any trial
semantic field that differs from the adapter-verified canonical fact.
Validation and test adapters never create an evidence bundle accessible to the
meta-agents.

Each task is investigated by a bounded read-only NexAU Debugger. It can list,
search, and read ranged portions of authorized evidence and raw training
artifacts regardless of total file size. Returned content is byte-capped with
explicit continuation/truncation metadata, and search reports skipped inputs
honestly. When passing and failing trials coexist, the structured diagnosis
must compare both and identify their earliest meaningful divergence; otherwise
it records the exact evidence limitation. Every diagnosis citation names a
structured line or byte range and is accepted only when that range was returned
by a nonempty bounded read or search hit. Verified trial facts determine
whether paired comparison is mandatory and which artifacts belong to passing
or failing trials.

## Seven evolvable component classes

The Evolve Agent is always reminded that the available component classes are:

1. system prompt;
2. tool descriptions;
3. tool implementations;
4. middleware;
5. skills;
6. sub-agents;
7. long-term memory.

The runner validates both file visibility and runtime attachment. Candidate
tool implementations, middleware, sub-agent imports, and other imported
Python execute in the host process; static checks do not create kernel
isolation.

## Meta-agent fidelity

The package contains full, versioned prompts and configs for:

- Source Explore Agent;
- Web Explore Agent;
- per-task Agent Debugger;
- global Evolve Agent.

The adaptation preserves:

- evidence reading order;
- paired-trial and causal analysis;
- prior-change attribution;
- component-level design choice;
- rollback/pivot discipline;
- Git and change-manifest deliverables;
- context management;
- one-shot logical lifecycle;
- progressive disclosure of evidence.

Each Evolve round commits a strict typed `change_manifest.json`. Stable logical
change IDs cover the complete component-file diff and record supporting
evidence, mechanisms, component rationales, predicted task fixes, and
task-level regression risks. After the combined candidate is evaluated, the
runner writes one heuristic prediction/risk verdict per logical change, plus
unattributed improvements and unanticipated regressions. This supports later
keep, improve, or rollback/pivot decisions; it is not an isolated causal
experiment for each edit.

Evolve also receives read-only immutable checkpoint history plus bounded
checkpoint log/diff tools. It may selectively restore named component files
from an earlier checkpoint, with the source, paths, and rationale persisted as
evidence. This enables agent-decided `KEEP`, `IMPROVE`, and `ROLLBACK + PIVOT`
without automatic score-triggered rollback or whole-harness replacement.

Benchmark-specific wording is supplied by the adapter through a declared
`MetaPromptContext`; it does not replace the operating contract with a short
role summary.

Source and Web Explore outputs are packaged as real read-only NexAU skills
registered with the Evolve Agent. Full content is loaded through `LoadSkill`
only when needed.

## Durable stage state machine

Every operation is a named stage:

```text
prepare
baseline.evaluate
explore.source
explore.web
round.1.debug
round.1.evolve
round.1.validate
round.1.freeze
round.1.evaluate
round.1.attribute
...
evolution.close
validation.C0
...
validation.CN
selection
final_test.C0
final_test.selected
complete
```

Each stage directory contains:

```text
stage.json
attempts/
  0001/
    attempt.json
    logs/
    outputs/
```

`stage.json` contains:

- stable stage ID;
- hashes of semantic inputs;
- status (`pending`, `running`, `completed`, `blocked`);
- attempt history;
- verified output hashes;
- timestamps;
- parent stage IDs.

A stage is complete only after all required outputs are present and their
hashes verify. The controller derives the next action from completed stages,
not from the existence of a directory or an optimistic state flag.

## Interrupted Evolve recovery

One evolution round has one logical Evolve stage, but that stage may have
multiple infrastructure attempts.

The Evolve workspace is a standalone Git repository seeded from the immutable
parent checkpoint. Evolve tool calls can create explanatory commits. Those
commits are durable progress checkpoints.

If the model server, process, or network fails:

1. the current execution attempt is closed as `infrastructure_failed`;
2. all model calls, traces, logs, and any uncommitted diff are archived;
3. the workspace is restored to its last clean Evolve commit;
4. a new attempt receives the same stage ID, parent evidence, and workspace;
5. its query includes a recovery note and paths to prior attempt evidence;
6. it continues the same logical update rather than creating an independent
   second candidate.

The runner never retries because a benchmark reward is low. It retries only
an adapter- or transport-classified infrastructure failure.

If no Evolve commit exists beyond the seed commit, recovery restarts from the
parent checkpoint but still records the failed attempt. If maximum stage
attempts are exhausted, the campaign blocks without corrupting earlier
checkpoints.

Uncommitted edits are evidence, not a checkpoint. They are archived before the
workspace is reset to the last successful commit.

A deterministic blocked stage can be explicitly reopened only while its
attempt budget has capacity. `retry-stage` records the operator reason and
prior error, then changes the stage to interrupted so normal `resume` creates
the next immutable attempt. It never erases attempts or changes semantic
inputs.

## Model transport contract

The default transport uses non-streaming OpenAI-compatible chat completions.
For every model-calling role, the runner records:

- requested exact model ID;
- reasoning setting;
- request hash;
- returned observable model label;
- response or error;
- timeout and retry classification.

Before a model-backed meta stage, and once before the first evaluated session
in each evaluation stage attempt, a fresh preflight checks:

1. endpoint availability;
2. model catalog membership when `/models` exists;
3. a positive exact-model completion;
4. a nonexistent-model negative control when supported.

Fresh preflight timestamps and random negative-control IDs are attempt
evidence, not semantic campaign inputs. This prevents fresh preflights from
changing checkpoint or evaluation identities.

The recorded client disables OpenAI SDK retries, and runner-created NexAU
agents use one native request attempt. Infrastructure recovery remains in the
explicit outer stage-attempt policy, so model identity and policy failures are
not multiplied by an inner retry loop.

Evaluated sessions use the same recorded exact-model client and native NexAU
loop. Exact model ID, reasoning policy, temperature, output limit, non-streaming
mode, and retry ownership are recursively enforced in every configured
sub-agent and again at the recorded request boundary. Protected aliases and
`extra_body` collisions are rejected before provider access. Resolved API keys
are injected only into in-memory configurations and clients, never generated
meta-agent YAML, while each session preserves its own `HistoryList`.

When configured, `transport.max_concurrent_requests` provides one
process-wide semaphore per endpoint for recorded clients. Adapter-owned
provider calls join that same budget only through the public
`model_request_slot()` seam.

## Evaluation recovery contract

An adapter must use stable evaluation and rollout identities derived from
semantic inputs. A resumed evaluation must:

- verify every completed unit before skipping it;
- preserve prior attempt artifacts;
- continue absolute attempt numbering;
- reject malformed or gapped attempt histories;
- never retry a completed valid zero reward;
- remove failure summaries only after full verified completion;
- generate aggregate metrics only from verified completed units.

The runner supplies reusable artifact-store helpers so adapters do not need to
reimplement these rules.

## Candidate validation

Before a checkpoint can freeze, the runner performs generic validation:

- required files and component globs;
- no symlinks or path escapes;
- file and tree size limits;
- YAML/JSON parse checks;
- Python compilation;
- NexAU `AgentConfig` load in a subprocess;
- registration and referenced-path checks;
- protected config invariants;
- exact task-ID memorization checks when dense IDs are visible;
- clean Git status;
- at least one non-manifest component change;
- valid change manifest.

The manifest-selected `agent_config` path is authoritative for conformance,
smoke loading, and evaluated runtime construction; the root file is not
required to be named `agent.yaml`. Candidate task-ID scanning ignores very
short or numeric identifiers that cannot be matched reliably and leaves
benchmark-specific leakage patterns to the adapter validator.

Registered local Python dependencies are discovered through both observed
eager imports and a recursive static import graph, including imports inside
tool functions. The validator never executes a tool merely to discover helper
modules.

The adapter then performs benchmark-specific validation. Neither validator may
run held-out tasks.

## Outputs

The campaign directory contains:

```text
campaign-artifacts/<campaign>/
├── campaign_manifest.json
├── campaign_state.json
├── checkpoints/
│   ├── C0/
│   └── ...
├── workspaces/
├── evaluations/
├── evidence/
├── debugger/
├── evolve/
├── explore_skills/
├── history/
├── attribution/
├── stages/
├── selection/
├── final/
└── reports/
```

The final selected harness is exported to:

```text
final/selected-harness/
```

Its manifest links back to the selected checkpoint, validation decision, final
test, complete file hashes, and Git history. The runner verifies a temporary
copy against the immutable checkpoint before publishing it atomically; an
existing partial or mismatched export is rejected rather than blessed.

## CLI

The initial CLI is:

```text
ahe init-project PATH
ahe conformance --config PATH
ahe validate --config PATH
ahe run --config PATH
ahe resume --config PATH
ahe status --config PATH
ahe run-round --config PATH --round N
ahe close-evolution --config PATH
ahe run-validation --config PATH
ahe run-final-test --config PATH
```

`run` and `resume` use the same idempotent controller. `run` creates a campaign
when absent; `resume` requires an existing campaign. Both stop on a blocked
stage and print its durable error artifact.

## Lessons incorporated from the Tau2 campaign

The design explicitly prevents the failures encountered during the first
adaptation:

| Failure | General correction |
|---|---|
| weak shortened meta-agent prompts | package full prompts and maintain a semantic fidelity matrix |
| Explore guides mounted as plain files | package Explore outputs as genuine read-only skills |
| concurrent rollouts shared mutable skill state | require per-rollout runtime roots and stable unit isolation |
| recalled sub-agents bypassed exact-model routing | bind and verify one model-client policy for all descendants |
| client timeout left upstream model work alive | use explicit request deadlines/cancellation where supported and no nested retries |
| fresh preflights changed resume identity | treat preflights as attempt evidence, not semantic run inputs |
| resumed evaluation generated a new run ID | derive unit IDs from the persisted semantic run identity |
| attempt numbering restarted at one | preserve contiguous absolute attempt histories |
| interrupted one-shot Evolve could not continue | checkpoint progress through Git and continue one logical stage across infrastructure attempts |
| completion inferred from partial directories | require verified stage output hashes |
| valid zero rewards risked retry | retry only infrastructure-classified failures |
| middleware described as securely isolated | distinguish static restriction from an operating-system sandbox |

## Security boundary

The runner enforces filesystem allowlists for meta-agents and keeps benchmark
hidden state outside those roots. This is a real access-control boundary for
the runner's own tools.

Arbitrary candidate tool implementations, middleware, sub-agents, import-time
Python, and adapter code are not made safe by static validation. The initial
deployment model is trusted local operators and benchmark authors. Operators
requiring hostile-code isolation must configure a process/container/VM
boundary around evaluated execution. The runner reports this limitation rather
than claiming a kernel sandbox.
