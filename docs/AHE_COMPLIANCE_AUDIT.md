# AHE compliance and plug-and-play audit

**Audited implementation:** `a7704c49d2c0488135973cf911e4f26c9e146f50`.

**Verdict:** This is a substantial, useful foundation for the proposed product,
but not yet a fully faithful or convincingly demonstrated plug-and-play
implementation of original AHE. The campaign structure is largely correct. Important parts of
AHE's diagnosis, prediction checking, and practical rollback have been weakened.
Separately, several runtime and adapter-contract defects prevent an unqualified
plug-and-play claim.

These are repairable gaps, not a reason to abandon NexAU or redesign the entire
project. The highest-value next step is to restore the missing AHE mechanisms,
fix the demonstrated boundary failures, and exercise one small real benchmark
through the public adapter interface.

For the plain-language version, read [the short report](AHE_COMPLIANCE_SUMMARY.md).
This audit proposes changes; it does **not** implement them.

## 1. Scope, authorities, and confidence

The order of authority is the supplied paper, the original executable
implementation, and then the explanatory methods reference. Where the paper
and executable implementation differ, both are reported rather than treating
either as silently definitive.

| Authority | Pinned evidence |
|---|---|
| Supplied paper | `~/AHE-new/agentic_harness_engineering.pdf`; SHA-256 `100e369bf09aa61242a105e2174c28fb9efbae635feff05ce18e0cb54b231b3e`; especially sections 3 and 4, Algorithm 1, and Appendix A |
| Original AHE implementation | `~/AHE-new`, original `main` / `origin/main` at `8b2a55d97590363fe50c3cc6b5e833b020a4bb4c` |
| Explanatory reference | `~/AHE-new/AHE_METHODS_REFERENCE.txt`, including its paper identity and separation of conceptual method from implementation |
| Runner | `~/ahe-runner`, audited commit above, package version `0.2.0` |
| Runtime actually inspected | NexAU `0.3.9`, installed from commit `35ee1861546db3cb280a6e17e38a74060d7c96c3`; Python `3.14.7` |

The local AHE checkout also contains later Tau2 adaptations. The original
controller and Evolve/Explore sources used here were compared with the original
baseline; the inspected method surfaces were unchanged. The Tau2-specific
controllers and benchmark modifications are **not** the compliance reference.

Source notation below:

- `A/` means the original `~/AHE-new` checkout at the pinned baseline.
- `R/` means this repository at the audited commit.
- `N/` means `.venv/lib/python3.14/site-packages/nexau/` inside this repository.

Line references describe the audited implementation, not subsequent fixes.
Evidence combines source tracing and deterministic reproductions. The important
stop-tool and external-action failures were exercised through the real NexAU
executor with fake model responses, not merely inferred from mocks of the
runner. No live model campaign or benchmark-performance measurement was made.
Consequently, this report does not claim that a real campaign improves or fails
to improve task scores.

## 2. What is already right

The controller implements a coherent lifecycle:

```text
freeze C0
  -> evaluate C0 on training tasks
  -> build evidence
  -> run Source and Web Explore once
  -> repeat N times:
       diagnose current results
       evolve one shared harness
       freeze and evaluate the next checkpoint
       record the transition
  -> close evolution
  -> validate C0 through CN
  -> select using the adapter's ordering, with earliest-checkpoint tie breaking
  -> test C0 and the selected checkpoint
  -> export the selected harness
```

This gets several important things right:

- Every produced candidate, including the final candidate, is evaluated.
- One candidate is shared across tasks; this is harness optimization rather
  than generating a separate solution or harness for each task.
- The V2 adapter receives a runner-owned evaluated-agent runtime. It can own
  benchmark orchestration without replacing NexAU's Agent/Executor loop.
- Prompts, tool descriptions and implementations, middleware, skills,
  sub-agents, and long-term memory have explicit manifest/attachment surfaces.
- Benchmark-owned structured actions are added in memory, not written into the
  evolvable harness. Ordinary call-ID/result pairing is checked.
- Checkpoints, stage records, training history, and much of the artifact chain
  are persisted and hashed. Recovery preserves committed work rather than
  casually deleting a failed attempt.
- Native benchmark metrics and selection semantics remain adapter-owned.
  A valid zero reward is not automatically retried.
- Documentation openly acknowledges the absence of a live campaign and the
  limits of host-executed Python isolation.

Primary evidence: `R/src/ahe_runner/controller.py:116-258`,
`R/src/ahe_runner/adapter.py`, `R/src/ahe_runner/runtime.py:135-345`,
`R/src/ahe_runner/conformance.py`, and `R/docs/VALIDATION_RESULTS.md`.

## 3. AHE fidelity: do not confuse three different questions

The runner can follow the general method without reproducing the paper's exact
experiment. Conversely, retaining role names and an outer loop is not enough
to preserve the mechanisms that made that method distinctive.

