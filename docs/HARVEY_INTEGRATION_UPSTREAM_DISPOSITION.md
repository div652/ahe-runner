# Harvey Integration Upstream Disposition

This document records the upstream review of AHE-001 through AHE-043 from
`HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`. The findings were checked
against the generic runner rather than accepted as Harvey-specific
requirements.

The resulting changes intentionally contain no Harvey task data, prompts,
metrics, adapter code, campaign artifacts, or benchmark-specific policy. They
form the AHE Runner 0.8.0 hardening boundary.

## Summary

| Item | Assessment | Upstream disposition |
|---|---|---|
| AHE-001 | Correctness gap | Fixed: evaluated-model catalog, positive, negative, and observable-identity preflight is preserved per evaluation stage attempt. |
| AHE-002 | Conformance gap | Fixed: every regular asset in a registered skill directory is an active, hashed skill component. The later Harvey `workspace_seed/` workaround (sandbox-only scripts copied by the adapter) needs no runner change: files outside the component globs are already part of the harness hash, and copying them into a benchmark-owned sandbox is adapter policy. |
| AHE-003 | Import-integrity risk | Fixed at validation boundary: reject candidate top-level names that shadow standard-library, loaded, or importable host modules. Separate-process grading remains the stronger future boundary. |
| AHE-004 | Packaging/onboarding gap | Documented: adapters must not resolve a second runner/NexAU stack; NexAU 0.3.9 remains a direct Git dependency and `ag-ui-protocol` is constrained below 1.0. |
| AHE-005 | Capability-routing gap | Fixed: object-valued session resources use per-session NexAU `GlobalStorage`, shared only with that root agent's sub-agents. |
| AHE-006 | Cross-session state risk | Fixed with AHE-005: each runtime session creates a distinct Agent and resource store. This is isolation of accidental state routing, not a hostile-Python sandbox. |
| AHE-007 | Static-import bug | Fixed: `from package import helper` now resolves `package.helper` instead of splitting the imported name into characters. |
| AHE-008 | Workspace contamination | Fixed: every meta-agent receives a private runtime sandbox/work directory. |
| AHE-009 | Prompt mutation/failure | Fixed: resolved core prompts and role supplements are passed to NexAU as literal strings; the prompt file remains as immutable attempt evidence. |
| AHE-010 | Oversized file-search output | Fixed through the AHE-016 tool/middleware replacement: guarded native file tools use long-output handling and private full-output artifacts. |
| AHE-011 | Recovery limitation | Fixed with explicit `ahe retry-stage`. Prior attempts and errors remain immutable and a reason is required. Exhausted stages reopen only under AHE-026's infrastructure-only rule. Identity-changing runner-patch amendments were rejected. |
| AHE-012 | Performance limitation | Deferred: cross-checkpoint concurrency requires one campaign-wide scheduler so adapter, judge, evaluated-agent, and meta-agent limits are not multiplied accidentally. |
| AHE-013 | Directory-listing overflow | Fixed through AHE-016: guarded listing/glob tools participate in bounded output handling. |
| AHE-014 | Evidence/citation mismatch | Fixed through AHE-016: full tool outputs are durable, while Debugger citations are captured from the post-truncation text actually shown to the model. |
| AHE-015 | Endpoint pressure | Fixed for runner clients with one runner-wide semaphore per endpoint; adapters can join the same queue through `ahe_runner.model_requests.model_request_slot`, and runner-spawned evaluated-trial children inherit it. |
| AHE-016 | Meta-agent fidelity gap | Fixed: original-AHE-style guarded tools, per-role settings, optional Docker shell, long-output handling, context compaction, Evolve skill, and Debugger failure policy are restored generically. |
| AHE-017 | Attribution bug | Fixed: Debugger task keys, benchmark task IDs, and evaluation task keys resolve to one stable attribution key, with unresolved references reported. |
| AHE-018 | Metric-guidance drift | Fixed as an explicit process and warning contract: the adapter guide defines the full metric-change checklist and local checks warn on configured deprecated metric terms in role supplements. Arbitrary prose is not claimed to be semantically verified. |
| AHE-019 | Prompt-assembly bug | Fixed: empty memory files add nothing, nonempty memory is separated from the rendered system prompt, and the generated memory starts empty instead of containing instructions meant for Evolve. |
| AHE-020 | Benchmark-parity limitation | Fixed as a protected opt-in: strict schemas remain the default, while a manifest may allow omission of `additionalProperties`; explicit open schemas and `**kwargs` bindings remain invalid. |
| AHE-021 | Transport-fidelity gap | Accepted with an explicit boundary: the standard API preflight cannot prove long-prompt retention or reasoning-effort application. Recorded requests now include deterministic size accounting, and operators must preserve independent near-limit and effort probes. The external GH Copilot Server patch is not part of this repository. |
| AHE-022 | Retry-bias and recovery gap | Fixed as a bounded opt-in: timeout-class hangs may resend the identical call, every attempt is immutable, unrelated errors are never resent, preflights follow the same policy, and campaign status reports resend counts. The default remains zero retries. |
| AHE-023 | Context-accounting and classification bug | Fixed generically: provider context rejections are excluded from infrastructure retry classification, adapters receive a public classifier for benchmark-native graded outcomes, and manifests can protect the evaluated agent tree's counter and limit. Harvey's tokenizer and scoring text remain local. |
| AHE-024 | Cross-model tokenizer assumption | Fixed as explicit protected policy rather than a universal tokenizer: manifests and meta config state the counter import, counting unit, limit, and optional provider maximum. Operators must recalibrate when the model or transport changes. |
| AHE-025 | Trial-execution contention and observability gap | Accepted with an ownership boundary: adapters still choose their worker model, while the runner provides an opt-in spawned evaluated-trial seam that preserves preflight and endpoint policy. Every evaluated session now records protected per-tool wall time. Harvey's `trial_processes` setting remains local. |
| AHE-026 | Recovery and operations gap | Fixed in `ahe retry-stage` rather than a second command: a blocked, retryable stage whose every attempt failed on infrastructure or was abandoned receives `recovery.maximum_stage_attempts` more attempts, recorded in `stage.json` as `attempt_allowance` and a `manual_retries` event. Stages with any agent, candidate, or benchmark failure stay bounded. `AHE_PRE_STAGE_COMMAND` runs an operator health check before each new attempt; its failure consumes no attempt. Harvey's `--allow-runner-code-change` flag and model-host memory guard were not adopted. |
| AHE-027 | Campaign-continuation gap | Fixed: `ahe extend-rounds` (dry run unless `--apply`) accepts only a larger `campaign.rounds`, recorded in a hash-chained `campaign_amendments.json`. `--reopen-closed-evolution` hash-verifies and moves the closing stages and `selection/`/`final/` outputs to `superseded/closure-NN/`, then resume reuses every other stage. The runner-code-change override was not adopted. |
| AHE-028 | Provider and wire-API gap | Fixed generically: `api_mode: responses` per transport or per model (system prompt as `instructions`, reasoning items resent between turns), `token_command` authentication refreshed in-process (covers Entra ID through the Azure CLI and any other token issuer), and bounded `transient_retries` using the AHE-031 schedule. Harvey's `provider: azure_entra` setting was not adopted; `token_command` subsumes it. |
| AHE-029 | Serialization bug | Fixed in the recorded client: resent Responses tool-call items get API field names and drop nulls. A test runs a real NexAU multi-turn tool call through the Responses API. The fix belongs in NexAU as well. |
| AHE-030 | Undefined outcome | Fixed as campaign policy: `evaluation.provider_rejection_policy` (`grade` or `rerun`) is identity, reaches adapters as `EvaluationRequest.provider_rejection_policy`, and the public `provider_rejection_message()` classifies HTTP 400/422 refusals narrowly. The adapter applies the policy and reports the count; the runner cannot verify adapter compliance. |
| AHE-031 | Retry-flood risk | Fixed: jittered exponential back-off is the only resend schedule; a 403 with a configured block marker opens a host-wide circuit breaker for `provider_block_seconds`, recorded calls and preflights stop without sending, and `ahe supervise` stops instead of relaunching. The TRAPI workload header became a generic `client_id_header`. An endpoint-wide retry budget was deferred. |
| AHE-032 | Rate-limit pacing gap | Fixed: host-wide per-model token admission control with multiplicative decrease and gradual recovery (`token_budget_*`), shared by recorded calls and preflights. Header-based pacing and TRAPI-specific gateway pauses were not adopted, because the headers proved unreliable. Status reports transient and 429 counts; achieved tokens per minute is deferred. |
| AHE-033 | Preflight brittleness | Fixed: the negative control accepts the model name, a structured model error code, a built-in gateway phrase, or `transport.model_rejection_phrases`. Recorded wordings from two gateways are offline test fixtures. |
| AHE-034 | Per-role transport gap | Partly fixed: per-model `api_mode` and `temperature: null` (no temperature sent). Fully separate per-role endpoints and authentication, and a validated model capability profile, are deferred. |
| AHE-035 | Route comparability | Fixed: every evaluation stage, `selection.json`, `final_test.json`, and `ahe evaluate` report record a route fingerprint; `ahe compare` warns when routes differ. Behavioural probes of system-prompt placement remain operator evidence. |
| AHE-036 | Coarse-metric attribution | No runner change needed: `compare()` and `selection_key()` are adapter-owned and already accept ordered metrics. The adapter guide now prescribes a secondary-metric tie-break and recording the deciding metric. |
| AHE-037 | Evidence gap for never-passing tasks | Deferred: four adapter interface additions for 44–53% more agent runs per round, which produced a passing run on only 1–4 tasks per round with no measured effect. Pointing the Debugger at earlier passing trials of the same task is the cheaper first step. |
| AHE-038 | Unattended-operation gap | Fixed with `ahe supervise`: fresh-process resumes with back-off, progress-aware failure counting, and stops on blocked stages, configuration or integrity errors, and provider blocks. Unit-level retries inside an attempt are adapter guidance, because adapters own units. |
| AHE-039 | Fixed-harness evaluation gap | Fixed with `ahe evaluate` (checkpoint or harness, split or task list, trials, seed) and `ahe compare`. Appending trials to an existing evaluation and splitting one evaluation across endpoints are deferred. |
| AHE-040 | Selection noise | Fixed for reporting: paired task-bootstrap intervals, minimum detectable differences, and checkpoints indistinguishable from the selected one in `selection.json` and `final_test.json`. Noise-aware selection rules and validation sizing are deferred, because they change the selection method. |
| AHE-041 | Evolution plateau | Deferred: a plateau rule in the core Evolve prompt, parent-checkpoint choice, and branching search change the AHE method and its prompt fidelity, on evidence from one campaign. The new uncertainty reports give operators the noise band. |
| AHE-042 | Identity granularity | Fixed: execution settings are excluded from campaign identity, evaluation stage inputs, and the evaluation request hash; changes are recorded automatically as `execution_settings` amendments. Later result-affecting fields are omitted at their default, enforced by a pinned identity test. Mid-campaign guidance amendments are deferred. |
| AHE-043 | Small operator hazards | Fixed: a controller built inside a spawned trial process that re-ran an unguarded operator script fails with a clear message, and child exit errors name the cause. Deleting read-only throwaway campaigns is documented rather than given a destructive command. |

