# AHE Compliance Follow-Up Audit

> **Repair closure:** Implementation commit `fe9c03e` closes the two retained
> F05/F06 boundary cases and the release-version mismatch described in the
> [Release 0.3.0 closure check](#release-030-closure-check). Focused
> regressions, the 63-test suite, static checks, and a clean-wheel campaign
> passed. See
> [`F05_F06_VERSION_REPAIR.md`](F05_F06_VERSION_REPAIR.md). The original
> assessment below still refers to `5380f65`, and the closure check preserves
> the pre-fix reproductions.

**Audited implementation:** `5380f65058a970e8e8ecfca00bf1f0a7045becea`.

**Verdict:** The major architectural repairs are real. The runner now follows
the AHE evolution procedure substantially better, and the runtime-backed
evaluated-agent integration genuinely works. However, the statement that all accepted findings
are fully fixed is premature: several repaired boundaries still miss supported
execution paths. There are also narrower compatibility and long-context
limitations.

This is a follow-up assessment, not an implementation patch. The original
[`AHE_COMPLIANCE_AUDIT.md`](AHE_COMPLIANCE_AUDIT.md) remains a historical audit
of `a7704c4`. The repair summary and validation record describe the author's
earlier checks; this report adds independent source review and reproductions.
The companion
[`AHE_COMPLIANCE_REAUDIT_SUMMARY.md`](AHE_COMPLIANCE_REAUDIT_SUMMARY.md)
explains the conclusions in plainer language.

## 1. Scope, authorities, and verification

The comparison separates the paper's method, the public implementation,
deliberate product adaptations, and violations of this runner's own contracts.
Later Tau2-specific changes in `AHE-new` were not used as the original AHE
baseline.

| Item | Pinned reference |
|---|---|
| Original AHE source | `AHE-new` at `8b2a55d97590363fe50c3cc6b5e833b020a4bb4c` |
| Repaired runner | `5380f65058a970e8e8ecfca00bf1f0a7045becea`, initially clean `main` |
| Harness framework | NexAU `0.3.9`, installed source commit `35ee1861546db3cb280a6e17e38a74060d7c96c3` |
| Runtime | Python `3.14.7`, OpenAI Python `3.28.0`, Pydantic `2.14.0` |
| Benchmark exercised | Bundled `RuntimeExampleAdapter`, with locally simulated model responses |

The complete existing suite passed: **43 tests in 63.96 seconds**. Additional
probes exercised actual NexAU execution and actual OpenAI SDK serialization,
using `httpx.MockTransport` or deterministic client fixtures rather than a live
provider. No live model-backed campaign was run and no real credentials were
used in credential probes.

The strongest positive integration check joined components that the existing
runtime-example tests exercise separately:

```text
RuntimeExampleAdapter
  -> real EvaluatedAgentRuntime
  -> real NexAU Agent/Executor
  -> real recorded OpenAI client and SDK
  -> local HTTP response fixture
  -> adapter-owned protected action and exact call-ID result
  -> verified evaluation and schema-V2 evidence
```

That check completed two training tasks with two trials each, using six model
request serializations. Both protected-action trials succeeded. One direct
trial was deliberately made incorrect to supply a real mixed-outcome fixture;
the independently verified score was `0.75`. This is evidence that the
integration works, not a model-quality or improvement result.

## 2. What is genuinely fixed

| Previous concern | Follow-up conclusion |
|---|---|
| Per-change prediction matching | Restored. `ChangeManifest` is shared by production and consumption; each edit gets observed/missing fixes, observed risks, and a verdict. |
| Manifest completeness | Improved beyond the public heuristic: typed records and exact final changed-path coverage are enforced. |
| Debugger navigation | Implemented as a real bounded NexAU agent with read/search/list tools, structured output, and task-local persistence. Its grounding gate still has F05. |
| Practical rollback/pivot | Implemented through checkpoint log/diff and selective path restoration, with explicit KEEP / IMPROVE / ROLLBACK + PIVOT instructions. |
| Failed completion and validation | Error-shaped stop-tool responses and validation of the final committed HEAD address the earlier premature-completion problem. Adapter validation is available inside Evolve. |
| Evaluation identity and artifacts | Substantially repaired: request, split, checkpoint, task/trial coverage, seeds, and artifact ownership/hashes are checked independently. |
| Evidence lineage | Source evaluation identity, aggregate metrics, task coverage, and trial artifact links are checked. This does not yet bind trial semantic fields; see F04. |
| Adapter source exposure | Automatic exposure of the entire adapter implementation is removed. Public roots are explicitly declared. |
| Domain customization | All roles receive context, append-only supplements are available, and Web research seed URLs are explicit. |
| Campaign provenance | Runner resources, resolved prompts/context, declared adapter dependencies, public roots, and supplements participate in identity and resume invalidation. |
| Harness compatibility | Manifest-selected root configuration, short/numeric task-ID handling, and eager transitive helper imports are improved. Lazy imports still fail; see F06. |
| Final publication | Existing exports must match the selected checkpoint; new exports are verified and atomically published. |
| Onboarding | Offline rehearsal and real evaluated-agent execution are now clearly separated, with a working runtime-backed adapter example. |

Relevant implementation entry points are
[`change_manifest.py`](../src/ahe_runner/change_manifest.py),
[`history.py:73-157`](../src/ahe_runner/history.py#L73-L157),
[`meta/tools.py:479-740`](../src/ahe_runner/meta/tools.py#L479-L740),
[`evaluation.py`](../src/ahe_runner/evaluation.py), and
[`controller.py:779-906`](../src/ahe_runner/controller.py#L779-L906).

The outer loop still has the right shape: evaluate completed training
experience, diagnose it, evolve one shared harness, validate/freeze it,
evaluate the new checkpoint, and feed its attribution into the next round.
The final produced checkpoint is evaluated. Held-out selection and final
testing remain outside the meta-agents' authorized evidence roots.

There is no reason to rewrite this architecture to address the findings below.

## 3. Remaining findings at a glance

P1 means address before relying on the affected path in an unattended live
campaign. P2 means a real correctness, compatibility, or boundedness gap, but
not evidence that every ordinary run fails.

| ID | Priority | Finding | Relation to the repair |
|---|---|---|---|
| F01 | P1 | Candidate settings can override the final serialized model request | Request-policy repair remains incomplete |
| F02 | P1 | Supported legacy hooks can still continue before an official action is executed | Protected-action handoff repair remains incomplete |
| F03 | P1 when real keys are configured | Resolved API keys are written into Debugger files readable by Evolve | Newly exposed consequence of the navigable Debugger |
| F04 | P2 | Trial outcome, score, and termination can contradict verified native results | Lineage is fixed; semantic binding remains incomplete |
| F05 | P2 | Debugger submission accepts unread citations and empty-read “coverage” | Navigation is fixed; actual-read enforcement is incomplete |
| F06 | P2 | Ordinary function-local helper imports fail conformance despite working in NexAU | Helper-module compatibility repair is partial |
| F07 | P2, long-write workloads | Compaction does not bound histories dominated by old file-write arguments | Additional limitation, not a uniquely demonstrated AHE deviation |

### F01. Validate the effective request, not only pre-merge keyword arguments

**Sources:** [`meta/model_client.py:56-143`](../src/ahe_runner/meta/model_client.py#L56-L143)
and [`runtime.py:548-590`](../src/ahe_runner/runtime.py#L548-L590).

The facade checks top-level `model`, `max_tokens`, `temperature`, `stream`,
and the custom `reasoning` object. It leaves other `extra_body` keys intact.
The OpenAI SDK merges those keys into the outgoing JSON after these checks.
The standard `reasoning_effort` parameter is also neither cleared nor checked.

This is reachable through an ordinary candidate `agent.yaml`, without a custom
network client or arbitrary host access:

```yaml
llm_config:
  extra_body:
    max_tokens: 104096
    temperature: 1.9
  reasoning_effort: high
```

With campaign settings of `4096` output tokens, temperature `0.2`, and
provider-default reasoning, that candidate passed conformance and ran through
the actual evaluated NexAU runtime. The serialized request contained
`104096`, `1.9`, and `reasoning_effort: high`. The fixture completion was
accepted.

A separate `extra_body.model` probe sent an alternate model ID. The response
label check then rejected the returned alternate label, but only **after the
request had been sent**. This does not demonstrate acceptance of a mismatched
response; it demonstrates that the promised pre-provider rejection is absent.

**Why it matters:** fixed experimental settings are an AHE invariant. An
apparent harness improvement must not secretly be a larger generation budget
or different reasoning/sampling setting. Depending on the provider, the
unexpected request can also increase cost or fail validation.

**Repair direction:** reject collisions with protected fields in `extra_body`,
normalize all supported reasoning representations, and enforce/log the
effective request. Apply the same policy at every actual model-calling path.
Do not assume the proxy ignores an unsupported alias.

**Acceptance:** actual SDK/HTTP-body tests for conflicting model, token,
temperature, streaming, and reasoning aliases; rejected cases must send zero
requests. Retain positive cases for allowed provider-specific parameters.

### F02. Legacy after-model hooks still cross the official-action boundary

**Sources:** [`runtime.py:303-311`](../src/ahe_runner/runtime.py#L303-L311),
[`runtime.py:468-503`](../src/ahe_runner/runtime.py#L468-L503), and
[`conformance.py:410-502`](../src/ahe_runner/conformance.py#L410-L502).

The repair wraps entries in `config.middlewares`. It does not wrap the supported
legacy `after_model_hooks` list. NexAU appends legacy hook middleware after the
configured middleware and runs after-model hooks in reverse order. A legacy
hook can therefore set `force_continue` before protected capture occurs.
NexAU's aggregate continuation flag is not cleared by the later capture.

The reproduction used a conforming callable legacy hook that requested
continuation when it saw `lookup_code`. Actual NexAU made **two model calls
before returning the official action to the adapter**. The second request
already contained:

```json
{
  "role": "tool",
  "name": "lookup_code",
  "tool_call_id": "legacy-action",
  "content": "no tool result (canceled, compacted or failed)"
}
```

No adapter-owned lookup had happened at that point.

**Why it matters:** the model is shown a synthetic result instead of waiting
for the real environment. This is the same protocol failure the original
repair intended to eliminate, reached through another accepted NexAU surface.

**Repair direction:** account for the complete final middleware chain,
including legacy hooks. Normalize/wrap those hooks with preserved ordering, or
explicitly reject unsupported configurations until they are safely handled.
Simply returning `force_continue=False` from a later hook is not sufficient
for NexAU's accumulated flag.

**Acceptance:** parameterize the real runtime test over modern middleware and
legacy hooks. Before adapter execution there must be exactly one model request
and no fabricated tool result; after a real result, the same session must
resume with the original call ID.

**Related design restriction:** the modern wrapper skips candidate
`after_model` processing altogether on captured official-action turns. That
also skips legitimate candidate confirmation/output-repair policies at that
seam. This is explicit in the code, not an additional reproduced handoff bug.
Document that limitation, or deliberately provide a safe postprocessing seam;
do not describe this path as preserving every native middleware behavior.

### F03. Debugger runtime configuration exposes the resolved provider key

**Sources:** [`meta/nexau_backend.py:626-675`](../src/ahe_runner/meta/nexau_backend.py#L626-L675)
and the Evolve read-root construction at
[`meta/nexau_backend.py:283-290`](../src/ahe_runner/meta/nexau_backend.py#L283-L290).

`_run_agent()` writes the resolved `config.api_key` into `runtime/agent.yaml`.
Evolve receives the complete Debugger output directory as an authorized read
root, including each task's runtime configuration.

An actual Debugger run with a synthetic environment-variable key produced that
file. The normal bounded Evolve `read_file` tool could read the synthetic key
from `debugger:tasks/<task>/runtime/agent.yaml`. No path escape was required.

**Why it matters:** using `api_key_env` does not keep the resolved value out of
model-readable artifacts. With a real provider credential, ordinary source
inspection can put it into a model request or subsequent generated content.
The default non-secret local placeholder is harmless; this audit does not
claim that a real credential was leaked or compromised.

**Repair direction:** persist only redacted/public configuration and inject
the resolved key in memory after loading, or keep private runtime configuration
outside every meta-agent-readable root. Never treat a prompt instruction not
to read credentials as the boundary.

**Acceptance:** use a synthetic marker with actual Debugger execution; scan
the authorized Evolve roots and exercise the read tool. The marker must be
absent while authenticated client construction still receives it in memory.

### F04. Verified artifact lineage does not verify the trial labels

**Sources:** [`evaluation.py:18-31`](../src/ahe_runner/evaluation.py#L18-L31)
and [`evidence.py:197-254`](../src/ahe_runner/evidence.py#L197-L254).

`VerifiedEvaluationUnit` carries identity and artifact references, but no
canonical outcome, score, or termination. Evidence verification binds each
trial to those references without comparing its semantic fields.

Starting from the real runtime-backed mixed task, the probe changed only one
successful evidence trial:

```text
score:              1 -> 0
outcome:         pass -> fail
termination:          -> action_limit
```

It updated the derived detail hash and evidence-manifest hash. The native
result, native trace, accepted evaluation, and aggregate `1/2` task state were
unchanged. `verify_evidence_bundle()` still accepted the bundle, even though
both detail trials now declared zero score.

**Why it matters:** a formatting/conversion bug can give the Debugger false
pass/fail supervision while all identity and hash checks succeed. A genuine
trace link is not proof that the accompanying label accurately describes it.

**Repair direction:** expose protected, adapter-verified canonical trial facts
in the evaluation contract, or add an adapter-owned evidence-verification hook.
Bind outcome, score, and termination to that authority. This does **not**
require the generic runner to understand every benchmark's raw trace format or
invent a universal reward function.

**Acceptance:** change each semantic field independently in otherwise valid
evidence and require rejection. Preserve valid zero rewards, partial outcomes,
and benchmark-native scoring semantics.

### F05. The Debugger gate validates a claimed list, not the actual citations

**Sources:** [`meta/tools.py:287-351`](../src/ahe_runner/meta/tools.py#L287-L351),
[`meta/tools.py:75-182`](../src/ahe_runner/meta/tools.py#L75-L182), and
[`debugger.py:64-93`](../src/ahe_runner/debugger.py#L64-L93).

The submission tool checks that the primary detail is in
`evidence_coverage.artifacts_read` and that this self-reported list is contained
in the access ledger. It does not check `evidence[].location` or the paired
passing/failing citations against that ledger. Paired status is validated by
nonempty strings, not by the task's actual available trials.

Two reproductions passed:

1. An actual NexAU Debugger read only its primary detail, then submitted
   invented event-999 claims about real raw trace paths it had never read.
   Its paired analysis was accepted after two model requests and zero raw
   trace reads.
2. A byte read starting beyond the primary file's end returned zero bytes but
   still added the file to the access ledger. That was enough for an accepted
   submission.

The first case does not deny that primary details may already contain useful
native information. The defect is acceptance of purported citations to
specific unread, nonexistent events, contrary to the stated gate.

**Repair direction:** make citation references structurally checkable and bind
them to actual nonempty reads/search hits, with artifact identity and useful
location information. Determine paired-trial availability from verified trial
facts. If comparable pass/fail trials exist, an arbitrary “unavailable”
explanation should not silently bypass the requirement.

**Acceptance:** reject unread references, invalid locations, empty-read
coverage, and false unavailable claims; accept genuine read-backed diagnoses
and genuine evidence limitations. Retain the real NexAU stop-tool repair loop.

This can enforce evidence access, not prove that a model understood the
evidence or established causality. The report does not demand that impossible
guarantee.

### F06. Helper imports work only when they happen during validation

**Sources:** [`conformance.py:118-133`](../src/ahe_runner/conformance.py#L118-L133),
[`conformance.py:454-521`](../src/ahe_runner/conformance.py#L454-L521), and
[`HARNESS_ABI.md:110-114`](HARNESS_ABI.md#L110-L114).

The new helper detection compares `sys.modules` before and after importing the
registered binding. It therefore observes eager imports, not imports inside
the tool function.

The same `tools/helper.py` was accepted with a module-level import and rejected
after moving that import inside the registered `record_note()` function:

```python
def record_note(...):
    from tools.helper import normalize_note
    note = normalize_note(note)
    ...
```

The rejection was:

```text
declared tool_implementations component is not runtime-attached: tools/helper.py
```

An independent real NexAU execution of that tool succeeded, returning
`{"note_count": 1, "recorded": "padded"}` and then `OK`. The production controller
would correctly honor the conformance rejection; the problem is the
validator's incorrect compatibility judgment, not bypassing it in production.

**Why it matters:** ordinary Python used for lazy loading or avoiding import
cycles can make an otherwise working harness impossible to onboard.

**Repair direction:** recognize local dependency imports independently of
whether their function executed during validation, or provide explicit,
validated support-file declarations. Do not invoke arbitrary tools just to
discover their imports, and do not require fake tool registrations for helpers.

**Acceptance:** eager and function-local imports of the same declared helper
must both conform and run. Unreferenced files and references outside allowed
roots must still receive the intended validation treatment.

### F07. Old write arguments can keep context above the compaction target

**Sources:** [`meta/middleware.py:44-132`](../src/ahe_runner/meta/middleware.py#L44-L132)
and [`meta/nexau_backend.py:655-665`](../src/ahe_runner/meta/nexau_backend.py#L655-L665).

The adaptive window does work for large old **tool results**. A positive
control shrank the preferred window to four iterations, preserved the latest
turn, and reached `124,252` tokens under the `144,000` target.

However, the compactor never shrinks old assistant tool-call arguments. Full
`write_file` calls retain previous file bodies. Using the actual NexAU token
counter and the configured `200,000 * 0.72` target:

| Measurement | Tokens |
|---|---:|
| Five large file-write turns before compaction | 157,774 |
| After compaction, retaining only one recent iteration | 157,890 |
| Target | 144,000 |
| Latest iteration alone | 31,552 |

The latest turn fits comfortably. Old write arguments prevent the target from
being met; replacing tiny write-result messages with longer markers actually
adds a small amount of context. This example is still below the hard
200,000-token ceiling, but further writes can exhaust it.

**Repair direction:** bound older write payloads or provide an appropriate
bounded-edit/compaction mechanism, preserving tool-call/result IDs and the
latest turn. If compaction cannot meet the budget, surface that condition and
use a deliberate recovery policy rather than treating any modification as
sufficient. Do not replace tiny results with larger markers.

**Acceptance:** test both read-heavy and write-heavy histories against the
actual target, including preservation of message structure, result IDs, and
error flags.

**Classification:** this is an additional long-run reliability limitation.
The original public configuration also used tool-result compaction; this
report does not establish that the public implementation solved this case.
It is not a reason to reject the whole AHE adaptation or a short controlled
pilot.

## 4. Historical correction: the bundled original Debugger did navigate files

One of the two narrowed historical claims is not supported by the pinned
public source. This is not based on an untracked or later-installed dependency:
`git ls-tree` confirms the Debugger `_source` files are present in the original
`8b2a55d` tree.

The relevant original path prefix is:

```text
agents/evolve_agent/skills/agent-debugger-cli/_source/agent_debugger_core/
```

Under that prefix:

- `cli/adb.py:64-89` normalizes trace files and calls `run_agent()`.
- `runtime/runner.py:41-77` passes trace **paths** in the user message and
  constructs a NexAU `Agent` from its YAML configuration.
- `runtime/agent_config.yaml:1-7,22-54` sets `max_iterations: 25` and registers
  read, search, glob, directory-listing, shell, and completion tools.

Original `evolve.py` invokes `adb ask` with trace paths. Its
`_ensure_adb_installed()` installs this bundled source if no `adb` is available.
An independently preinstalled `adb` could differ, so the repository alone
does not establish what every historical experiment installed. It does
establish that the **bundled public implementation is a file-tool agent**.

Recommended wording:

> The public controller makes a host invocation of `adb ask` for each task.
> Its bundled implementation runs a tool-using NexAU Debugger over normalized
> trace files. The runner substitutes a bounded read-only Debugger, changes
> the default iteration budget from 25 to 80, and adds structured submission
> and evidence-access checks.

One host invocation is not one model completion. Correct the corresponding
Debugger rows in `PROMPT_FIDELITY.md` and the historical statements in
`AHE_COMPLIANCE_SUMMARY.md` and `AHE_COMPLIANCE_REPAIR_PLAN.md`.

The other narrowing remains reasonable: mutable Explore knowledge is discussed
in the paper but is not established as a required executable mechanism in this
public baseline.

## 5. Method fidelity: preserve these choices and label these differences

The following are **not reopened defects**:

- **No automatic rollback.** Agent-mediated rollback with usable history,
  diffs, and selective restoration preserves the intended decision process.
- **Immutable Explore outputs.** Keep this declared choice. It does not make
  the public-implementation adaptation invalid.
- **Heuristic attribution.** Predictions for individual edits are compared
  with one combined-candidate evaluation. Multiple edits can receive credit
  for the same improvement. Isolated per-edit causal experiments are not a
  requirement inherited from original AHE.
- **Benchmark-owned structured tools.** Inject their schemas into the runtime
  API tool list while keeping execution and semantics in the adapter. Prompt
  prose alone is not an equivalent tool API.
- **Native metrics.** A new benchmark need not pretend its scoring rule is the
  original coding benchmark's pass@k.
- **Selecting C0.** Returning the verified baseline when no candidate wins is
  correct behavior, not evidence that the architecture failed.

Remaining deliberate differences prevent claiming a literal reproduction of
all original settings:

| Surface | Original bundled configuration/protocol | Runner |
|---|---|---|
| Source/Web iteration budgets | 200 each | Defaults remain 200 |
| Evolve iteration budget | 500 | Default remains 500 |
| Debugger iteration budget | 25 | Default 80 |
| Role output/sampling settings | Explore 32,000 tokens / 0.2; Evolve 32,000 / 0.7; bundled Debugger 20,000 / 0.4 | Shared configured meta-model settings rather than those distinct role defaults |
| Explore compaction | Threshold 0.75, preferred 15 iterations | Shared threshold 0.72, preferred 12, adaptively reduced |
| Evolve compaction | 220,000-token middleware configuration, threshold 0.75, preferred 10 | Shared configured context limit, threshold 0.72, preferred 12 |
| Explore research protocol | More prescriptive search/depth/deliverable instructions | Less prescriptive reusable-skill contracts, including four-source Web guidance |
| Meta tooling | Broader native file/shell tools | Narrower protected roots and bounded tools |
| Transport | Original Explore configs requested streaming | Runner requires non-streaming chat completions |

Many of these are sensible portability or protection choices. They should be
described as an **AHE-method adaptation with explicit settings**, not exact
experiment equivalence. The fidelity matrix already acknowledges several;
the Debugger comparison needs the historical correction above.

## 6. Plug-and-play and documentation assessment

The proposed separation is sound: a file-backed NexAU harness supplies agent
behavior; the adapter supplies the task/environment and scoring; the controller
owns the evolution process. The real runtime-backed example now demonstrates
that separation rather than merely providing a phrase-scoring stand-in.

The remaining work is to make the supported paths agree with the promises.
F01/F02 affect whether the supposedly fixed evaluation environment stays
fixed. F04/F05 affect whether the next edit is informed by trustworthy
experience. F06 affects whether normal existing harness code can be onboarded.
F03 must be addressed when real credentials are used.

This is **plug-and-play for the declared text/tool session contract**, not yet
for every task NexAU can represent. Typed images/audio, changing tool sets,
interruptions, and event streams remain explicit future ABI work. A screenshot
UI benchmark may need such an extension; that extension should be made to the
adapter/runtime contract without forking NexAU's central executor.

The improved README, adapter guide, operator guide, and runtime-example guide
are usable starting points. The explicit warning that changing only the offline
meta backend does not create a live benchmark is particularly helpful.
`VALIDATION_RESULTS.md` is also honest about not running a live campaign.

Documentation adjustments still needed:

1. Replace the unconditional all-findings-complete status with scoped results
   and the open findings above.
2. Correct the original Debugger history and role-budget comparison.
3. Distinguish artifact/identity verification from trial-label verification.
4. Describe the actual grounding and middleware limitations until fixed.
5. Narrow broad compaction and transitive-import guarantees until the
   additional cases pass.

This does not call for arbitrary-code hosting isolation in the current
trusted-local MVP, additional mandatory benchmark formats, or guaranteed score
improvement. Those would be different product commitments.

## 7. Suggested repair order and acceptance

| Order | Work | Completion evidence |
|---|---|---|
| 1 | F01 effective-request policy, F02 full hook-chain handoff, F03 private credential handling | Real SDK/body and NexAU boundary tests; no request on policy rejection, no premature continuation, no key in readable artifacts |
| 2 | F04 canonical trial facts, then F05 citation/paired-analysis enforcement | Otherwise valid contradictory evidence and unread diagnoses are rejected; genuine evidence remains accepted |
| 3 | F06 lazy support imports; F07 bounded write-heavy history | Normal helpers conform and execute; measured context targets hold or explicit recovery is invoked |
| 4 | Historical/status documentation | Claims match the pinned original code and the actual acceptance coverage |
| 5 | A separately scoped live pilot | Real role completion, actionable diagnoses, evidence-linked edits, and held-out selection observed under approved model/task/budget settings |

Preserve the currently working attribution, immutable checkpoints, selective
restore tools, final-HEAD validation, explicit public roots, and atomic exports.
The fixes should close these specific gaps, not redesign the optimizer.

## Appendix: local reproduction artifacts

The audit artifacts are retained in this session directory, outside the
repository and outside the production package:

```text
/home/agarwaldi/.copilot/session-state/4710795b-04b8-4bad-ba06-5ff58da62939/files/
  ahe_reaudit_probes.py
  ahe_reaudit_probe_results.json
```

Run against the audited environment:

```bash
cd /home/agarwaldi/ahe-runner
.venv/bin/python \
  /home/agarwaldi/.copilot/session-state/4710795b-04b8-4bad-ba06-5ff58da62939/files/ahe_reaudit_probes.py
```

The script generates isolated temporary operator projects, uses synthetic keys
and local response fixtures, and cleans those temporary projects afterward.
Its assertions demonstrate the current gaps; they should be inverted and
converted into regression tests when repairs are implemented.

| Artifact | SHA-256 |
|---|---|
| Probe script | `8e959b8063d48a35803bcfb740d97d3dbadcb0aad478bf9c83127668c9ec7ba7` |
| Captured results | `9206ce64ff73eaccee1165b46720fe035705dbbc3d6bf106f0047a0b81a42b0d` |

These are local reproduction artifacts, not committed regression tests.
The report records the fixtures, observed outcomes, source locations, and
acceptance criteria so the findings do not depend on trusting a test count.

## Release 0.3.0 closure check

**Reviewed implementation:** `25f8c09ae45fef0d8839148400a37107edce8430`,
clean before this documentation update. NexAU remains `0.3.9` with the same
runtime source hashes as the previous review.

**Verdict:** No additional core AHE-method deviation was found in these
repairs. The major request, action-handoff, credential, trial-semantics, and
compaction failures are fixed. The release is close, but F05 and F06 still have
specific supported-path gaps, and the runtime reports the wrong release
version. These are localized repairs, not a reason to redesign the system.

### Confirmed repairs

The targeted contract and campaign-integration suite passed **47 tests in
81.55 seconds**. Independent local probes additionally confirmed:

| Area | Observed behavior on the repaired implementation |
|---|---|
| F01 request policy | Eight original override/alias cases were rejected with zero HTTP requests. Explicit high reasoning and campaign token/temperature settings worked through actual NexAU and OpenAI SDK serialization. |
| F02 action handoff | The real runtime-backed example, with the previously problematic legacy continuation hook, made the expected six requests for four trials. No model continuation occurred before the adapter supplied the action result. |
| F03 credential handling | An actual Debugger run wrote only the placeholder to its readable YAML. The synthetic credential marker was absent from the entire generated Debugger output tree. |
| F04 canonical facts | Rehashing evidence after changing outcome, score, termination, or diagnostic class no longer hides a contradiction: all four mutations were rejected. |
| F05 ordinary grounding | A real Debugger submission citing unread artifacts was rejected; the agent could read them and successfully resubmit using the new structured schema. The boundary case below remains. |
| F06 ordinary lazy helper | The committed regression accepts function-local imports of a simple helper module. Package initializers remain a gap below. |
| F07 compaction | The original write-heavy fixture fell from 157,759 to 126,304 tokens under the 144,000 target, preserving the latest turn. Actual NexAU blocked an irreducible 3,014-token request against a 2,500 target, below its 5,000 hard ceiling, with zero provider requests. |
| Historical Debugger claim | The fidelity and repair documents now correctly describe the bundled original tool-using Debugger and its 25-iteration default. |

All model responses were generated locally by deterministic fixtures. This
does not establish live research quality, diagnosis quality, or benchmark
improvement. No provider endpoint was contacted.

### C1 / F05: an unread final line can enter the citation ledger

**Priority:** P2.

**Source:** [`meta/tools.py:159-189`](../src/ahe_runner/meta/tools.py#L159-L189).

When a line-mode read exhausts `max_bytes`, the truncation branch assigns
`last_line = line_number` even if the remaining byte allowance is zero. The
ledger then marks that unread line as returned, provided an earlier line
contributed some bytes.

Concrete reproduction:

```text
trace.jsonl:
{"event":"setup"}
{"event":"UNREAD_FAILURE"}

read_file(root="evidence", relative_path="trace.jsonl",
          end_line=2, max_bytes=18)
```

Only the 18-byte first line was returned. The tool reported `end_line: 2`,
and `submit_debugger_diagnosis` accepted a citation specifically to line 2.
A control that read only line 1 without crossing the byte boundary correctly
rejected the identical citation.

**Fix:** credit only lines actually returned. Do not credit a wholly unread
next line, and do not treat a partially returned final line as a complete
line-range read; represent its actual byte coverage instead.

**Acceptance:** the exact-boundary and partial-line cases must reject citations
outside returned content, while a subsequent complete read permits the same
valid citation. This concerns access enforcement, not proving the model's
interpretation correct.

### C2 / F06: lazy submodule imports omit their package initializers

**Priority:** P2.

**Source:** [`conformance.py:527-588`](../src/ahe_runner/conformance.py#L527-L588).

The static graph resolves the final module file but does not include the
`__init__.py` files Python executes along its package path. For example:

```text
tools/helpers/__init__.py
tools/helpers/normalize.py
```

```python
def record_note(...):
    from tools.helpers.normalize import normalize_note
    ...
```

The same import at module scope passed conformance. Inside the tool function,
it failed with:

```text
declared tool_implementations component is not runtime-attached: tools/helpers/__init__.py
```

The actual NexAU tool executed successfully and returned
`{"note_count": 1, "recorded": "padded"}`. This is an ordinary static Python
import, not the intentionally unsupported dynamic-import case.

**Fix:** include each local ancestor package initializer and its dependencies
when resolving a static import. Preserve containment and unreferenced-file
checks rather than blanket-whitelisting every initializer.

**Acceptance:** eager and function-local imports through a regular nested
package both conform and execute, including absolute and relative forms.

### C3: the 0.3.0 wheel and campaigns still report runtime version 0.2.0

**Priority:** P3; release metadata rather than an AHE algorithm defect.

**Sources:** [`__init__.py:3`](../src/ahe_runner/__init__.py#L3),
[`controller.py:833`](../src/ahe_runner/controller.py#L833), and
[`pyproject.toml`](../pyproject.toml).

The project and built wheel metadata say `0.3.0`, but the wheel's
`ahe_runner/__init__.py` still sets `__version__ = "0.2.0"`. A fresh campaign
therefore records `"runner_version": "0.2.0"` while using the new contracts.
This was verified in both source and the built wheel, not inferred from stale
editable-install metadata.

**Fix:** synchronize or derive the runtime release version and verify that
wheel metadata, the runtime value, and campaign metadata agree. Also update
the runtime-example guide's remaining "schema-V2 evidence" wording to schema
V3.

The separate full-package content hash still identifies code changes, so this
does not demonstrate unsafe campaign reuse. It does make release reporting
incorrect.

### Readiness and retained evidence

Fix C1-C3 before declaring the `0.3.0` repair completely closed. The reviewed
architecture is otherwise a sound basis for a scoped text/tool benchmark
pilot. Preserve agent-mediated rollback, immutable Explore seeds, benchmark
ownership, and the existing evolution procedure. There is no newly identified
need to change those methodology decisions.

The new probes and results are stored alongside the previous local audit
artifacts:

```text
/home/agarwaldi/.copilot/session-state/4710795b-04b8-4bad-ba06-5ff58da62939/files/
  ahe_closure_probes.py
  ahe_closure_probe_results.json
```

Run `ahe_closure_probes.py` with the runner's `.venv/bin/python`; it imports
fixture helpers from the adjacent original `ahe_reaudit_probes.py`. It creates
and cleans isolated temporary projects, uses only synthetic credentials and
local response fixtures, and does not change implementation files.

| Artifact | SHA-256 |
|---|---|
| Closure probes | `5731f6c64a0eb8090bb1e30d508140616c819ea88bbf22313c8477a4f8565db8` |
| Captured results | `eb8791f14846925c66ee9c12fd7d45de11b0c307e7124744445c7096e0987947` |