| Mechanism | Paper / original executable implementation | Runner assessment |
|---|---|---|
| Fixed evaluated model; evolve surrounding harness | Central invariant | Intended and substantially implemented, but effective settings are not fully enforced; R03 |
| Seven harness component classes | Explicitly exposed to evolution | Present, with attachment restrictions that reject some legitimate package layouts; P02 |
| One global candidate, evaluated on a task collection | Core method | Preserved |
| Dense experience, not just scalar rewards | Raw trajectories, verifier artifacts, resource/termination evidence | Adapter can supply these, but common requirements and usable access are incomplete; A02, C02 |
| One Debugger job per task, covering its rollouts | Iterative, tool-enabled investigation; compare passing and failing runs where available | Task grouping is present; investigative mechanism and required paired analysis are not; A02 |
| Progressive disclosure | Overview, task diagnosis, then raw evidence | Layout partially present; large-file access and direct Debugger calls break the intended drill-down; A02 |
| Predictions and risks per logical edit | Manifest and next-iteration attribution are per change | Prompt/consumer mismatch and aggregate-only verdict; A01 |
| Git-backed edits and rollback/pivot | Continuous workspace history; explicit Evolve decisions | Per-round commits are retained, but Evolve loses the practical historical rollback surface and instructions; A03 |
| Source and Web Explore seed knowledge | Two distinct research roles; original code overlaps them with baseline evaluation | Both exist and run concurrently with each other, but only after baseline evaluation; scheduling deviation, not a methodological blocker |
| Exploration deliverable depth | Exact framework interfaces/templates; substantial, evidence-backed web research | Topics retained, mandatory depth and source requirements weakened; section 5 |
| Updating Explore-produced skills | Paper allows refinement/removal from iteration 2; public code's placement outside the nominal editable workspace makes this less clear | Runner explicitly makes these artifacts read-only; definite paper difference, less clear public-code difference |
| Automatic rollback | Algorithm 1 places rollback before distillation; ordinary public-code loop explicitly leaves rollback to Evolve | Absence of automatic rollback alone is **not** a new public-code deviation; A03 concerns the additional loss of agent-mediated rollback |
| Infrastructure failures in scores | Paper counts aborted attempts as zero; original statistics include exceptions in denominators | Runner blocks/resumes infrastructure failures instead. Sensible possible product policy, but not the same experiment |
| Validation selection and final test | Paper's primary optimization uses all 89 training tasks, with separate transfer analysis | Added held-out selection is a useful generalization, not literal reproduction; its secrecy must actually hold; C01 |
| Arbitrary starting harness | Paper experiment starts with a minimal coding harness | Accepting an existing NexAU harness is appropriate for this product; do not force every user back to a bash-only seed |
| Explicit post-edit evaluation of CN | Original staggered loop can leave an unevaluated final update | Runner's explicit checkpoint sequence is an improvement, not a defect to copy |

Original anchors: `A/evolve.py:557-743`, `1103-1160`, `1216-1545`,
`2239-2330`, `4060-4147`, and `4230-4639`;
`A/agents/evolve_agent/evolve_prompt.md`; paper section 3 and Algorithm 1.

Optional original features such as best-of-N candidate sampling are not
prerequisites for implementing the core method. Nor should a general adapter
be forced to use Terminal-Bench's metric when its native benchmark defines
something else.

## 4. Prioritized findings and repair contracts

**P1** means the issue blocks a strong claim of faithful or reliable end-to-end
operation. **P2** means an important integration, reproducibility, or usability
gap. These are delivery priorities, not security severity ratings.

### A01 / P1: The edit-prediction contract does not connect to attribution

**Observed.** The Evolve prompt asks for top-level `predicted_effects` and
`regression_risks` as lists of strings. `write_transition()` only reads
predictions from dictionary entries containing `task_keys`. A conforming
prompt-shaped manifest therefore produces an empty predicted-task set.
Regression risks are not consumed, and the output has one overall
classification rather than a verdict for each logical change.

The freeze-time validator is weaker still: `changes: [{}]` is accepted.
Declared paths need only be a subset of changed paths, so unaccounted edits
are also permitted. The completion tool has somewhat stronger path checks,
but there is no single shared, enforceable method contract.

**Why it matters.** AHE is supposed to learn whether a particular proposed
intervention behaved as predicted. An aggregate score change cannot replace
that feedback, particularly when several edits interact.

**Repair.** Define one typed manifest used by the prompt, tools, freeze logic,
and attribution. Each logical edit needs a stable ID, component/path set,
supporting evidence, root-cause/fix explanation, commit identity, predicted
task fixes, and regression risks. Require complete diff coverage. Produce
per-edit verdicts and a separate unexpected-regression summary. Do not treat
these observational verdicts as proof of causality. Keep optimizer bookkeeping
distinct from evaluated runtime artifacts and task-solution memorization.

**Acceptance.** Two edits with different predictions produce separate
verdicts; a risky task and an unpredicted regression remain distinguishable.
Prompt-generated examples round-trip through the actual parser. Missing
evidence, malformed rows, nonexistent commits, and undeclared changed files
are rejected before a checkpoint is accepted.