## Important design decisions

### Candidate imports

The runner still loads candidate Python in the host process where NexAU
requires it. The new validation guard closes accidental and straightforward
module replacement, including names such as `json.py` or an installed package
root. It does not turn host-imported middleware into an OS sandbox. A future
stronger design would load candidate code and grading code in separate
processes with a narrow serialization boundary.

### Session resources

`AgentSessionRequest.session_resources` is for object capabilities that are
intentionally available to evaluated harness tools or sub-agents. It avoids
putting arbitrary Python objects into NexAU's string template context and
avoids process-global cross-trial routing. Adapters must never put hidden
answers, verifier state, credentials, or protected benchmark action
implementations into this store.

### Meta-agent tools

The final implementation uses the original NexAU file tools behind runner path
guards instead of retaining the interim Harvey-specific search/list wrappers.
Read and write roots are explicit. Long outputs are saved in private attempt
artifacts and replaced with bounded model-visible text. Shell access remains
disabled by default and can be enabled only through an explicit Docker
configuration.

### Recovery

`retry-stage` changes only the stage's execution authorization:

```bash
ahe retry-stage --config ahe.yaml \
  --stage round.2.evolve \
  --reason "Corrected the adapter-side parser"
ahe resume --config ahe.yaml
```

It does not delete an attempt, change semantic inputs, rewrite campaign
identity, or bypass `maximum_stage_attempts`. This keeps recovery useful
without weakening checkpoint reproducibility.