Evidence: [history.py:71-106](../src/ahe_runner/history.py#L71-L106),
[workspace.py:304-339](../src/ahe_runner/workspace.py#L304-L339),
`R/src/ahe_runner/meta/prompts/evolve.md:177-220`;
original `A/evolve.py:2239-2330`. Both schema mismatch and permissive manifest
acceptance were reproduced.

### A02 / P1: Debugger is a one-shot summarizer, not the original investigator

**Observed.** `_run_debugger_task()` serializes a task detail into a direct
chat-completion request. Its extra turns correct malformed JSON; they are not
tool-assisted investigation. It has no file tools, raw-trace dereferencing,
context compaction, or input-size guard. A detail containing trace paths does
not let this Debugger read those traces. Inlining everything instead creates
an unbounded-context problem.

The prompt does not require the original passing-versus-failing divergence
analysis. The overview also lacks the original task-linked, one-line causal
summaries. Separately, the shared meta tools refuse any file over 160,000
bytes **before** applying a requested line range. Search silently skips such
files without listing them as skipped.

**Why it matters.** This weakens AHE's central mechanism: understanding where
execution went wrong from sufficiently rich evidence. Good JSON formatting
and a plausible root cause do not compensate for missing access.

**Repair.** Use a bounded, read-only NexAU Debugger with navigable task/rollout
artifacts and structured final output. Require paired pass/fail comparison
when available, and explicit evidence limitations otherwise. Provide bounded
line/byte reads, indexed search, and honest truncation metadata; limit returned
content, not the total size of the file that contains it.

**Acceptance.** A task with one passing and one failing long trace is diagnosed
by citing the actual divergence and verifier evidence. A requested small range
in a multi-megabyte trace is readable. Search reports truncation or skipped
inputs explicitly. Context pressure does not silently remove required evidence.

Evidence: [nexau_backend.py:425-523](../src/ahe_runner/meta/nexau_backend.py#L425-L523),
`R/src/ahe_runner/meta/prompts/debugger.md`,
`R/src/ahe_runner/meta/tools.py:20-157`;
`A/evolve.py:1103-1160,1216-1545`. A 160,008-byte fixture demonstrated the
small-range refusal and silent search omission.

### A03 / P1: Agent-mediated rollback and pivot are materially weaker

**Observed.** Each Evolve round starts a fresh Git repository seeded with the
parent's files. Historical workspaces exist on disk, but the agent's allowed
roots do not expose prior checkpoint contents, and its Git tools do not provide
historical log/show/revert operations. The prompt also drops the original
explicit KEEP / IMPROVE / ROLLBACK + PIVOT discipline, repeated-failure
component-level pivot, and preservation of original seed rules.

**Why it matters.** Recording commits for a human is different from giving the
next Evolve invocation enough information and tools to undo a harmful earlier
change. Reconstructing old code from narrative history is not equivalent.

**Repair.** Preserve usable lineage or expose protected, training-only
historical snapshots/diffs plus a constrained revert operation. Restore the
decision and pivot instructions. Record seed invariants explicitly rather
than hoping the current prompt remembers them.

**Acceptance.** After two rounds, the third Evolve invocation can inspect and
undo one earlier logical edit without losing an unrelated improvement. Its
decision and evidence are recorded. A repeated failed local fix can result
in a documented component-level pivot.

This does **not** require blindly introducing automatic rollback: the public
controller's normal loop intentionally makes attribution report-only and lets
Evolve decide. Automatic versus agent-decided rollback needs an explicit
methodology decision.

Evidence: `R/src/ahe_runner/workspace.py:63-99`,
`R/src/ahe_runner/meta/nexau_backend.py:42-45,253-319`,
`R/src/ahe_runner/meta/prompts/evolve.md`;
`A/agents/evolve_agent/evolve_prompt.md:206-227`,
`A/evolve.py:4230-4639`.

### R01 / P1: Candidate continuation can cross the protected-action boundary

**Observed.** The protected middleware removes benchmark calls from ordinary
tool execution so that the adapter can execute them. However, NexAU combines
middleware `force_continue` flags with logical OR. A candidate middleware can
therefore request another model turn even after the protected boundary has
intercepted an action.

In the reproduction, the model emitted `lookup_account`; the candidate
middleware requested continuation. NexAU made **two model calls before the
adapter received the action**. The second request contained a synthetic tool
message, `no tool result (canceled, compacted or failed)`, rather than an
official benchmark result.

**Repair.** Give protected external actions a trusted yield/stop mechanism
that candidate continuation cannot override. A candidate must not advance
past an outstanding official action. Preserve the fixed NexAU loop by using
an appropriate runtime integration point; do not move official action
execution into editable tool code.

**Acceptance.** With adversarial-but-valid `force_continue` middleware,
exactly one model call occurs before the adapter receives the pending action.
The next model call occurs only after the exact official call-ID/result pair
is supplied. Include multiple pending actions and mixed-call rejection cases.

Evidence: `R/src/ahe_runner/runtime.py:386-453`,
`N/archs/main_sub/execution/hooks.py:576-602`,
`N/archs/main_sub/execution/executor.py:858-910`; exercised through real NexAU.

### R02 / P1: Failed completion stops the agent instead of allowing repair

**Observed.** `complete_task()` returns a JSON **string** containing
`{"ok": false, ...}` when requirements are unmet. NexAU wraps non-dictionary
tool results and suppresses stop behavior only for a top-level
`status: "error"`. It consequently treats the failed attempt as a successful
stop-tool invocation.

A premature Explore completion with a missing skill ended after one model
response, leaving a supplied repair response unused. Outer checks then fail;
the stage machinery treats deterministic integrity failures as blocked.

There are related repair gaps: cached candidate validation is not bound to the
final workspace HEAD, and the Evolve validation tool runs generic validation
but not the adapter's validator. Adapter rejection arrives after Evolve has
finished, outside its repair loop.

**Repair.** Return the native error shape required by NexAU and stop only on
successful completion. Bind validation to exact content/HEAD and invalidate it
after mutation. Make the same generic and adapter-specific validation available
inside Evolve, while keeping benchmark evaluators protected.

**Acceptance.** Failed completion returns actionable feedback and the same
agent repairs it. Editing after validation requires revalidation. An
adapter-specific invalid candidate can be corrected before freezing rather
than permanently blocking an otherwise recoverable campaign.

Evidence: [tools.py:409-475](../src/ahe_runner/meta/tools.py#L409-L475),
`R/src/ahe_runner/meta/tools.py:284-323`,
`R/src/ahe_runner/controller.py:575-640`,
`R/src/ahe_runner/state.py`;
`N/archs/main_sub/execution/tool_executor.py:245-292`.

### R03 / P1: Effective model/resource policy is not fully controlled

**Observed.** When campaign reasoning is provider-default, the runtime omits a
reasoning update instead of clearing candidate reasoning. A candidate's
`{"effort": "high"}` survived the override. The recorded client accepted
`max_tokens=104096` and `temperature=1.9` despite campaign values of 4096 and
0.2. The direct Debugger request omits configured temperature.

Disabling OpenAI SDK retries also does not disable NexAU's retries: the actual
runtime retained five LLM attempts, with native broad-exception retry logic.
The documentation's no-nested-retry assertion is therefore too strong.

**Repair.** Specify which settings are fixed, which are evolvable harness
policies, and which are hard campaign ceilings. Normalize settings at every
actual model-call boundary, including children, middleware-induced calls, and
compaction. Explicitly clear reasoning for provider-default and pass the
Debugger's configured settings. Coordinate retry ownership with the native
runtime and count actual attempts.

**Acceptance.** Out-of-policy requests fail before contacting a provider;
provider-default leaves no candidate reasoning override; all roles use their
declared parameters. A controlled failure produces exactly the declared number
of attempts without retrying model-identity errors as transient failures.

Do not mislabel all budget evolution as forbidden AHE behavior: configurable
context management is part of harness engineering. The defect is ambiguity
and failure to enforce the policy the campaign claims to use.

Evidence: `R/src/ahe_runner/runtime.py:483-516`,
`R/src/ahe_runner/meta/model_client.py:57-120`,
`R/src/ahe_runner/meta/nexau_backend.py:479-484`,
`N/archs/main_sub/config/config.py:278-303`,
`N/archs/main_sub/execution/llm_caller.py:338-427`,
`R/docs/ARCHITECTURE.md:493-495`.

### C01 / P1: The authorized adapter source root exposes held-out cases

**Observed.** Source Explore and Evolve can read the adapter class's entire
source file. The bundled adapter places validation/test cases and their
phrase-based scoring data inline in that file. Authorized `read_file` access
therefore exposes entries such as `validation-confirm-actions` and
`test-completion`, even though held-out result directories are excluded.

**Repair.** Declare explicit public/safe adapter documentation and source
roots, not an entire implementation file by inference. Separate hidden task
data and private verifier internals from those roots. Public schemas and public
scoring rules can remain visible where benchmark policy permits.

**Acceptance.** Hidden sentinel task content cannot be obtained through any
authorized meta read/search root, source skill, or prompt context. Training
diagnoses remain detailed without exposing held-out answers.

This is a failure of the runner's added held-out boundary, not a violation of
a held-out protocol that original AHE's training experiment did not have.
Also, file-tool allowlists are not an operating-system sandbox: imported
candidate tools, middleware, and import-time Python execute on the host.
Trusted local operation is a reasonable initial scope; arbitrary untrusted
uploads need a separate isolation design.

Evidence: `R/src/ahe_runner/meta/nexau_backend.py:61-64,254-260,367-381`,
`R/src/ahe_runner/example/adapter.py:36-53`,
`R/docs/ADAPTER_GUIDE.md:103-109`. The authorized-source read was reproduced.

### C02 / P1: Evaluation and evidence acceptance omit common invariants

**Observed.** The controller checks agreement between `evaluate()` and
`verify_evaluation()`, but not all invariants against the actual request.
A V2 adapter returning the same incorrect object twice was accepted for
`C0/train` even though the object said `WRONG-CHECKPOINT/test` and claimed a
nonexistent artifact.

Evidence verification checks the local manifest/detail hashes and some
identities, but does not verify the declared source-evaluation path/hash,
expected trial coverage, metric consistency with the source evaluation, or
a mandatory detail envelope. A nonexistent source evaluation with an all-zero
hash and empty trial detail was accepted.

**Repair.** Let adapters recompute native rewards, but have the runner enforce
request identity, split/checkpoint identity, expected coverage, artifact
existence/hashes, and evidence lineage. Require a small common trial envelope
with native trace/evaluator artifacts referenced underneath it. Distinguish
harness conformance from adapter behavioral conformance.

**Acceptance.** Wrong identities, missing trials, invented source hashes, and
stale metrics fail before Debugger/Evolve sees the bundle. An adapter's verifier
cannot merely bless its own malformed result. The sample verifier should
demonstrate independent recomputation rather than just rereading result JSON.

This is not a request for one universal benchmark-event schema. Native trace
formats can remain native; provenance and coverage are common requirements.

Evidence: `R/src/ahe_runner/controller.py:320-442`,
[evidence.py:38-77](../src/ahe_runner/evidence.py#L38-L77),
`R/src/ahe_runner/example/adapter.py`,
`R/docs/ADAPTER_GUIDE.md:229-281`.

### P01 / P2: Task-specific role customization is only partially exposed

**Observed.** `MetaPromptContext` is a useful starting point. It supplies
guidance to Explore and Debugger, but is not directly included in the Evolve
query. Evolve may learn some of it indirectly through skills and evidence.
There is no project-level per-role prompt supplement, explicit required
research-URL input, or explicit safe source-root declaration. Debugger's
taxonomy remains hardcoded rather than consuming the adapter taxonomy fully.

**Repair.** Keep protected AHE core prompts and add versioned, hashed,
append-only domain supplements for Source, Web, Debugger, and Evolve. Pass
the same resolved metric/task context to every relevant role. Expose safe
source roots and research seeds explicitly. A complete prompt replacement
should be an acknowledged methodology fork, not silently "AHE compliant."

**Acceptance.** A new domain is configured entirely in the operator project,
without editing installed runner files. All four roles receive the intended
domain guidance, and changing that guidance invalidates campaign identity.

Evidence: `R/src/ahe_runner/adapter.py:177-186`,
`R/src/ahe_runner/config.py:102-122`,
`R/src/ahe_runner/meta/nexau_backend.py:877-924`.

### P02 / P2: Some ordinary NexAU harnesses fail the advertised interface

| Reproduced case | Current result | Required repair |
|---|---|---|
| Valid manifest with root config renamed from `agent.yaml` | Conformance accepts it; runtime still loads `agent.yaml` and fails | Honor `manifest.agent_config` everywhere, or explicitly constrain the schema to one filename |
| Registered tool imports a helper under `tools/` | Helper matches the standard component glob but is rejected as not directly runtime-attached | Represent support modules or account for transitive dependencies without requiring dummy tools |
| Training task ID is `"1"` | An unrelated `schema_version: 1` string is rejected as task memorization | Use stable aliases and benchmark-aware checks, not unrestricted substring rejection across all text and metadata |

Evidence: `R/src/ahe_runner/runtime.py:275`,
`R/src/ahe_runner/conformance.py:114-128,455-500`,
`R/src/ahe_runner/harness.py:396-416`.

There is also a scope limitation rather than a reproduced defect:
`AgentInputMessage.content` accepts arbitrary values, but the runtime stringifies
non-string input and protected results. That does not transport typed image or
audio content. Screenshot-driven UI tasks need a richer message contract;
dynamic tool-set changes and interruption/event-stream semantics also need
explicit treatment. Text-based interactive tasks are a more accurate current
scope than "all major harnesses and UI agents."

Evidence: `R/src/ahe_runner/runtime.py:545-588`.

### O01 / P2: Campaign identity does not freeze all behavior-changing inputs

**Observed.** Identity includes config, seed and manifest hashes, package
versions, benchmark interface, split identities, and the adapter class's source
file. It does not pin runner source revision/content, packaged role prompts and
tools, the adapter's dependency closure, or resolved `MetaPromptContext`.
Editing installed prompt code without changing the version can therefore fall
outside the stated immutability guarantee.

The controller also constructs a methods-reference path under `src/docs/`,
which does not exist. The backend falls back to a short packaged runner
description, not the detailed original AHE reference.

**Repair.** Hash or pin the actual protected runtime/prompt/tool bundle and
declared adapter code/data dependencies. Include resolved domain guidance.
Use an explicit, packaged method reference with a recorded identity. Do not
infer that hashing one class file captures everything it imports.

**Acceptance.** Changing any behavior-affecting protected input makes resume
fail with a precise identity mismatch. Reproducing a campaign does not require
guessing which local source edits or prompts were present.

Evidence: [controller.py:788-832](../src/ahe_runner/controller.py#L788-L832),
`R/src/ahe_runner/controller.py:69-78`,
`R/src/ahe_runner/meta/nexau_backend.py:61-74,672-677`,
`R/src/ahe_runner/resources/AHE_METHODS_REFERENCE.md`,
`R/docs/ARCHITECTURE.md:232-243`.

### O02 / P2: A partial selected-harness export can be recorded as complete

**Observed.** `_final_action()` copies the selected harness only if the export
directory does not already exist. It does not verify an existing directory
against the selected checkpoint. A fixture representing an interrupted copy
produced `final_test.json` even though the exported directory had no
`agent.yaml`.

**Repair.** Export into a temporary sibling directory, compare the complete
content map with the selected checkpoint, and atomically publish it. An
existing partial export must be repaired or rejected, never blessed by hashing
whatever happens to be present.

**Acceptance.** A partially populated export cannot become a completed final
stage. A resumed export exactly matches the selected immutable checkpoint.

Evidence: [controller.py:758-786](../src/ahe_runner/controller.py#L758-L786);
deterministic partial-directory reproduction.

### D01 / P1 for onboarding: The documented live switch does not work as stated

**Observed.** The README says to change endpoint/model settings and select
`meta.backend: nexau`. The generated project still has
`meta.explore.web_search_backend: disabled`, while the production backend
requires `ddgs`. Following the backend-only switch fails immediately.

More importantly, `ExampleAdapter.evaluate()` deliberately ignores the runtime
and scores predefined phrases in prompt files. The scripted backend writes
those phrases. This is a useful lifecycle fixture, but switching only the meta
backend does not turn it into a benchmark that executes the evaluated agent.

**Repair.** Separate three guides: offline lifecycle rehearsal, a real
runtime-backed benchmark example, and a research-quality campaign. Document
the complete configuration change and make clear that the example adapter must
also change. Provide at least one tiny runtime-backed task and one protected
interactive-action example.

**Acceptance.** A newcomer follows the documented route without editing
runner internals. Actual evaluated-agent sessions produce benchmark results
and traces; Debugger consumes those traces; an evidence-linked edit is evaluated
again. Selecting C0 because no improvement occurred is a valid outcome.

Evidence: `R/README.md:57-77`,
`R/src/ahe_runner/templates/project/ahe.yaml`,
`R/src/ahe_runner/meta/nexau_backend.py:42-74`,
`R/src/ahe_runner/example/adapter.py`,
`R/src/ahe_runner/meta/scripted.py:130-190`.

## 5. Role prompts and runtime settings: semantic comparison

Prompt length alone is not a fidelity criterion. The important question is
whether the instructions, tools, inputs, and enforced outputs preserve the
original role's job.

| Role/surface | Preserved | Material difference |
|---|---|---|
| Source Explore | Separate role; installed framework and adapter inspection; durable `SKILL.md` loaded by Evolve | Original demanded exact interfaces, templates/code, source-line evidence, and substantial coverage. Runner's topic checklist and minimum skill length do not enforce equivalent deliverables; safe project-specific roots are missing |
| Web Explore | Separate web research role; persistent knowledge artifact | Original specified seeded URLs, roughly 15-20 searches, exact methods/parameters/results, negative findings, and implementable examples. Runner requires fewer sources and broad recommendations, without the same research contract |
| Debugger | One task unit, structured diagnosis, confidence and falsifier fields | Direct completion rather than original navigable agent; paired-rollout and fragility analysis weakened; hardcoded taxonomy and no compaction |
| Evolve | Reads history and knowledge, uses Git, writes a manifest, performs candidate validation | Per-change prediction loop broken; rollback/pivot and seed-preservation instructions reduced; full domain context not directly passed |
| Explore knowledge lifecycle | Both artifacts are mounted and required | Explicitly read-only; paper describes later refinement/removal. Public-code placement makes exact code equivalence ambiguous |
| Nominal iteration/context budgets | Evolve 500 iterations; Explore 200; nominal 200,000-token contexts | Those numbers alone do not establish equal behavior: output budgets, temperature, compaction, role implementation, and transport retries differ |
| Evolve compaction | Tool-result replacement with retained source references | Original config: 220,000-token compaction budget, threshold 0.75, keep 10 tool results. Runner: nominal 200,000, threshold 0.72, keep 12, with adaptive adjustment; emergency overflow/long-output handling is not equivalent |
| Explore compaction | Same general tool-result compaction approach | Original: 200,000, threshold 0.75, keep 15. Runner uses the shared threshold/retention policy rather than preserving the separate configuration |
| Model/output settings | Roles can be assigned exact configured models | Original role YAMLs use 32,000 output tokens, Evolve temperature 0.7, Explore 0.2. The generated runner uses 8,192 output tokens and temperature 0.2 for its meta model, with provider-default reasoning. These are different experiment settings, not invisible equivalents |

Original prompt/config sources:
`A/agents/evolve_agent/{evolve_prompt.md,evolve_agent.yaml}`,
`A/agents/explore_agent/source_agent/{prompt.md,agent.yaml}`,
`A/agents/explore_agent/web_agent/{prompt.md,agent.yaml}`.
Runner sources: `R/src/ahe_runner/meta/prompts/*.md`,
`R/src/ahe_runner/meta/nexau_backend.py:525-658`, and
`R/src/ahe_runner/templates/project/ahe.yaml:15-25`,
`R/docs/PROMPT_FIDELITY.md`.

There is an inconsistency within the paper's model-effort descriptions: main
prose describes high effort broadly, while Appendix A/Table 4 and the explicit
public experiment configuration assign xhigh to Evolve/Explore and high to the
evaluated agent. For exact reproduction, use the explicit configuration and
record the discrepancy; do not collapse both into an invented uniform setting.

`docs/PROMPT_FIDELITY.md` should become an actual original-to-runner matrix with
preserved requirements, intentional substitutions, omissions, and executable
acceptance criteria. Its broad conclusion that these are only
parameterizations, not reductions, is not supported by the current behavior.

## 6. Does it deliver the requested plug-and-play design?

**Architecturally, mostly yes. Operationally, not yet.**

| User-supplied piece | Current support | Remaining work |
|---|---|---|
| Existing harness in NexAU form | Manifest plus normal NexAU configuration and components | P02 layout restrictions; document supported subset rather than universal compatibility |
| Fixed central execution loop | V2 runtime constructs NexAU Agent/Executor | Preserve this approach while fixing R01/R02 |
| Benchmark orchestration above that loop | Typed adapter and persistent sessions | Demonstrate it in a real runnable benchmark, not only isolated runtime fixtures |
| Benchmark-owned tools | Protected structured-tool overlay and adapter execution | Fix continuation boundary; clarify public schema versus private implementation |
| Native rewards and task-level feedback | Adapter-owned evaluation/evidence interfaces | C02 shared envelope, coverage, provenance, and independent verifier requirements |
| Custom Explore/Debugger/Evolve guidance | Partial `MetaPromptContext` support | P01 protected core plus project-level supplements |
| Repeatable evolution and returnable result | Checkpoints, selection, export | A01-A03, O01-O02; avoid promising inevitable improvement |
| UI/multimodal additions above the loop | Text interaction can fit | Typed multimodal content and event/interrupt contracts are not yet implemented |

### Answer to the benchmark-tool question

The correct choice is not simply "prompt" versus "editable tools file."

**The benchmark owns official action names, argument schemas, implementations,
and result semantics. The runtime supplies their public schemas to the model
as structured tools without modifying the harness files.** Prompt text can
explain policy and correct usage, but cannot replace API tool declarations.

For example, the benchmark may own `cancel_order(order_id)`. It controls what
cancellation actually does and whether the action is permitted. The harness
may evolve a planning prompt, a confirmation helper, or a verification policy;
it must not redefine `cancel_order` to return success without cancelling.

The runner's `BenchmarkInterface` / `BenchmarkToolSpec` overlay is the right
architectural decision. Keep it. Fix its scheduling boundary rather than
moving benchmark implementations into the mutable harness or resorting to
prompt-only imitation of tool calls.

If evolving descriptions of official tools is desirable later, introduce an
explicit description-only overlay while protecting names, schemas, dispatch,
and semantics. That is a separate, declared extension; it is not currently
implemented.

### A suitably small standard evidence contract

Do not standardize every event in every benchmark. Standardize the outer
record: evaluation/checkpoint/split identity, stable task alias, expected
trial IDs, outcome and termination state, native metric summary, and trace/
verifier artifact paths with hashes. Keep rich native artifacts underneath it.
Provide read tools that can navigate them and require the Debugger to state
when evidence is insufficient.

This is enough to make the bridge predictable without replacing every
benchmark's native evaluator.

## 7. Documentation corrections needed

The documentation is substantial and useful, especially the ABI, adapter, and
operator guides. The problem is not a lack of prose; it is that several
statements describe intended behavior more strongly than the code warrants.

| Document | Correction |
|---|---|
| `README.md:57-77` | Distinguish the phrase-scoring lifecycle fixture from real evaluated-agent execution; document `ddgs`, enabled explorers, and the real adapter requirement |
| `docs/V2_DESIGN.md:25-26` | Debugger currently does not use the same NexAU Agent/Executor as the other roles |
| `docs/PROMPT_FIDELITY.md` | Replace generic "Yes" coverage with the semantic differences in section 5 |
| `docs/ARCHITECTURE.md:322-340` | Align the stated evidence layout, including `overview.md` and `metrics.json`, with what the schema/example actually require and write |
| `docs/ADAPTER_GUIDE.md:141` | Full domain context is not directly passed to Evolve as stated |
| `docs/ADAPTER_GUIDE.md:229-281` | Separate adapter responsibilities from invariants the runner actually verifies; fix source-hash and coverage claims |
| `docs/ARCHITECTURE.md:232-243` | Narrow immutability claims until prompt/code/dependency provenance is pinned |
| `docs/ARCHITECTURE.md:493-495` | Account for native NexAU retries, not only the OpenAI SDK setting |
| Isolation descriptions | State that candidate tools and import-time Python, not just middleware, execute on the host |
| Export/recovery descriptions | Do not equate directory existence with a complete selected harness |

The minimum additional configuration for the production meta backend includes:

```yaml
meta:
  backend: nexau
  explore:
    source_enabled: true
    web_enabled: true
    web_search_backend: ddgs
```

This is **not** a complete live-campaign recipe: the configured endpoint,
advertised model IDs, credentials, dependencies, and a runtime-backed benchmark
adapter must also be correct. In particular, changing this YAML does not make
the bundled phrase-based evaluator call the evaluated agent.

## 8. Recommended implementation sequence

Treat the findings as a bounded repair program, not an invitation to build a
universal agent platform before running anything.

| Stage | Work | Exit condition |
|---|---|---|
| 1. Restore the distinctive AHE mechanisms | A01 manifest/attribution; A02 navigable Debugger and large-file tools; A03 rollback/pivot surface | A paired-rollout diagnosis leads to a committed, predicted intervention; its next evaluation produces meaningful per-change feedback and can inform rollback |
| 2. Make runtime/benchmark boundaries dependable | R01 protected yield; R02 repair loop; R03 declared model/retry policy; C01/C02 source and evidence boundaries | Candidate code cannot advance past an official action or silently change enforced settings; malformed/foreign evidence is rejected |
| 3. Prove one genuine integration | Real runtime-backed example; P01 domain guidance; the P02 blockers encountered by that example; corrected onboarding | An operator project runs without patching the runner; artifacts show actual evaluated-agent calls and benchmark outcomes |
| 4. Make the result reproducible and returnable | O01 provenance; O02 atomic export; broaden adapter conformance and examples | Resume cannot mix incompatible implementations, and exported content exactly matches the selected checkpoint |

A second, structurally different benchmark should then demonstrate that the
first integration did not merely move benchmark-specific code into the
runner. It does not need to be another large benchmark: a small interactive
task with a protected action is enough to exercise a distinct contract.

Methodology choices should be decided explicitly before implementation:
automatic versus agent-decided rollback, mutable versus immutable derived
Explore knowledge, infrastructure-failure scoring, permitted budget evolution,
and full role-prompt overrides. Ordinary plumbing fixes do not require
reopening those choices.

## 9. Constructive gaps in the product idea

1. **Promise an optimization process, not guaranteed improvement.** Sometimes
   the seed is already good, the task set is too small, or the search fails.
   Return the best verified candidate under the declared rule, including C0.
   The controller already has the right baseline fallback concept.

2. **"Bring any harness" still involves a port.** NexAU is a sensible first
   substrate with broad component coverage, not proof that every streaming,
   multimodal, stateful, or event-driven application fits the current ABI.
   State the supported subset and expand it from actual integrations.

3. **Keep adaptation separate from rewriting the benchmark.** Rich training
   feedback is desirable; changing official action semantics or scoring to
   help a candidate is not. Explicit ownership is more important than putting
   every tool in one convenient directory.

4. **Track cost, latency, and reliability as well as task reward.** A harness
   can increase its score simply by using more tokens, retries, or sub-agents.
   Decide which trade-offs are allowed and record effective resource use.
   Do not implicitly call increased compute a free harness improvement.

5. **Predictions aid learning but do not prove causality.** Two rollouts can
   reveal a useful pass/fail divergence; they cannot establish a small gain
   with high certainty. Multiple edits can interact. Confirm important wins
   and regressions and keep final-test evidence out of optimization decisions.

6. **Separate trusted local use from an upload service.** A local MVP can
   reasonably trust benchmark and harness authors. Supporting arbitrary
   uploaded Python is a different deployment problem requiring isolation,
   resource controls, and protected credentials.

None of these requires abandoning the proposed design. A focused, honestly
scoped NexAU implementation with one convincing real adapter is more valuable
than prematurely supporting every framework or inventing an enormous common
trace language.

## Appendix: reproducible evidence inventory

The supplementary probe script and captured output are retained locally:

```text
~/.copilot/session-state/4710795b-04b8-4bad-ba06-5ff58da62939/files/
  ahe_audit_probes.py
  ahe_audit_probe_results.json
  ahe-paper.txt
```

Script SHA-256:
`e5df38cc52c71708b23d1fbb5f24cca470e9a6bee33123ac2abfa24904f31d43`.
Recorded result SHA-256:
`46666e64abd647077a53f883ecc16fa0d01ba5d9b89b20516dd3903cda0cb8ad`.
These identify the recorded artifacts; temporary directory names differ on
reruns. The script is a local audit artifact, not an installed project command.

To reproduce on this checkout/environment:

```bash
cd ~/ahe-runner
.venv/bin/python \
  ~/.copilot/session-state/4710795b-04b8-4bad-ba06-5ff58da62939/files/ahe_audit_probes.py
```

It uses generated temporary projects and fake provider clients. It makes no
live model requests and cleans up its temporary project directories. The
expected missing-entrypoint scenario causes NexAU to print a caught config
error; the final JSON records the actual observations.

| Fixture/action | Observed result | Finding |
|---|---|---|
| Freeze-time validation of `changes: [{}]` | Accepted | A01 |
| Prompt-shaped string predictions with an observed task improvement | Empty predicted-task set; aggregate `partially_effective`; no per-change verdicts | A01 |
| Read one line/search a known first-line marker in a 160,008-byte file | Read refused; search empty with no skipped-file notice | A02 |
| Real NexAU with protected `lookup_account` and candidate `force_continue` | Two calls before adapter execution; synthetic missing-result message | R01 |
| Real NexAU with premature completion and a subsequent repair response | Stops after the first call; repair response unused | R02 |
| Campaign provider-default reasoning over candidate `effort: high` | Candidate reasoning remains in effective config | R03 |
| Recorded client receives altered token/temperature parameters | Accepts 104096/1.9 against configured 4096/0.2 | R03 |
| Read the explicitly authorized adapter source root | Inline held-out entries accessible | C01 |
| Adapter evaluate/verify agree on wrong checkpoint/split and missing artifact | Controller accepts the object for requested C0/train | C02 |
| Evidence with nonexistent source evaluation, false hash, and empty trial detail | Accepted | C02 |
| Rename valid root config and update manifest | Conformance accepts; runtime fails looking for `agent.yaml` | P02 |
| Add a helper imported by a registered tool | Rejected as unattached under standard tool glob | P02 |
| Use training ID `"1"` and unrelated `schema_version: 1` text | Rejected as memorization | P02 |
| Precreate a partial final export directory | Final record written despite missing agent config | O02 |
| Change only generated `meta.backend` to `nexau` | Rejected because web backend is still disabled | D01 |

Additional source-confirmed observations, including lost rollback access,
missing role customization, incomplete provenance, and native five-attempt
retry configuration, are labeled and sourced in their findings. No claim of
an actual production outage or measured benchmark regression is inferred
from these deterministic fixtures.