### Concurrency

`transport.max_concurrent_requests` limits recorded model calls across all
runner clients for one endpoint. Benchmark-owned clients are outside the
runner's interception boundary, so they must explicitly wrap calls with:

```python
from ahe_runner.model_requests import model_request_slot

with model_request_slot(request.transport):
    ...
```

Cross-stage scheduling remains sequential until the runner can allocate one
shared campaign budget instead of multiplying each adapter evaluation's local
concurrency.

### Evaluated-trial isolation

The runner cannot universally force one process per trial because the adapter
owns benchmark-native unit scheduling, grading, corpus checks, and recovery.
Some adapters already use subprocesses, containers, remote workers, or
I/O-bound async execution. The generic repair is therefore opt-in:
`run_evaluated_trial_in_child()` reconstructs the evaluated runtime in a fresh
spawned process, reuses the parent preflight, preserves protected project
imports and model/context policy, and shares the endpoint request queue.

Grading and `EvaluationStore` completion remain in the parent. Abnormal child
termination remains infrastructure and must end the evaluation attempt so a
fresh CLI process can restore all resource budgets. The runner does not import
Harvey's adapter setting, sandbox policy, document corpus, or worker count.

Every evaluated runtime independently adds protected timing around root and
sub-agent harness tools. `tool_timings.json` and
`AgentOutput.tool_timings` make CPU/tool contention visible without inferring
it from gaps between model calls. Benchmark-owned protected actions still run
in the adapter and retain adapter-owned timing.

### Infrastructure-exhausted stages and the pre-stage hook

Harvey's local fix added a separate `retry-blocked-stage` command. Upstream
folds the rule into the existing `retry-stage`, so operators have one recovery
command. While a blocked stage has attempts left, the command authorizes one
more, as before. Once a stage has used every attempt, it is reopened only when
it is retryable and every attempt ended as `infrastructure_failed` or was
abandoned by a killed process. No attempt produced a result, so the reopening
cannot select among outcomes. A stage with any genuine failure stays bounded
by its original budget, preserving AHE-011's guard against retrying until a
stage passes.

For stage records written before `retryable` was stored, a stage counts as
retryable only if an earlier infrastructure failure was followed by an
automatic retry rather than an operator retry.

The reopen does not change campaign identity, so it is recorded on the stage
(`attempt_allowance` and the `manual_retries` event) rather than in
`campaign_amendments.json`.

`AHE_PRE_STAGE_COMMAND` is an environment variable rather than a
configuration field. It is operational, like credentials, and must not change
campaign identity. The runner records it on each attempt it preceded.

### Continuing a campaign

`campaign.rounds` is part of campaign identity, and only `evolution.close`
records it as a stage input. `extend-rounds` therefore accepts exactly two
identity differences: the configuration file hash and `campaign.rounds`. Any
runner-package change is refused. This keeps the AHE-011 decision against
identity-changing runner patches inside one campaign. Harvey's
`--allow-runner-code-change` override was deliberately not adopted.

Reopening a closed campaign archives rather than deletes the old closure.
Meta-agents never mount validation, test, `selection/`, or `final/`, so the
superseded results cannot influence later evolution. Because the operator may
decide to continue after reading the test result, the Operator Guide requires
a report to give both closures.

### Providers, rate limits, and blocks

The Harvey branches added TRAPI- and CAO-specific settings. Upstream keeps the
mechanisms and drops the provider names. Authentication is a token command,
not a provider enum. The workload header is a configurable header name.
Provider blocks are recognised by configurable 403 markers. Endpoint state
that must be shared by processes (blocks and the token window) lives in a
per-user state directory, locked with `flock`. Recorded calls and preflight
probes use one send policy, so a preflight cannot retry around a block or
the window. Adapter-owned judges are expected to use the same recorded
client.

### Identity and execution

Campaign identity used to be the entire configuration, so changing a retry
count or a concurrency limit forced a new campaign. The identity now covers
result-affecting settings only. Execution settings can change between
resumes, and every change is recorded. The evaluation request hash excludes
them too, so an in-progress evaluation resumes under new limits.
`LATER_RESULT_DEFAULTS` is the rule for future fields; a pinned test fails when
a field is added without being classified.

### Uncertainty without a new selection rule

AHE-040's evidence shows that small validation sets cannot separate close
checkpoints. Upstream reports the uncertainty: paired task-bootstrap
intervals over per-task scores, deterministic for the same summaries, plus
the list of checkpoints indistinguishable from the selected one. The
selection rule itself is unchanged, so earlier campaigns keep their meaning.
Noise-aware selection is left as an explicit future method choice.

### Metric changes

Metric semantics cannot be proven from unrestricted natural-language
supplements. The upstream contract therefore combines:

1. a required adapter/documentation checklist;
2. campaign identity changes when metric inputs change;
3. optional literal stale-term warnings through
   `meta.deprecated_metric_terms`.

This catches known obsolete vocabulary while avoiding false claims of semantic
validation.

### Long-term memory

NexAU concatenates `system_prompt_suffix` directly. The runner therefore adds
the separator itself and skips empty memory files. A generated project keeps
`LongTermMEMORY.md` empty until Evolve has an evidence-supported reusable
lesson; instructions about the component belong in Evolve's protected
guidance, not in evaluated-agent prompt content.

### Tool-schema parity

`additionalProperties: false` remains the safe default because it tells the
model that undeclared arguments are invalid. Some benchmark-native schemas
omit the keyword, and exact harness comparisons must preserve that observable
schema. `tool_schemas.allow_omitted_additional_properties` permits omission
only, is frozen outside the evolvable harness, and never permits an explicit
open schema or a catch-all Python binding.

### Transport fidelity and hung calls

Catalog membership, a small positive completion, a negative control, and the
observable response label are API-visible checks. They cannot prove that an
intermediary retained the oldest messages in a near-limit prompt or applied a
reasoning-effort setting. The runner now states that limitation in every
preflight artifact and records request bytes plus message/tool character
counts for later audit. A live campaign still requires independent
model-specific behavioral probes retained as operator evidence.

`transport.hung_call_retries` is strict-default-off and capped. It retries only
when a request timeout fired before any response, reacquires the shared
endpoint slot for each attempt, and records every failure before resending the
identical payload. A timeout may still fail after all configured attempts;
overload, model mismatch, policy errors, and context rejection never enter this
loop.

### Context accounting

NexAU's fallback character estimate is not presented as a model tokenizer.
`context_accounting` in the protected harness manifest freezes the evaluated
agent tree's `max_context_tokens`, optional token-counter import,
human-readable unit, and optional provider input maximum. Equivalent protected
settings exist for meta agents. Counter code belongs outside the evolvable
harness.

Known provider context rejections are not infrastructure failures even when a
proxy reports them as HTTP 500. The generic helper identifies the rejection;
the benchmark adapter remains responsible for its native final text,
termination reason, trace policy, and grade.

## Generic regression coverage

The 0.8.0 hardening changes add or extend coverage for:

- evaluated-model preflight caching, proofs, and failures;
- per-session resources and isolation;
- shared endpoint request slots, including adapter participation;
- skill-directory assets and `from package import helper`;
- candidate top-level module shadowing;
- literal meta prompts containing Jinja-like text;
- private meta-agent sandboxes, guarded tools, long outputs, and compaction;
- explicit blocked-stage retry and exhausted-attempt rejection;
- task-reference attribution aliases;
- deprecated metric-term warnings;
- readable evidence artifacts and imported branch campaign behavior;
- separator-safe, empty-aware long-term-memory attachment;
- strict-by-default benchmark tool-schema parity mode;
- transport-preflight scope disclosure and deterministic request-size
  accounting;
- bounded immutable hung-call resends and aggregate status counts;
- provider context-rejection classification;
- protected evaluated/meta context-counter units and limits.
- spawned evaluated-trial runtime reconstruction and preflight reuse;
- endpoint-budget sharing across runner-spawned processes;
- protected root/sub-agent tool wall-time evidence;
- infrastructure-only reopening of exhausted stages, including legacy records
  and refusals for mixed or non-retryable stages;
- pre-stage commands that run before attempts and consume none on failure;
- hash-chained round extensions, tamper detection, and refusal of any other
  identity change, including runner code;
- reopening a closed campaign with byte-for-byte reuse of earlier stages and
  archived closures;
- Responses API calls, including a real NexAU multi-turn tool call, resent
  tool-call serialization, and wire-API enforcement;
- token-command authentication, client ID headers, jittered transient
  resends, host-wide provider blocks, and token admission control;
- negative-control recognition of gateway codes and phrases;
- identity and execution separation, recorded execution amendments, and the
  pinned identity field set;
- `ahe supervise`, `ahe evaluate`, `ahe compare`, uncertainty reports, the
  re-entered-script guard, and provider-rejection classification.

Validation results are recorded separately after the implementation commit so
the tested source identity remains explicit.
