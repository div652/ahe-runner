# AHE Runner recommendations from the Harvey LAB integration

This is a cumulative maintainer-facing record, separate from the implementation
plan and generated campaign artifacts. It contains confirmed issues, their
impact, approved branch-local repairs, and recommendations for upstream AHE.
It does not claim that a local patch has been accepted upstream.

## Working agreement

For every newly encountered issue:

1. Stop the affected implementation and explain the issue to the operator.
2. Wait for the operator to understand and authorize the local repair.
3. Add evidence, scope, proposed repair, and upstream recommendations here.
4. Make the smallest approved repair on its owning branch: `harveybench` for
   AHE/project code or `divyanshu_harvey_evolution` for approved LAB changes.
5. Commit the repair, regression coverage, and explanatory record before
   executing offline checks; record their outcome without erasing failures.
6. Continue the Harvey integration until the next issue or decision.

The operator owns final `ahe conformance`, `ahe validate`, `ahe run`,
phase/resume commands, live model preflights, and benchmark/evolution execution.
The assistant announces new permanent files before creating them.

## Issue register

| ID | Confirmed issue | Operator approval | Branch-local status |
|---|---|---|---|
| AHE-001 | Evaluated-agent sessions skipped the configured model preflight | Explained; repair authorized on 2026-10-12 | Fixed in `4a4e57b`; targeted offline regressions and lint passed |
| AHE-002 | Registered skill folders' helper/resource files are not recognized as active skill components | Explained; operator chose a skills-free initial harness instead of a repair; on 2026-10-14 the operator asked for a LAB-exact harness for campaign 9 | No conformance patch. Campaign 9 puts LAB's skill scripts in the Harvey `workspace_seed/` folder, copied into each sandbox (see the AHE-002 section) |
| LAB-001 | Filename helper bypasses configured Copilot routing and suppresses its API failure | Explained in plain language; repair authorized on 2026-10-12 | Fixed in LAB `86678eba6` and AHE `f0e3b0b`; 92 LAB and 19 AHE offline cases passed |
| AHE-003 | Candidate harness folders can shadow protected Python modules during evaluation and grading | Explained in plain language; adapter-level repair authorized on 2026-10-12 | Adapter-level rejection in Harvey `validate_candidate`; AHE source unchanged |
| AHE-004 | `ahe-runner` cannot be declared as a dependency of an operator package under uv, because of its NexAU git-URL pin | Explained; recording authorized on 2026-10-12 | Worked around in the Harvey `pyproject.toml`; AHE source unchanged |
| AHE-005 | V2 offers no way to give editable harness tools a benchmark-owned isolated execution environment | Explained; option A (adapter-owned per-session container lookup) chosen on 2026-10-12 | Adapter-owned sandbox handle in `harvey_ahe.workspace`; its token-in-context routing was replaced under AHE-006 |
| AHE-006 | Routing a session to its resource through session `context` is unsafe: NexAU's current context is one process-wide slot that concurrent sessions' sub-agents can read | Explained in plain language; on 2026-10-13 the operator chose orchestrator-owned binding (the runner hands each session its own resources) | Branch-local `AgentSessionRequest.session_resources` in `runtime.py`; Harvey adapter binds the sandbox handle through it |
| AHE-007 | The static import scan misreads `from pkg import name` as `pkg.n.a.m.e` | Explained with AHE-006; recording only | No patch; the Harvey seed imports helpers as `from tools._common import ...`, which is unaffected |
| AHE-008 | Meta-agents have no sandbox work folder, so NexAU copies the Explore skills, read-only, into the operator's current directory; a later Evolve run there fails with `Permission denied` | Explained in plain language; on 2026-10-13 the operator approved a private per-run folder and deleting the leftover `AHE_Harvey/.skills` | Branch-local `LocalSandboxConfig(work_dir=<runtime_dir>/sandbox)` in `meta/nexau_backend.py`, plus a regression test |
| AHE-009 | Project role supplements are rendered as Jinja together with the core meta prompt, so template markup in a supplement crashes that meta-agent at its first model call; config loading does not check this | Explained in plain language; recording approved on 2026-10-13 | No AHE patch; the Harvey supplements avoid Jinja markup, and an offline render check passes for all four roles |
| AHE-010 | `search_text` has no total output budget, so a few parallel searches over the debugger's raw model-call logs overflowed the Evolve context (617,681 tokens against 200,000) and stopped round 2 of the first live campaign | Explained in plain language; on 2026-10-13 the operator approved a per-call output cap with match snippets | Branch-local cap (about 32,000 characters per call), 400-character match snippets, `truncation_reason`, and a regression test in `meta/tools.py`; 179 offline tests passed, and a replay of the failing searches dropped from about 617,681 to about 48,000 tokens |
| AHE-011 | A non-infrastructure meta-agent failure permanently blocks its stage with attempts left, no command unblocks it, and protected-code fingerprints forbid continuing the campaign after any AHE repair | Explained in plain language; on 2026-10-13 the operator chose a new campaign with the AHE-010 repair | No AHE patch; first campaign kept as evidence, new campaign `harvey-lab-campaign-2-search-cap` started from round 1 |
| AHE-012 | Independent evaluations run one after another (validation of C0..Cn, the final test pair), and exploration waits for the C0 evaluation it does not use, leaving most allowed concurrency idle | Timing review explained on 2026-10-13; recording approved (performance recommendation, not a correctness defect) | No AHE patch. The Harvey side switched to LAB's default `grading_parallel: 6` (campaign 3) |
| AHE-013 | Meta agents have no general tool-output safety net (the original AHE evolve agent uses NexAU's `LongToolOutputMiddleware`) and no shell or structured way to read one field of a very long JSON trace line | Raised by the operator on 2026-10-13 after comparing with the original AHE `evolve_agent.yaml`; recording approved | No AHE patch at first; the safety-net part is addressed locally by AHE-014 |
| AHE-014 | `list_files` has no output-size budget: in campaign 3 round 1, one recursive Evolve listing of `raw_evaluation` returned 274,020 characters into the context | Explained in plain language; on 2026-10-13 the operator approved option 2b (a long-output safety net for Evolve and both Explore agents, not the Debugger), a 64,000-character limit with a 24,000 + 8,000 preview, and campaign name `harvey-lab-campaign-4-gp50-output-backstop` (later renamed `harvey-lab-campaign-4-judge-cap-output-backstop`; runbook section 5d) | Branch-local `MetaToolOutputBackstopMiddleware` in `meta/middleware.py`, wired for Evolve and Explore in `meta/nexau_backend.py`; 191 offline tests passed, and a replay of the 274,020-character listing showed the agent 32,734 characters |
| AHE-015 | AHE has no endpoint-wide limit on model calls in flight: the evaluated agent, the benchmark judges, and the meta agents each send calls independently, and a burst above the Copilot limit is refused instead of waiting | Explained in plain language; on 2026-10-13 the operator asked that calls above the limit be queued, and approved `transport.max_concurrent_requests: 24` with the Harvey judge cap lowered to 18 | Branch-local `transport.max_concurrent_requests` and a shared queue in `meta/model_client.py`; 199 offline tests passed |
| AHE-016 | The meta agents' harnesses differ from the original AHE agents: different file tools without binary or line-length protection, no long-output middleware for the Debugger, a context compaction that stops the agent instead of falling back, no shell, an 80-iteration debugger, and a debugger failure blocks the round. In campaign 4 round 2 a debugger read four `.docx` files as text and was stopped | Explained; on 2026-10-13 the operator asked for full parity with the original AHE meta agents and chose: path guard, Docker-sandboxed shell, original debugger budget, non-blocking debugger failures, original temperatures, readable evidence, `nexau-evolution-guide` skill (runbook section 5f) | Implemented and tested offline (`6f9d941`); campaign 5 is the live check |
| AHE-017 | Change attribution compares Evolve's predicted task IDs (the debugger's per-round `task-<hash>` keys) with the evaluation's task keys, which never match, so every change was marked INEFFECTIVE with 0 hits in campaigns 3, 4 and 5 round 1, and the next Evolve read that wrong feedback | Found after campaign 5 round 1; on 2026-10-13 the operator chose to fix it, redo C1's attribution, then continue round 2 | Branch-local alias map (debugger key, task id, task key → evaluation task key) in `controller.py` and `history.py`; 229 offline tests passed |
| AHE-018 | Changing the primary metric left the Harvey role supplements describing the old one, so campaigns 4 to 8 gave the Debugger and Evolve conflicting objectives; nothing in AHE checks that they agree | Explained on 2026-10-14; the operator asked to record it and the general rule; fix approved after campaign 8 | Fixed locally on 2026-10-14 after campaign 8: all four supplements now describe `mean_criterion_pass_rate`; applies from campaign 9. Rule for future metric changes recorded in the AHE-018 section |
| AHE-019 | Long-term memory is always appended to the evaluated agent's system prompt with no separator, even when it only holds a note about the file itself; in campaigns 1 to 8 every evaluated trial's prompt ended `...the work is complete.# Persistent harness memory` followed by a note meant for Evolve | Explained on 2026-10-14; the operator chose to fix it in AHE core | Fixed locally on 2026-10-14 (`6f72430`): empty memory files add nothing and the suffix starts after a blank line; the starting memory files are empty and the note moved to the Evolve supplement. Applies from campaign 9 |
| AHE-020 | Conformance requires `additionalProperties: false` in every tool schema, and NexAU sends that field to the model, so a harness cannot reproduce a benchmark's own tool schemas exactly | Explained on 2026-10-14; the operator chose an opt-in manifest setting | Fixed locally on 2026-10-14 (`44ea8aa`): `tool_schemas.allow_omitted_additional_properties`, default off; on only in `harness_manifest_lab.yaml` (campaign 9) |
| AHE-021 | The GH Copilot Server transport silently cut prompts over about 270k tokens from the start and ignored the requested reasoning effort; AHE has no check that the transport honours the configured context and effort | Found on 2026-10-14 while analysing campaign 9's infrastructure errors; the operator stopped campaign 9 and approved a patch | Fixed locally on 2026-10-14 in the host's GH Copilot Server `server.js` (sends `options.configuration`); verified by `scripts/copilot_context_effort_probe.py` |
| AHE-022 | A model call that hangs until the request timeout fails its whole trial; the unit is rerun from the start, which wastes work and favours shorter trials | Found on 2026-10-14 in campaign 9 (52 gpt-5.6-sol hangs); the operator chose per-call resending | Fixed locally on 2026-10-14: `transport.hung_call_retries` (default 0), every attempt recorded |
| AHE-023 | NexAU checks the context limit with a characters-÷-4 estimate and stops the agent before sending; LAB has no such check, and a provider "too long" error became an infrastructure error that was rerun | Explained on 2026-10-15 after campaign 9's rerun; the operator chose to keep those results with a caveat, fix it, and evaluate all 250 tasks again | Fixed locally; campaign 9b re-evaluates |
| AHE-024 | The exact token counter of AHE-023 uses o200k_base for every model; Claude's tokenizer turns the same Harvey text into 1.65-1.68 times as many tokens, so `max_context_tokens` means a different amount of text per model | Explained on 2026-10-15 while preparing campaign 9c (claude-opus-4.8); the operator chose to keep the harness unchanged and let Anthropic's rejection end Opus runs, as LAB does, and approved recording; 9c was then dropped | No patch; only GPT models (o200k_base) are evaluated so far |
| AHE-025 | All concurrent trials run as threads in one Python process, so host-side harness tools such as `grep` queue behind each other; LAB runs every task in its own process | Found on 2026-10-15 during campaign 10's C0 evaluation; the operator approved recording it | Fixed locally: the adapter setting `trial_processes` runs each trial's agent in its own process; campaign 10 was stopped and restarted with it |
| AHE-026 | When an outage outlasts a stage's attempts, the stage is blocked and the campaign cannot continue, although every attempt failed on infrastructure and no result is in doubt; there is also no hook to keep a long-lived dependency such as the model server healthy between stages | Found on 2026-10-16 when campaign 12's model server crashed during the C4 train evaluation; the operator approved a command that reopens only infrastructure-blocked stages, plus a pre-stage hook | Fixed locally: `ahe retry-blocked-stage` (recorded in `campaign_amendments.json`) and `AHE_PRE_STAGE_COMMAND`; campaign 12 resumed with both |
| AHE-027 | `ahe extend-rounds` refuses once evolution has closed, so a finished campaign cannot be continued for more rounds; the only option is a new campaign that repeats every finished round | Raised on 2026-10-16 when the operator asked to run campaign 12 (finished at 5 rounds) for 5 more rounds from its last checkpoint | Proposed: `ahe extend-rounds --reopen-closed-evolution` moves the closing stages and their outputs to `superseded/closure-NN/` and records them in the amendment; every other stage is reused |
| LAB-002 | Oversized agent output has no defined LAB score | Option A (240,000-character judge-prompt limit) chosen on 2026-10-12 | Harvey judge wrapper; full record in `docs/HARVEY_LAB_BENCHMARK_ISSUES.md` |
| LAB-003 | Dual-judge disagreement semantics (discussion item) | Disagreement labelled failing; headline unchanged | Harvey adapter metrics; full record in `docs/HARVEY_LAB_BENCHMARK_ISSUES.md` |

Other potential integration limitations in the implementation plan still
require investigation and the operator's case-by-case approval.

Harvey LAB issues (LAB-002 onward) are recorded in full in
`docs/HARVEY_LAB_BENCHMARK_ISSUES.md` so they can be shared with Harvey
separately; LAB-001 stays here for continuity and is summarized there.

## AHE-001: enforce preflight for evaluated-model attempts

### Evidence and affected baseline

Inspected AHE revision:
`bc5ef29837a58a0afe2d621ae3c825c2b3c7acec` on `harveybench`,
package declaration `ahe-runner 0.3.0`.

At that revision:

- `src/ahe_runner/runtime.py:255-366` opened evaluated sessions and constructed
  `RecordedExactModelClient` directly.
- `src/ahe_runner/meta/model_client.py:39-174` enforced requested/returned
  labels and generation policy, but did not perform catalog or positive/
  negative preflight probes.
- The preflight call sites in `src/ahe_runner/meta/nexau_backend.py`
  at lines 100, 158, and 261 all used `models.meta`.
- `docs/OPERATOR_GUIDE.md:242-244` described fresh probes for every
  model-backed stage attempt, a broader claim than the implementation.

Reading the shared preflight function and every call site established the gap
without sending live model requests.

### Expected versus actual behavior

With `transport.model_catalog: required` and
`transport.negative_control: required`, an evaluated model should complete the
same observable-contract checks as the meta model before task execution:

1. The exact model is advertised.
2. A small positive request succeeds with the requested response label.
3. A deliberately nonexistent model is rejected as required by the policy.

Previously only the meta role received those probes. Evaluated task calls
still rejected missing/different completion labels. There was no observed
label mismatch; this is missing startup policy enforcement, not evidence
that another upstream model actually answered.

### Impact on the Harvey integration

The benchmark adapter receives an AHE-owned evaluated runtime. It should not
have to duplicate runner model transport policy or create its own evaluated
client just to make the advertised preflight contract true.

Without the repair, a required evaluated catalog/negative-control policy
could be configured but never checked. That weakens early detection of an
unavailable endpoint/model or an endpoint that accepts arbitrary model IDs.

### Approved local repair

The fix is scoped to evaluated V2 attempts and reuses
`meta.preflight.run_model_preflight`; no second probe implementation is added.

- Run lazily after session/config/tool checks and before constructing the
  first evaluated client/Agent for the attempt.
- Supply the exact evaluated `ModelSettings`, transport policies, and
  in-memory API key.
- Share one preflight across sessions in the runtime instance. The controller
  creates one runtime instance per evaluated stage attempt.
- Synchronize access and remember both success and failure. A failed probe
  is re-raised unchanged, not silently retried by another session in the same
  attempt.
- Retain each fresh attempt's report in its own directory:
  `runtime/model-preflights/attempt-*/model-preflight.json`.
  The random directory suffix is attempt evidence, not semantic identity.
- Link the proof path/hash from each successful session's
  `runtime_manifest.json`.
- Preserve failed reports when a new evaluation attempt runs.
- Keep request/unit identity unchanged: timestamps, random negative model
  IDs, and proof directories do not enter semantic inputs.
- Perform no probes if the adapter opens no new sessions, including an
  evaluation resumed entirely from verified completed units.

Catalog/negative-control `required`, optional/best-effort, and disabled modes
retain the shared helper's existing semantics. Normal completion-label checks,
model budgets, reasoning policy, retries, and benchmark metrics are unchanged.

### Offline regression coverage

New cases are added to the existing `tests/test_runtime.py`; no separate
runtime implementation or regression-test file is created.

The test endpoint uses `httpx.MockTransport`; catalog, positive, and negative
requests are fully local test objects, not calls to the Copilot server.

Coverage includes:

- no calls at runtime construction;
- evaluated-model identity/reasoning/non-streaming settings on the probe;
- one proof reused across multiple sessions and concurrent preflight access;
- rejection before evaluated-client/Agent construction when the catalog,
  positive response, or required negative control fails;
- preservation of infrastructure classification and failure caching;
- an unchanged failed proof plus a fresh proof on a new attempt;
- unchanged evaluation request hash despite fresh attempt evidence;
- explicit disabled controls while retaining the positive probe;
- existing protected-action, session-history, and custom-root behavior.

Targeted offline command, to be executed only after the fix commit:

```bash
.venv/bin/python -m pytest tests/test_runtime.py tests/test_model_access.py tests/test_runtime_example_adapter.py tests/test_evaluation_store.py -q
```

The initial fix/coverage record was committed as `4a4e57b` before testing.
The first run from the checkout returned **26 passed, 1 failed**: an existing
runtime test assumes the current directory has no `.skills` cache. The
checkout's ignored cache predates this patch (2026-10-12 14:40 UTC), so this
was an environment precondition failure, not a failed preflight regression.

The same four test files were run from a fresh temporary working directory:
**27 passed in 1.80 seconds**. No existing cache was deleted and no source
change was made to bypass the assertion. Ruff also passed for both changed
Python files. All requests used the in-memory mock HTTP transport; the
assistant did not invoke the operator's final CLI checks or live model calls.

For an existing checkout with a skill cache, run the above absolute test
paths from a fresh empty working directory. As a test-harness improvement,
maintainers could snapshot pre-existing cache state and assert that it was
not modified, instead of asserting that no cache exists. This observation
does not establish a new runtime defect and is not a second local repair.

### Upstream recommendations

1. Enforce model policy at the runner-owned evaluated boundary, not separately
   in every benchmark adapter. Include this regression in release coverage.
2. Define the scope explicitly: a new evaluated stage attempt shares one
   preflight across new sessions; fully cached recovery is not a new
   generation and must not consume extra model calls.
3. Preserve proofs and reference them by hash. Consider passing the existing
   controller stage-attempt identity to the runtime for stronger cross-linking
   instead of relying only on a separate evidence-directory suffix.
4. Keep fresh preflight evidence out of semantic request/unit hashes. Test
   interrupted evaluation recovery and completed-zero preservation.
5. Document that adapter-owned judges/helpers need their own declared model
   checks. Fixing the evaluated role does not automatically cover arbitrary
   provider calls made inside a benchmark library.
6. Retain the identity limitation: exact proxy labels and negative controls
   verify observable behavior, not independent upstream execution identity.

These recommendations do not authorize future changes to judge models,
task selection, native scores, prompts, or the AHE evolution algorithm.

### Verification log

| Check | Outcome |
|---|---|
| Baseline source/call-site inspection | Confirmed missing evaluated preflight; no live requests |
| Approved fix implementation | Committed as `4a4e57b` before tests |
| Initial targeted run from checkout | 26 passed, 1 pre-existing cwd/cache assertion failed |
| Targeted run from isolated cwd | 27 passed; same four files, no source changes |
| Python lint | Passed for `runtime.py` and `test_runtime.py` |
| Final conformance/validation/live campaign | Operator-owned; not executed by the assistant |

The patch changes protected runner content. Existing completed quick-start
artifacts remain untouched, but their campaign identity must not be resumed
with the patched package. Use a fresh Harvey campaign directory; the original
commits remain the reproduction source for the old example.

## AHE-002: account for declared files inside registered skill folders

### Evidence and impact

NexAU `0.3.9` registers each skill directory as a sandbox upload asset
(`nexau/archs/main_sub/agent.py:587-600`), including its helper/resource files.
Native LAB skills include Python, JavaScript, and shell executables; for
example, `lab_core/harness/skills/pptx/scripts/generate_pptxgenjs.js`.

AHE's `src/ahe_runner/conformance.py:360-409` registers only each skill's
`SKILL.md`. Declaring executable/resources paths in the manifest's `skills`
component class therefore results in a "not runtime-attached" error, despite
their membership in the folder mapped by NexAU. Leaving them unclassified
would not satisfy the stated every-evolvable-file attachment contract.

### Operator decision: no initial skills

After this issue was explained, the operator requested a base harness without
any skills. The initial evaluated configuration will contain `skills: []`,
with no copied manuals, skill helper directories, or `LoadSkill` tool.
Ordinary workspace/document tools and installed document-processing libraries
remain available; they are not skill bundles.

This is an approved behavioral deviation from the native LAB baseline, which
eagerly loads its document manuals/scripts. Label the new seed as a
skills-free LAB-derived NexAU harness, not an exact skills-enabled LAB port.
Native tasks, scoring, and judge semantics are not changed by this decision.

The decision concerns the initial evaluated harness only. It does not remove
AHE meta-agent research procedures or automatically prohibit evolution from
adding skills later. If later evolution requires scripted skill assets,
revisit this recorded limitation rather than claiming it has been fixed.

### Upstream recommendation; no local repair

Consider recognizing explicitly declared regular-file assets beneath
registered skill folders as active skill dependencies. Preserve compatibility
with existing Markdown-only manifests; retain path/symlink checks and ensure
asset registration does not execute helper code on the host.

Add coverage for Python/JavaScript/shell resources, undeclared or unregistered
folders, malicious paths, and skill-asset changes in candidate attribution.
This recommendation is deferred; no conformance code or asset policy was
patched as part of the skills-free baseline decision.

### Campaign 9 update: LAB-exact harness with `workspace_seed/`

On 2026-10-14 the operator asked for a starting harness identical to LAB's:
its system prompt with the docx, pptx and xlsx manuals included, its seven
tools, and its skill scripts. The scripts must run inside the sandbox, as in
LAB, not on the host.

The Harvey adapter now copies any regular files under the harness's
`workspace_seed/` folder into `/workspace` of each trial's sandbox before the
agent starts. `harness_lab/workspace_seed/skills/{docx,pptx,xlsx}/scripts/`
holds LAB's scripts unchanged, so the manuals' paths
(`skills/docx/scripts/...`) work as in LAB. The manuals are part of
`systemprompt.md`, as LAB builds its prompt, so no NexAU skill is
registered and this issue does not arise.

Safeguards in `harvey_ahe.candidate_checks`: host Python and YAML may not
import or bind anything under `workspace_seed/`; `runpy` is banned; seed
Python is not scanned as host code because it only runs in the sandbox.
Symlinks and special files are not copied. Seed files count toward the
harness hash and size limits.

This is a Harvey-side workaround. The upstream recommendation above still
applies.

## LAB-001: make the filename helper's transport explicit

This issue belongs to the pinned **Harvey LAB library**, not to AHE Runner.
It is recorded here because it blocks the approved all-Copilot integration.

### Evidence, explanation, and authorization

Baseline LAB revision: `d7e56d4559ca8e3eee4db031c718e489b37d2220`,
package `lab-core 1.2.0`.

`lab_core/evaluation/scoring.py:268-326` first attempts exact filenames,
extension/unique-file checks, and fuzzy stem matches. Only unresolved
deliverables with remaining available files use the LLM helper.
`scoring.py:329-401` then constructs `anthropic.Anthropic()` directly, requests
`claude-sonnet-4-6` with a 1,024-token budget, and catches errors into `{}`.
Choosing Copilot rubric judges does not configure that helper.

A different filename does **not** automatically score zero: native
heuristics can recognize it without any helper. The problem is an unavailable
helper connection when a model-based match is actually needed. Suppressing
that connection failure can leave a real file unmatched and cause its
criteria to be judged with a missing-file marker.

The operator requested a simpler explanation before deciding, then
explicitly approved the proposed fix. No native scoring rule is being relaxed
and no deliverable is automatically awarded credit.

### Approved local changes

On the LAB branch:

- Add the optional keyword-only `deliverable_matcher` callback to
  `score_rubric`, `evaluate_run`, and `evaluate_run_dual`.
- Pass the original helper prompt and output schema into it after the native
  heuristic checks have failed.
- Require exactly the unresolved keys with string/null values. A valid null
  is still a valid no-match result, not an infrastructure error.
- Propagate injected callback failures before any criterion judge call. Do
  not replace them with an empty mapping.
- Keep the legacy Anthropic behavior when no callback is supplied.
- Remove a stale single-judge `scores.json` before a new grading attempt,
  complementing the existing removal of `scores_dual.json`; helper failure
  must not leave an old completion masquerading as a new result.
- Preserve criterion prompts, matching order, per-judge all-pass semantics,
  dual averaging, and standard/custom profile labeling.

On the AHE operator-project branch:

- Add protected `harvey-ahe-project/copilot_deliverable_matcher.py`.
- Require explicit exact model/transport settings; no live model ID or
  reasoning setting is guessed.
- Reuse AHE's recorded exact-model client and model preflight.
- Retain the native 1,024-token budget and temperature `0.0`; the current
  proxy does not forward temperature, and token usage remains unavailable.
- Lazily preflight only when the helper is used; retain each callback
  instance's proof and calls in a fresh attempt directory.
- Require catalog and negative-control checks, rather than allowing generic
  transport defaults to weaken this helper's observable-contract policy.
- Cache preflight failure without a hidden retry; validate every normal
  completion's observable model label.
- Validate JSON output against the native schema and record validation
  failures as incomplete grading, never a normal low benchmark score.
- Preserve the semantic matching prompt. Because the proxy ignores
  `response_format`, append only a JSON-format instruction plus the unchanged
  schema. Native grammar-constrained output is replaced by strict local
  validation, not silently claimed to be protocol-identical.

The helper module is **protected adapter support**, not an evaluated-agent
skill or an evolvable tool. It does not enable skills in C0.

### Files and reproduction boundary

LAB source/tests/docs:

- `lab_core/evaluation/scoring.py`
- `lab_core/evaluation/run_eval.py`
- `tests/test_scoring.py`
- `tests/test_eval_integration.py`
- `docs/copilot-server.md`

AHE project/tests:

- `harvey-ahe-project/copilot_deliverable_matcher.py`
- `harvey-ahe-project/requirements.txt`
- `tests/test_harvey_filename_matcher.py`

From the AHE checkout root, install the declared local LAB dependency with:

```bash
uv pip install --python .venv/bin/python -r harvey-ahe-project/requirements.txt
```

This is a local editable dependency, so reproduction must pin the resulting
LAB commit as well as the AHE commit. The package release version alone does
not identify the approved callback extension.

The LAB source, regression cases, and explanatory documentation were committed
before testing as `86678eba6fcbc6c03958742c6e9e75737514f513` on
`divyanshu_harvey_evolution`.

The new callback is callable as
`deliverable_matcher(prompt, output_schema) -> dict[str, str | None]`.
The eventual benchmark adapter must pass its instance explicitly to grading.
The native CLI's `--judges copilot/...` option alone still does not supply
this callback; no live CLI wiring is claimed here.

### Intended offline checks and initial status

Commit the source, regressions, and this explanation before these commands:

```bash
# LAB checkout:
.venv/bin/python -m pytest tests/test_scoring.py tests/test_eval_integration.py -q
# AHE checkout, after installing the declared operator dependency:
.venv/bin/python -m pytest tests/test_harvey_filename_matcher.py -q
```

Cases cover unchanged legacy prompt/schema, exact/fuzzy/extension precedence,
valid null matching, malformed callback output, timeout before criterion
grading, public single/dual API plumbing, stale-result suppression, raw
requested/returned labels, non-streaming settings, positive/negative controls,
proof preservation, and no hidden retries. HTTP traffic is fully mocked.

**Initial status: verification pending.** Source and regression coverage are
being committed before execution. Actual outcomes will be appended afterward
without amending the initial record.

The initial AHE helper/coverage record was committed as
`25ff53bff0a73f6d6c449a37a9e4efd04af5e382` before testing. The LAB targeted
run then passed **92 tests in 2.43 seconds**. Installing the declared local
LAB dependency in the AHE virtual environment completed successfully.
The first AHE lint check found an unused `ConfigurationError` test import:
the intended exact-budget regression had not been added. Three boundary
cases (1,023/1,025 tokens and non-native temperature) were added in a
separate correction commit before rerunning the checks. No framework repair
or model call was required for this test-coverage correction.

The first AHE callback run then passed **14 tests in 2.37 seconds**, and
both repositories' selected lint checks passed. A final contract review added
constructor rejection of weakened preflight settings and coverage of explicit
reasoning forwarding, before rerunning those regressions.

Cross-repository type checking first needed explicit source roots for the
editable AHE install. With those roots fixed, it identified an Anthropic SDK
overload diagnostic in the unchanged legacy helper call. The same diagnostic
was reproduced against the exact `d7e56d455` baseline source. It is not caused
by the callback patch and is bypassed by the injected Copilot path; no
unrelated legacy repair was made. The new callback and grading entry point
are checked separately without suppressing that diagnostic.

### Recommendations

For LAB maintainers, retain a client/callback injection seam for optional
grader-side model helpers, and document those calls separately from rubric
judges. Consider making helper failure a grading error by default in a
versioned protocol change; this local repair intentionally does not change
legacy behavior for unrelated users.

For AHE maintainers, operator guidance should enumerate all model-calling
roles, including benchmark-owned filename helpers, and require their settings,
proofs, and completion labels in protected evidence. A runner-owned evaluated
preflight alone does not cover arbitrary SDK calls inside benchmark libraries.

### Final offline verification and source pins

Verified source revisions:

- LAB: `86678eba6fcbc6c03958742c6e9e75737514f513`.
- AHE helper: `f0e3b0b55dae4732cc974e356a4b97e3438d18e1`.
- Earlier regression correction: `bfb1748`, committed before rerunning.

Results:

- LAB matching and grading API selectors: **92 passed**.
- Final AHE recorded-helper selector: **19 passed in 0.86 seconds**.
- Both repositories' selected lint checks: passed.
- New callback plus public grading entry point: **0 type errors and
  0 warnings**, with the editable source roots explicitly configured.
- Full legacy scoring file: the unchanged Anthropic SDK overload diagnostic
  remains, reproduced against the original baseline; it was not suppressed
  or repaired as part of this Copilot-use-case fix.

Integration environment: AHE Runner `0.3.0`, LAB Core `1.2.0`,
OpenAI SDK `3.29.0`, Anthropic SDK `1.13.0`, jsonschema `4.26.0`,
pytest `9.1.1`.

All model requests in regression coverage use `httpx.MockTransport`.
No live catalog request, positive/negative probe, grading, benchmark campaign,
or final AHE command was executed. Temporary type-check configuration and
baseline source snapshots are removed afterward; no additional repository
configuration or legacy source edits were made.

The helper hook and callback are ready. Wiring the callback into the main
Harvey `BenchmarkAdapterV2` remains part of the unfinished operator-project
implementation; this repair does not claim that the full integration is ready.

## AHE-003: prevent harness folders from shadowing protected modules

### Plain-language summary

During an evaluation, AHE puts the candidate harness folder at the front of
Python's import search list. A candidate that adds a top-level folder or file
named after a protected package can therefore replace that package with its
own copy for the rest of the evaluation, including grading (module shadowing).
For example, a top-level `lab_core/evaluation/scoring.py` could return perfect
scores without any judge call (reward hacking).

### Evidence

Inspected AHE revision: `7107b55e20f8e712e37a4635f9633cdadd77bf31`.

- `src/ahe_runner/runtime.py:258-259` enters `HarnessImportScope` when the
  evaluated-agent runtime is constructed. `runtime.py:388` leaves it only in
  `close()`.
- `src/ahe_runner/controller.py:337-350` constructs that runtime, then calls
  `adapter.evaluate(request, runtime)`. Grading runs inside `evaluate`, so it
  runs while the scope is active.
- `src/ahe_runner/harness_imports.py:23-31,47-67` inserts the harness root at
  `sys.path[0]`. It also removes already-loaded modules whose top-level name
  matches any root-level harness `.py` file or folder containing `.py` files.
  Later imports therefore resolve to the harness copy.
- `src/ahe_runner/harness.py:97-230` checks size, symlinks, required files,
  component coverage, agent references, conformance, compilation, task
  memorization, and runtime-owned files. It does not reject extra root-level
  Python folders that match no component glob.

An offline reproduction (temporary directory, no model calls) created
`harness/ahe_runner/errors.py`. Inside `HarnessImportScope`,
`importlib.import_module("ahe_runner.errors")` loaded the harness copy.
After the scope closed, the real module was restored.

### Impact on the Harvey integration

Harvey grading imports `lab_core.evaluation`, `harvey_ahe.grading`, AHE model
clients, and document libraries while the scope is active. A candidate could
replace any of them without editing protected files. It could also shadow
standard-library modules such as `json` that are imported lazily.

### Approved local repair (adapter level only)

The operator authorized an adapter-level guard. AHE source is unchanged.

The Harvey adapter's `validate_candidate` lists every top-level importable
name in the candidate harness: root files with a Python import suffix
(`importlib.machinery.all_suffixes()`), and root folders containing such a
file anywhere beneath them. It rejects a name when any of these holds:

- it is a standard-library or built-in module name;
- it is already loaded in `sys.modules`;
- `importlib.util.find_spec` resolves it outside the harness;
- it is one of the protected names `ahe_runner`, `benchmark_adapter`,
  `harvey_ahe`, `lab_core`, or `nexau`.

AHE calls `validate_candidate` before evaluating every evolved candidate
(`controller.py:597-640`). The seed's only top-level module is `tools`, which
collides with nothing in the integration environment. If an installed package
named `tools` ever appears, every candidate fails validation, which is the
safe outcome.

Files:

- `harvey-ahe-project/harvey_ahe/candidate_checks.py`: `top_level_import_names`
  and `module_shadowing_errors`. They inspect names only and import nothing;
  `find_spec` on a top-level name runs no module code.
- `tests/test_harvey_candidate_checks.py`: offline regressions covering a
  seed-shaped harness (allowed), standard-library, protected, installed,
  deeply nested, and compiled-extension names (rejected), unique helper
  packages and Python-free or hidden folders (allowed), plus the original
  reproduction showing the rejected layout really shadows `ahe_runner.errors`.

The adapter's `validate_candidate` reports these errors. Its implementation is
part of the still-unfinished Harvey adapter.

Residual limitation: a protected module that imports a package lazily, where
that package is not installed, cannot be detected by "already importable".
Inspection found no such optional import in LAB grading or sandbox code, AHE
Runner, or the Harvey adapter. The adapter imports its grading dependencies at
module load, before the scope opens. Candidate Python is still imported and
run on the host, an existing risk that AHE already documents; this guard
does not sandbox it.

### Upstream recommendations

1. In `validate_harness`, reject root-level importable names that collide with
   the standard library, built-ins, loaded modules, or installed distributions.
   Alternatively, only allow root-level Python under declared component folders.
2. Prefer isolating harness code under a unique package namespace, for
   example a generated `_ahe_harness_<digest>` loaded via `importlib`, instead
   of placing the harness root first on `sys.path`.
3. Consider running grading outside the import scope, or in a separate
   process, so evaluated code cannot affect protected scoring.
4. Add regressions that try to shadow a standard-library module, a runner
   module, and an adapter dependency.

### Offline verification

The guard and tests were committed in `15cbf91` before running checks.

- `tests/test_harvey_candidate_checks.py` plus the existing filename-helper
  suite: **32 passed** (13 new guard cases, 19 helper cases).
- Ruff on the first run: one import-order finding (`harvey_ahe` sorts as a
  third-party package) and one line-wrapping difference. Neither changed
  behavior. Both were fixed with `ruff check --fix` and `ruff format`;
  tests passed again afterwards.
- Pyright on the guard and its tests: **0 errors, 0 warnings**.

No model call, harness evaluation, or AHE CLI command was run.

## AHE-004: operator packages cannot depend on `ahe-runner` under uv

### Plain-language summary

AHE Runner lists NexAU as a direct git link rather than a released package
version (`pyproject.toml:12-13`: `nexau @ git+https://github.com/nex-agi/NexAU.git@v0.3.9`).
uv only accepts such links from packages that are installed directly. If an
operator's adapter package declares `ahe-runner==0.3.0` as a dependency, uv
stops with: "Package `nexau` was included as a URL dependency. URL dependencies
must be expressed as direct requirements or constraints."

### Local workaround (setup only)

The Harvey package (`harvey-ahe-project/pyproject.toml`) no longer restates
`ahe-runner`. AHE is the host environment, installed from this repository as
the README describes. The adapter records installed package versions at run
time instead. No AHE source was changed.

### Upstream recommendations

1. Publish NexAU to a package index and depend on a version pin, or
2. document that operator packages must not restate `ahe-runner`, or must add
   the NexAU URL as a direct requirement or constraint.

## AHE-005: no seam for a benchmark-owned execution environment

### Plain-language summary

LAB runs every agent command and document parse inside a per-run container
that has no network and sees only that task's documents (read-only) plus an
output folder. In AHE V2, editable harness tools run through NexAU's sandbox.
The runtime always makes that a local sandbox on the host, in the session's
working directory. An adapter can pass only text values to a session.

Example: an agent runs `cat ~/harvey-labs/tasks/<task>/task.json`. Inside
LAB's container, that file does not exist. Through the local sandbox, the
command succeeds and reveals the grading rubric. Network commands would also
succeed.

### Evidence

- `src/ahe_runner/runtime.py:635-640`: with no sandbox configured, or with a
  local one, the runtime installs `LocalSandboxConfig(work_dir=working_directory)`.
- `src/ahe_runner/runtime.py:43-50`: `AgentSessionRequest` carries only
  `session_id`, `artifact_dir`, `working_directory`, protected tool schemas, and
  `context: dict[str, str]`.
- `docs/ADAPTER_GUIDE.md:254-259`: context values are string-valued template
  variables; structured payloads must not be hidden in context.
- NexAU v0.3.9 ships only `local_sandbox.py` and `e2b_sandbox.py`
  (`nexau/archs/sandbox/`), with no container-runtime sandbox.
- LAB: `lab_core/sandbox/sandbox.py` starts Podman with `--network none`,
  read-only documents and writable output/workspace mounts.
  `lab_core/harness/tools.py` routes `bash` and binary `read` through it.

### Local repair (operator choice A, 2026-10-12)

- The adapter starts LAB's own `Sandbox` for each unit and wraps it in a
  narrow `harvey_ahe.workspace.WorkspaceHandle` (`exec`, path mapping, and
  writability checks).
- Routing (revised under AHE-006, 2026-10-13): the first version kept handles
  in a process-wide table and passed a token through the session `context`.
  AHE-006 showed that context can leak between concurrent sessions, so the
  table and token were removed. The handle is now bound to its own session
  through `AgentSessionRequest.session_resources`.
- Editable harness tools call `harvey_ahe.workspace.handle_for(agent_state)`.
- `validate_candidate` permits importing `harvey_ahe.workspace` and nothing
  else from `harvey_ahe`, and rejects imports of grading internals.
- The adapter revokes the handle (later calls fail) and stops the container in
  `finally`.

Residual risk: candidate Python is still imported on the host, as AHE already
documents for its trusted-local model. The container bounds agent commands,
not arbitrary edits to the tool code itself.

### Upstream recommendation

Let a V2 adapter provide a per-session execution capability for harness tools
without a side channel. Examples: an adapter-supplied NexAU sandbox
implementation or config, or a typed capability object reachable from
`AgentState`. The runtime would own its lifecycle and record it in
`runtime_manifest.json`.

## AHE-006: per-session resources must be bound by the runner, not looked up through context

### Plain-language summary

When several trials run at once, each trial's tools must reach only that
trial's sandbox. The right owner of that routing is the orchestrator: it
creates the session, so it should hand the session its own resources
(dependency injection). AHE V2 has no such hook. The only per-session channel
is `context`, a dictionary of strings. The first Harvey version (AHE-005)
therefore put a lookup token in `context`.

That channel is unsafe because NexAU keeps the "current context" in one
process-wide variable, not one per thread or per session. The root agent of a
session keeps its own copy, so root-agent tools are correct. A sub-agent,
however, is started with a copy of whatever is in the process-wide slot at that
moment. With two concurrent trials, A's sub-agent can receive B's context.

Example: trials A and B run in parallel and A's agent calls a helper
sub-agent. The helper gets B's token, reads B's documents, and writes into B's
output folder. Both trials are graded on mixed work and no error is raised.
The same applies to any context value, including the `working_directory` value
that AHE itself adds.

### Evidence (NexAU 0.3.9, AHE `harveybench`)

- `nexau/archs/main_sub/agent_context.py:156-160`: `_current_context` is a
  module-level global. `AgentContext.__enter__` and `__exit__` (lines 41-65)
  overwrite and restore it with no thread or session scoping.
- `nexau/archs/main_sub/execution/executor.py:972`: before dispatching
  sub-agents, the executor reads `get_context()` (the process-wide slot). The
  `copy_context()` snapshots at lines 1019 and 1042 do not help because the
  slot is not a `ContextVar`.
- `nexau/archs/main_sub/execution/subagent_manager.py:143-151`: that copy is
  passed to the child agent's `run`.
- `nexau/archs/main_sub/agent.py:801-907`: the root `AgentState` gets its own
  `AgentContext` object, which is why root-agent tools are unaffected.
- `src/ahe_runner/runtime.py` (before this repair): `AgentSessionRequest` had
  no field other than `context` for per-session values.
- Offline reproduction (no model calls): thread A enters
  `AgentContext({"harvey_workspace": "token-A"})`, thread B then enters
  `AgentContext({"harvey_workspace": "token-B"})`, and A's
  `get_context().context["harvey_workspace"]` returns `token-B`.

### Impact on the Harvey integration

The seed harness has no sub-agents, so the first version was not yet affected.
Evolution may add sub-agents, after which concurrent evaluation
(`evaluation.max_concurrency > 1`) could silently mix trials.

### Approved local repair (operator decision, 2026-10-13)

The runner now owns the binding.

- `src/ahe_runner/runtime.py`: `AgentSessionRequest.session_resources:
  dict[str, Any]`, default empty. Keys must be public Python identifiers and
  may not be NexAU-reserved storage keys (`tracer`, `skill_registry`,
  `parallel_execution_id`). When non-empty, `open_session` creates a fresh
  NexAU `GlobalStorage` holding the resources and passes it to
  `Agent(config=..., global_storage=...)`. It then checks that initialization
  did not replace any resource. With no resources, construction is unchanged.
- Why `GlobalStorage`: NexAU creates one per root `Agent`. The executor and
  sub-agent manager receive that same object, and every sub-agent is
  constructed with it (`subagent_manager.py:123,133`). It is therefore
  session-scoped, shared down the session's agent tree, and unreachable from
  other sessions. NexAU skips non-serializable values when persisting session
  state, so live objects are never written out.
- Harvey adapter: no process-wide table and no token. It creates the
  `WorkspaceHandle`, passes `session_resources={"harvey_workspace": handle}`,
  and calls `handle.revoke()` before stopping the container. Tools call
  `harvey_ahe.workspace.handle_for(agent_state)`, which reads the session's
  storage and fails closed if the handle is missing or revoked.
- `docs/ADAPTER_GUIDE.md` documents the field and warns against routing
  through `context`.
- Regression tests in `tests/test_runtime.py`:
  - two concurrent sessions each see only their own resource;
  - each session's sub-agent manager holds the same storage object;
  - reserved and invalid keys are rejected.

Residual risk: candidate tool code still runs in the host Python process
(AHE-005), so deliberately malicious code could search process memory. This
repair removes accidental cross-trial routing; it is not a security boundary
against hostile harness code.

### Offline verification (2026-10-13)

- `tests/test_runtime.py` (no model calls): 16 passed. This includes
  `test_session_resources_are_bound_to_their_own_session` and the
  reserved/invalid-key cases of
  `test_session_resources_reject_reserved_or_invalid_keys`.
- First run from the repository root: 15 passed, 1 failed
  (`test_runtime_intercepts_protected_actions_and_preserves_sessions`). The
  cause was a stray `.skills/` left in the root by the quickstart, not this
  change. The same file passed 16/16 from a clean directory; the cause was
  then recorded as AHE-008.
- Ruff is clean on the changed files. Pyright has one error on
  `runtime.py` (`on_close=self._sessions.pop`), which is identical on the
  unmodified file.

### Upstream recommendations

1. Add a supported per-session resource or capability seam to
   `AgentSessionRequest`, for example `session_resources` or a typed capability
   object. The runtime should own it, record resource keys and types in
   `runtime_manifest.json`, and document the lifecycle (who closes it, and
   when). Benchmarks with containers, simulators, or databases all need this.
2. State in `ADAPTER_GUIDE.md` that session `context` must not be used for
   routing while NexAU's context is process-global.
3. Report upstream to NexAU that `_current_context` should be a `ContextVar`
   (or that sub-agents should inherit the parent `AgentState.context` instead
   of the global). Until then, AHE should pass the parent state's context
   explicitly to sub-agents or document the limitation.
4. Add a concurrency regression test that runs two sessions with sub-agents
   in parallel and checks that context values and resources stay separate.

## AHE-007: static import scan splits `from pkg import name` into characters

### Plain-language summary

To learn which harness files a tool depends on, conformance parses the tool's
imports. For `from pkg import name` it builds the module path from the
characters of `name`, so `from tools import helpers` is recorded as
`tools.h.e.l.p.e.r.s`. The real helper file is then not found by the static
scan.

### Evidence

- `src/ahe_runner/conformance.py:572`, in `_local_import_names` (from line 545):
  `names.add(".".join([*base_parts, *alias.name]))`. Here `alias.name` is a
  string, so unpacking it yields single characters. Intended:
  `[*base_parts, alias.name]`.

### Impact

Helpers imported at module load are still found by the dynamic `sys.modules`
comparison in `_resolve_import_contract`. A helper imported only lazily, as
`from pkg import module` inside a function, would be missed and reported as a
declared but unattached tool implementation (a false conformance failure).
The Harvey seed imports helpers as `from tools._common import ...`, which
the scan handles correctly.

### Recommendation; no local repair

Replace `*alias.name` with `alias.name` and add a conformance test with a
lazily imported `from tools import helper` dependency.

## AHE-008: meta-agent skill copies land in the operator's current directory

### Plain-language summary

When NexAU starts an agent's sandbox, it copies the agent's skills into a
`.skills/` folder inside the sandbox work folder (`work_dir`). AHE never gives
its meta-agents a work folder, so NexAU uses its default: the directory AHE was
launched from. Only the Evolve agent has skills (the two Explore skills).

AHE marks those skill copies read-only, and the copy keeps that setting. On the
next Evolve sandbox start from the same directory, NexAU first deletes the old
copy, which fails with `Permission denied`. NexAU starts the sandbox lazily on
every tool call, so each of that Evolve agent's tool calls then fails.

Example: the quickstart was launched from the `AHE_Harvey` repository root.
Its single Evolve round left `AHE_Harvey/.skills/source-exploration/` and
`.../web-research/`, both read-only. A second Evolve round, or a second campaign
launched from the same folder, would have failed. The leftover folder also made
`tests/test_runtime.py::test_runtime_intercepts_protected_actions_and_preserves_sessions`
fail when run from the repository root. That test asserts that the evaluated
runtime leaves no `.skills` in the current directory.

### Evidence (NexAU 0.3.9, AHE `harveybench`)

- `nexau/archs/sandbox/base_sandbox.py:80`: the `LocalSandboxConfig.work_dir`
  default is `SANDBOX_WORK_DIR` or `os.getcwd()`.
- `nexau/archs/main_sub/agent.py:587-593`: each skill folder is mapped to
  `<work_dir>/.skills/<name>` and queued for upload.
- `nexau/archs/sandbox/local_sandbox.py:1209-1242`: `upload_directory` runs
  `shutil.rmtree(dst)` when the destination exists, then `shutil.copytree`,
  which preserves the source's read-only mode.
- `src/ahe_runner/meta/nexau_backend.py`: `_copy_read_only_skill` makes the
  per-round Explore skill copies read-only. Before this repair, `_run_agent`
  built the meta `AgentConfig` without a `sandbox_config`. By contrast,
  `src/ahe_runner/runtime.py:668-672` already pins the evaluated agent's
  `work_dir` to its session workspace.
- `nexau/archs/main_sub/execution/tool_executor.py:97`: every tool call calls
  `agent_state.get_sandbox()`, which starts the sandbox (and uploads skills) if
  it is not yet running.
- Offline reproduction: copy a `0o555` folder with `shutil.copytree`, then
  repeat NexAU's `rmtree` and `copytree`. The second step raises
  `PermissionError: [Errno 13] Permission denied: 'SKILL.md'`.

### Impact

- Multi-round campaigns, and campaigns relaunched from the same directory, can
  break in the Evolve phase. The Harvey campaign has several rounds.
- Two campaigns launched from the same directory share and overwrite each
  other's Explore skill copies.
- Each campaign leaves read-only folders in the operator's directory.

### Approved local repair (operator decision, 2026-10-13)

- `src/ahe_runner/meta/nexau_backend.py` `_run_agent`: after loading the
  generated meta config, it sets
  `config.sandbox_config = LocalSandboxConfig(work_dir=<runtime_dir>/sandbox)`.
  A leftover folder from an earlier attempt of the same role is made writable
  and removed first, as `_copy_read_only_skill` already does. AHE meta tools do
  not use the NexAU sandbox, so only the skill-copy location changes.
- Regression test
  `tests/test_meta_resources.py::test_meta_agent_skill_copies_stay_in_private_runtime_sandbox`.
  It starts a real NexAU sandbox twice (no model calls) from a separate current
  directory with a read-only skill. It checks that the copies land in
  `<runtime_dir>/sandbox/.skills/` and that nothing is written to the current
  directory.
- The leftover `AHE_Harvey/.skills/` from the quickstart (git-ignored) was
  deleted with the operator's approval.

### Offline verification (2026-10-13)

- `tests/test_runtime.py` and `tests/test_meta_resources.py`, run from the
  repository root after the leftover folder was deleted: 28 passed. No
  `.skills/` reappeared in the root.
- Negative check: with the pre-fix `nexau_backend.py` temporarily restored,
  the new regression test fails at its first check, because the skill copy was
  not placed in `<runtime_dir>/sandbox`. The fixed file was then restored.
- Ruff is clean. Pyright reports 6 errors in `nexau_backend.py`, the same 6
  as on the unmodified file.

### Upstream recommendations

1. Give every meta-agent an explicit sandbox `work_dir` inside its run
   artifacts, as the evaluated runtime already does.
2. Never depend on the process current directory in library code; consider
   failing fast when a NexAU config has skills but no `sandbox_config`.
3. Add a test that runs two Evolve sandbox starts in one campaign and checks
   the launch directory stays clean.

## AHE-009: project role supplements are rendered as Jinja templates

### Plain-language summary

A project can add its own guidance to each meta-agent's prompt (a "role
supplement"). AHE glues that text under the protected core prompt, saves the
result as a file, and gives NexAU the file as the meta-agent's system prompt.
NexAU treats a file prompt as a fill-in-the-blanks template (a Jinja
template). So if the supplement contains template symbols, such as two
opening curly braces, NexAU tries to fill them in. Unbalanced symbols crash
the meta-agent on its first model call; balanced ones silently change the
text. AHE checks only that the supplement file exists when it loads the
config. The failure therefore appears mid-campaign, after evaluation spend,
rather than at `ahe validate`.

Example: the first Harvey `evolve.md` draft told the Evolve Agent not to put
Jinja markup in `systemprompt.md`, and quoted the markup literally. Rendering
the combined Evolve prompt failed with
`ValueError: Error processing Jinja template ...: unexpected char '`'`.

### Evidence (NexAU 0.3.9, AHE `harveybench`)

- `src/ahe_runner/meta/nexau_backend.py:761-782` (`_resolved_prompt_text`):
  the supplement text is appended to the core prompt without escaping.
- `src/ahe_runner/meta/nexau_backend.py:624-635`: the combined text is written
  to `<runtime_dir>/system_prompt.md` and configured with
  `system_prompt_type: file`.
- `nexau/archs/main_sub/prompt_builder.py:148-153`: the base system prompt is
  built with `create_dynamic_prompt(..., template_type=system_prompt_type)`.
- `nexau/archs/main_sub/prompt_handler.py:211-213`: the `file` type is
  rendered by `_process_jinja_prompt`.
- `src/ahe_runner/config.py:230-242`: role supplements are resolved as
  declared files only; their content is not checked.

### Local handling (no AHE patch)

- The Harvey supplements describe Jinja markup in words instead of quoting it.
- Offline check (2026-10-13): each core prompt plus its Harvey supplement,
  joined exactly as `_resolved_prompt_text` does, was rendered through NexAU's
  `PromptHandler._process_jinja_prompt`. All four roles (source, web,
  debugger, evolve) rendered byte-identical to the input. Before the wording
  fix, source and evolve failed.

### Upstream recommendations

1. Treat supplements as literal text. For example, wrap them in a Jinja
   `raw` block, or escape them before they are joined to the core prompt.
2. Alternatively, render every resolved meta prompt during `ahe validate`
   and `ahe conformance`, failing fast and also catching silent changes.
3. Document in the role-supplement docs that supplement text is otherwise
   rendered as a template.

## AHE-010: `search_text` has no total output budget

### Plain-language summary

Meta-agents search files with `search_text`. AHE limits each search to 300
matching lines and cuts each line to its first 2,000 characters, but it does
not limit the total size of one result. That allows about 600,000 characters
per call. The Evolve Agent can call the tool several times in one turn.

In round 2 of the first live Harvey campaign, Evolve's third turn ran six
searches, one per training task key, over the `debugger` root. That root
also holds every debugger's raw model-call logs
(`tasks/*/runtime/model_calls/*/request.json` and `response.json`): 172
files, about 34 MB, mostly single-line JSON. Each log repeats the task key,
so five searches hit the 300-match limit. Every match was a 2,000-character
slice of JSON.

The combined results came to 617,681 tokens; the context limit is 200,000.
The compaction middleware keeps the latest turn intact, so it could not
shrink the context. It correctly refused to call the model (fail-closed),
and the stage failed. Round 1's Evolve had not made these searches, so the
failure depends on the model's choices and can occur on any benchmark whose
debugger traces are long.

### Evidence (AHE `harveybench` at `50cfb70`, NexAU 0.3.9)

- Campaign `harvey-lab-first-campaign`, stage `round.2.evolve`, attempt 1,
  2026-10-13 08:00:16-08:00:55 UTC. Error: `meta-agent context remains above
  the configured compaction target after preserving the latest iteration:
  617681 > 144000`, then `Prompt tokens (619450) exceed max_context_tokens
  (200000)`.
- `evolve/C2/attempt-0001/runtime/model_calls/call-0003/response.json`: six
  `search_text` calls on root `debugger`, `relative_path: "tasks"`,
  `file_glob: "**/*.json"`, querying the six task keys.
- A `grep` count of the same queries over `debugger/C2/tasks` found
  127, 589, 408, 317, 690, and 281 matching lines.
- `src/ahe_runner/meta/tools.py`: `_MAX_SEARCH_MATCHES = 300` and
  `line[:2_000]` are the only bounds. In contrast, `read_file` has
  `max_bytes` (default 64,000, maximum 160,000).
- `src/ahe_runner/meta/nexau_backend.py:288-298`: Evolve's `debugger` root is
  the whole debugger output folder, including `runtime/model_calls`.
- `src/ahe_runner/meta/middleware.py:58-95`: compaction keeps the latest
  iteration and raises when it alone exceeds the target.

### Branch-local repair (approved 2026-10-13)

In `src/ahe_runner/meta/tools.py`, `search_text` now:

1. Stops adding matches once the serialized matches would exceed 32,000
   characters (`_MAX_SEARCH_OUTPUT_CHARS`). It returns `truncated: true`,
   `truncation_reason: "output_char_limit"`, `output_chars`,
   `output_char_limit`, and a hint to narrow the search or use `read_file`.
   Six parallel calls are now bounded to about 200,000 characters, roughly
   50,000 tokens.
2. Returns a snippet of at most 400 characters, starting 150 characters
   before the match, plus `column` and `snippet_start_column`. The old
   first-2,000-characters slice often missed the match in long JSON lines.
   Lines of up to 400 characters are still returned whole, unchanged.
3. Records a debugger line as read, so that the line can be cited, only when
   the whole line was returned. Before, a line cut at 2,000 characters still
   counted as fully read. With shorter snippets, keeping that would have
   weakened the debugger's rule that it may cite only content it has seen.
   To cite a long line, the debugger now reads it with `read_file`.
4. Keeps the 300-match limit, now reported as
   `truncation_reason: "match_limit"`.

`meta/tool_descriptions/search_text.tool.yaml` describes the bound, the
snippets, and what to do when truncated. Regression test:
`tests/test_meta_resources.py::test_search_text_bounds_total_output_and_returns_match_snippets`.

Both files are fingerprinted into the campaign identity (see AHE-011), so
this repair needs a new campaign.

Offline verification (2026-10-13, after proposal commit `0c4fca0`):

- The first run of the new test failed: it read the debugger-access record
  under an unnamespaced storage key. It now uses `tools._debugger_accesses`
  and passes.
- Results: 13 of 13 in `tests/test_meta_resources.py`; the full suite, 179
  of 179; ruff check and format clean; pyright 0 errors in `meta/tools.py`
  and the new test. The 8 other errors are older typing issues in existing
  test code.
- Read-only replay: the six failing Evolve searches were re-run with the
  patched `search_text` over the real
  `harvey-lab-first-campaign/debugger/C2`. Each returned 31,818 to 32,437
  characters (63 to 79 snippets, `output_char_limit`), 192,944 characters in
  total, about 48,000 tokens. Before the repair the same six calls produced
  about 617,681 tokens.

### Upstream recommendations

1. Give every meta tool a total output budget, not only per-item limits.
2. Add a per-turn budget across parallel tool calls, or have the compaction
   middleware truncate an oversized latest tool result with an explicit
   marker instead of failing the stage.
3. Consider hiding or summarizing the debugger's raw `runtime/model_calls`
   from Evolve's `debugger` root. Each request log repeats the whole growing
   conversation, so its size grows quadratically with debugger turns. The
   submitted diagnoses and `overview.md` are the intended Evolve inputs.
4. Return match snippets around the hit, with columns, for long lines.

### Follow-up observed in campaign 3 (2026-10-13)

With the cap in place, two of six round-1 debuggers had a diagnosis
rejected once: "debugger citations were not covered by nonempty bounded
reads or search hits: raw_evaluation:units/.../trace.jsonl#lines=10-10".
The cited `trace.jsonl` lines are single JSON records of 87-172 KB. The
debugger had seen them only through capped search snippets, which are
deliberately not recorded as coverage, and through `read_file` calls cut
off by `max_bytes`, which record only a byte range. Both debuggers
recovered on their own: they read a byte range, cited it, and were
accepted. The cost was one extra rejection and a few reads.

This is the intended conservative behavior. Two further upstream
improvements would avoid the extra turn:
1. Record each capped search snippet as byte-range coverage of exactly the
   bytes returned. The snippet already carries `snippet_start_column`, so
   the byte offsets are known. Accept a citation of that byte range.
2. When a line citation is rejected because the line is longer than any
   read returned, say so in the error ("line 10 is 92,605 bytes; cite a
   byte range you read"), rather than giving only the generic coverage
   message.

## AHE-011: blocked meta stages cannot be retried or repaired in place

### Plain-language summary

When the AHE-010 failure happened, AHE marked `round.2.evolve` as
**blocked** after one of its three allowed attempts. AHE retries a stage only
if the error is classed as an infrastructure failure
(`InfrastructureError`). A context overflow is not, so the stage was blocked.
No `ahe` command unblocks a stage, so the remaining attempts cannot be used.
The only remaining options are to edit `stage.json` by hand or to start
over.

A repair can't be continued in place either. The campaign manifest
fingerprints AHE's package, core prompts, tool descriptions, `tools.py`,
config, and role supplements. On the next command, any change raises
`campaign identity changed in protected inputs [...]; use a new artifacts
directory`. That protects reproducibility. Combined with the block, though,
one meta-agent failure that needs a code fix throws away all completed
evaluation work. Here that was round 1, about 46 minutes of live runs.

### Evidence

- `src/ahe_runner/state.py` (`StageStore.run`): `can_retry` requires
  `isinstance(exc, InfrastructureError)`; otherwise
  `stage["status"] = "blocked"`, and a blocked stage raises
  `StageBlockedError` on every later call.
- `src/ahe_runner/cli.py`: the commands are `init-project`, `conformance`,
  `validate`, `run`, `resume`, `status`, `close-evolution`,
  `run-validation`, `run-final-test`, and `run-round`. None of them unblocks
  or retries a stage.
- `src/ahe_runner/controller.py:830-905` (campaign manifest): the
  `protected_runner_resources` fingerprints, and the `IntegrityError` above
  on any change.
- `harvey-lab-first-campaign/stages/round.2.evolve/stage.json`:
  `status: blocked`, one `failed` attempt, `maximum_stage_attempts: 3`.

### Local handling (no AHE patch)

The operator chose a new campaign that includes the AHE-010 repair:
`campaign.name: harvey-lab-campaign-2-search-cap`, with its own artifacts
folder. The first campaign's artifacts are kept unchanged as evidence of the
failure. Its round 1 results are recorded in
`harvey-ahe-project/HARVEY_AHE_RUNBOOK.md` section 5a. Considered and not
chosen:

- Hand-resetting the blocked stage and retrying with identical code. The
  same overflow could recur.
- Patching and rewriting the manifest fingerprint. That defeats the
  integrity guard.

### Upstream recommendations

1. Add an explicit operator command such as `ahe retry-stage <name>`. It
   would record the reason, keep failed attempts, and respect
   `maximum_stage_attempts`.
2. Classify context-budget overflows of meta-agents as retryable, or as a
   distinct "repairable" class, rather than final.
3. Support a documented "runner patch" path. For example, record a new
   runner fingerprint as a manifest amendment that later reports show,
   instead of forcing a full restart for a fix that does not change the
   evaluation contract.

## AHE-012: independent evaluations and exploration run sequentially

### Plain-language summary

This is a speed issue, not a correctness bug. Several AHE steps do not
depend on each other, yet the controller runs them one after another. The
allowed concurrency is mostly left idle:

1. **Validation.** `run_validation` evaluates C0, C1, ..., Cn on the
   validation split in a plain loop, one checkpoint at a time. With the
   Harvey pilot split (3 validation tasks, 1 trial), each evaluation has
   only 3 units, while `evaluation.max_concurrency` is 20. Up to 12 units
   could run at once; instead there are four waits of about 11 minutes each.
2. **Final test.** `run_final_test` evaluates C0 and the selected checkpoint
   one after another, though they are independent.
3. **Exploration.** In round 1, `run_round` finishes the C0 training
   evaluation before exploration starts. Exploration reads only the seed
   harness and the prompt context, not C0's results, so it could overlap
   that evaluation. In the first Harvey campaign that would have saved
   about 9 minutes.

Each evaluation is already checkpointed as its own stage, with its own
semantic inputs and artifacts, so running them at the same time would not
change any result.

### Evidence (AHE `harveybench`, first Harvey campaign, round 1)

- `src/ahe_runner/controller.py` `run_validation`: a `for index in
  range(rounds + 1)` loop calling `_ensure_evaluation(index,
  SplitName.VALIDATION)`. `run_final_test`: a dict comprehension calling
  `_ensure_evaluation` for `{0, selected}` in turn.
- `src/ahe_runner/controller.py` `run_round`: `_ensure_training_evaluation(0)`
  and `_ensure_evidence(0)` come before `_ensure_exploration()`. The
  exploration stage's semantic inputs are `baseline_harness_sha256`,
  `meta_model`, `source_enabled`, `web_enabled`, and `prompt_context`; there
  are no evaluation inputs.
- Measured stage times (2026-10-13): `evaluation.C0.train` 07:02:44-07:13:53
  (669 s), then `exploration` 07:13:54-07:22:50 (536 s). Per-trial grading
  took 6.3-9.0 minutes of each 11-14-minute training evaluation (details in
  `harvey-ahe-project/HARVEY_AHE_RUNBOOK.md` section 5b).

### Local handling (no AHE patch)

The largest Harvey-side cost was in the adapter's own configuration:
`grading_parallel: 1` graded each judge's rubric criteria one at a time.
From campaign 3 on, `ahe.yaml` uses 6, which is LAB's own `run_eval.py`
default. The verdicts are independent calls returned in rubric order; an
offline test checks that serial and parallel grading give identical rows.
This adapter-side change does not address AHE-012 itself.

### Upstream recommendations

1. Run validation evaluations of all checkpoints in one concurrent batch,
   sharing one `evaluation.max_concurrency` budget across checkpoints. Keep
   one stage per checkpoint, so resume semantics do not change.
2. Run the final-test evaluations of C0 and the selected checkpoint the
   same way.
3. Start exploration in parallel with the C0 training evaluation. It depends
   only on the frozen baseline.
4. Optionally, report per-stage wall-clock times in `ahe status`, so
   operators can see where time goes without reading artifact timestamps.

## AHE-013: no tool-output safety net or structured reads of long trace lines

### Plain-language summary

The original AHE evolve agent puts every tool result except shell output
through NexAU's `LongToolOutputMiddleware`. Any result over 10,000
characters is cut down to its beginning and end, the full text is saved to a
file, and the agent is told where to find it. The original agent also has
`run_shell_command`, so it can use `jq`, `sed`, or Python to pull out one
field of a huge JSON trace line.

ahe-runner has neither. Each tool must limit its own output, and the meta
agents have no shell. When one tool did not limit itself (`search_text`,
AHE-010), nothing else caught it and the stage was blocked. Long trace lines
can be read only as raw byte ranges. That works, but it costs extra turns
and context, because the agent cannot ask for just "the tool call in record
10".

This is not a bug in the current code; the campaign runs correctly. It is a
gap compared with the original method, and it showed up in practice twice:
the AHE-010 blockage, and the citation retries in the AHE-010 follow-up.

### Evidence

- Original AHE, `agents/evolve_agent/evolve_agent.yaml` (main branch),
  checked 2026-10-13. It configures
  `middleware.long_tool_output:LongToolOutputMiddleware` with
  `max_output_chars: 10000`, `head_chars`/`tail_chars: 5000`,
  `head_lines: 50`, `tail_lines: 30`, and
  `bypass_tool_names: [run_shell_command]`, followed by context compaction.
  Its tools include `run_shell_command`. Its `middleware/long_tool_output.py`
  saves the full output under `/tmp/nexau_tool_outputs` through the sandbox
  API and adds a "use the read file tool" hint.
- The NexAU version installed with ahe-runner ships the same middleware as
  `nexau.archs.main_sub.execution.middleware.long_tool_output`.
- ahe-runner, `src/ahe_runner/meta/nexau_backend.py`: the generated meta
  agent config lists only
  `ahe_runner.meta.middleware:MetaContextCompactionMiddleware`. This block
  is identical on `origin/main` and `harveybench`, and no commit in the
  history mentions `LongToolOutput`.
- ahe-runner, `docs/AHE_COMPLIANCE_AUDIT.md`: the design intent is
  "bounded line/byte reads, indexed search, and honest truncation metadata;
  limit returned content". That is a per-tool rule with no shared backstop.
- Evolve tool list (campaign 3, round 1, first request): `list_files`,
  `read_file`, `search_text`, the write tools, the git and checkpoint tools,
  `validate_candidate`, `complete_task`, and `LoadSkill`. There is no shell
  and no structured or JSON reader.

### Why the original middleware cannot simply be added

This is our reading of the code; the maintainers have not confirmed it.
1. **Read roots.** ahe-runner's `read_file` accepts only named read roots.
   A full output saved to `/tmp/nexau_tool_outputs` is outside all of them,
   so the hint would point to a file the agent cannot open.
2. **Citation honesty.** ahe-runner's tools record debugger "accesses" when
   they return content, and `submit_debugger_diagnosis` accepts only
   citations that those accesses cover. A middleware that shortens the
   output afterwards would leave accesses recorded for text the model never
   saw.
3. **Artifact location.** Writes to a shared `/tmp` folder would sit outside
   the campaign artifacts, which raises the same issue as AHE-008.

### Upstream recommendations

1. Add a protected backstop middleware for all meta agents. It should cap
   any single tool result, and the total of one turn's parallel results,
   below the compaction target, and fail clearly or shorten clearly rather
   than letting one turn exceed the context limit. Save full outputs inside
   the attempt's runtime folder, expose that folder as a read root, and have
   tools record accesses only for the content actually returned after
   shortening.
2. Add a read-only structured reader for JSON and JSONL artifacts, for
   example `read_json(root, path, line, pointer, max_chars)`, using a JSON
   pointer such as `/messages/3/tool_calls/0`. It would record access to
   exactly what it returns, and debugger citations could reference
   `#line=10&pointer=/...`. This gives the useful part of the original
   shell-plus-`jq` workflow without giving meta agents a shell.
3. Alternatively, store each trace message on its own line, or add an
   index of byte offsets per message, so that line and byte reads line up
   with meaningful units.
4. Document the deliberate differences from the original AHE meta-agent
   configuration: no shell, no web tools for evolve, and no
   `LongToolOutputMiddleware`. Users comparing the two then do not have to
   rediscover them.

### Local handling

No AHE patch; this branch keeps ahe-runner's meta-agent configuration.
AHE-010's per-tool cap prevents the observed overflow, and byte-range
`read_file` was enough for the debuggers to recover (campaign 3, round 1).

Update (AHE-014): campaign 4 adds a branch-local safety net for Evolve and
both Explore agents. It does not cover the Debugger. See AHE-014.

## AHE-014: `list_files` has no output-size budget

### Plain-language summary

`list_files` stops after 5,000 entries but does not limit how many
characters it returns. In campaign 3, round 1, Evolve listed the whole
`raw_evaluation` root recursively. The single answer was 274,020 characters,
about a third of the context limit. The stage did not fail, because context
compaction later removed it, but one or two similar calls in the same turn
would have repeated the AHE-010 blockage. This is the concrete case behind
the general gap recorded in AHE-013.

### Evidence

Campaign 3 (`harvey-lab-campaign-3-grading-parallel`), measured as the
JSON-encoded tool result in each Evolve `conversation_trace.json`:

| Evolve run | Results over 30,000 characters |
|---|---|
| C1 | `list_files` 274,020 (`root=raw_evaluation`, `recursive=true`); `list_files` 52,917; `LoadSkill` 44,225 and 36,596 |
| C2 | `LoadSkill` 44,225 and 36,596; `list_files` 42,384 |
| C3 | `LoadSkill` 44,225 and 36,596; `list_files` 42,386 |

`LoadSkill` returns the two Explore skills, which the Evolve agent is meant
to read in full.

### Approved local repair (option 2b)

Approved by the operator on 2026-10-13. A protected middleware is added to
the Evolve and both Explore agents (not the Debugger). After each tool call:

1. If the result has at most 64,000 characters, nothing changes.
2. Otherwise the agent sees the first 24,000 and last 8,000 characters, plus
   a note with the original size and where the full result is saved.
3. The full result is saved in the run's own folder,
   `<runtime_dir>/tool_outputs/`, reformatted one JSON item per line when it
   is JSON, so it can be read in line ranges.
4. That folder is added as a read-only root named `tool_outputs`.
   `read_file`, `search_text`, and `list_files` can open it; the write tools
   cannot.
5. `LoadSkill` and the finishing tool (`complete_task`) are never shortened.
   Other fields of the tool result, such as NexAU's stop-tool flag, are kept.

Why not the Debugger: its tools record which parts of an artifact they
returned, and `submit_debugger_diagnosis` accepts only citations that those
records cover. If the safety net cut a result afterwards, the Debugger could
cite text it never saw. Its tools keep their own limits (`read_file` up to
160,000 bytes, `search_text` about 32,000 characters, `list_files` 5,000
entries).

Runner code is part of the campaign fingerprint, so campaign 4 is renamed
`harvey-lab-campaign-4-gp50-output-backstop` before it starts. It was later
renamed again, to `harvey-lab-campaign-4-judge-cap-output-backstop`, when a
failed judge load probe replaced the `grading_parallel` 50 plan with a shared
judge-call cap (runbook section 5d).

### Upstream recommendations

1. Give `list_files` its own character budget and a `max_depth` argument,
   like the AHE-010 `search_text` cap.
2. Adopt a protected safety net like this one for all meta agents, and make
   the Debugger's access records reflect only the text actually returned
   after shortening (see AHE-013).

### Local result

- `src/ahe_runner/meta/middleware.py`: new `MetaToolOutputBackstopMiddleware`
  (`after_tool` hook). Saved copies are named
  `<sequence>-<tool>-<call id>.txt` and never overwrite an earlier copy. If a
  copy cannot be saved, the result is still shortened and the note says so.
  If an escaped JSON result fits within the limit once decoded, the decoded
  form is returned whole and nothing is saved.
- `src/ahe_runner/meta/nexau_backend.py`: `_run_agent(...,
  tool_output_backstop=True)` creates `<runtime_dir>/tool_outputs/`, adds it
  to a copy of `meta_read_roots` (not to `meta_write_roots`), rejects a
  caller root with the same name, and places the middleware before context
  compaction. `run_evolve` and `_ensure_explorer` turn it on; the Debugger
  does not.
- Tests: 7 middleware cases in `tests/test_meta_middleware.py` and 2 wiring
  cases in `tests/test_meta_resources.py`. They cover passthrough at the
  limit, the head, tail, and note, the readable saved copy, the bypassed
  tools, unique names after a restart, a failed save, decoded JSON that fits,
  the settings check, the read-only root and path escape, Debugger-style runs
  without the safety net, and the reserved root name.
- Offline checks on 2026-10-13: the full suite passed 191/191 (182 before).
  ruff check and format are clean. pyright reports no new errors; the 6
  errors it lists in `nexau_backend.py` were already there before this change.
  Replaying campaign 3's 227,056-character listing (274,020 characters as
  stored in the trace) gave the agent a 32,734-character result and a
  7,826-line saved copy. An ad-hoc run through NexAU's own `MiddlewareManager`
  returned the shortened result as a dict, as the tool executor expects.

## AHE-015: no endpoint-wide queue for model calls

### Plain-language summary

The evaluated agent, the benchmark's judges, and the meta agents all call the
same model endpoint, but nothing in AHE limits how many of those calls are in
flight at once. `meta.max_concurrent_model_calls` covers only meta agents.
When too many calls start together, the GitHub Copilot service refuses the
extra ones instead of letting them wait. The GH Copilot Server extension
retries twice and then returns HTTP 500 with an empty message, so the caller
fails. For the evaluated agent that throws away the whole trial, which is
then re-run.

### Evidence

- Judge load probe, 2026-10-13 14:10 UTC (runbook section 5d): 120 judge
  calls sent at once, 90 succeeded. The Copilot Chat log shows upstream HTTP
  429 `ChatRateLimited`; each wave accepted about 30 new requests across both
  models together.
- The same probe at 24 calls at once passed 24/24.
- The Copilot Chat logs from campaigns 1-3 contain no rate-limit lines.

### Approved local repair

Approved by the operator on 2026-10-13: "the requests must just be queued,
rather than rejecting them."

1. New optional setting `transport.max_concurrent_requests` (unset by
   default, which keeps today's behaviour).
2. `RecordedExactModelClient` takes a slot from one queue shared by every
   client in the process for the same endpoint, only around the HTTP call,
   and records how long the call waited. Model preflights do not use this
   client and are not counted.
3. Campaign 4 sets 24, the level the probe passed at. The Harvey adapter's
   own judge cap is lowered to 18 inside it, so agents always keep at least 6
   slots.

### Upstream recommendations

1. Provide one endpoint-wide concurrency limit covering the evaluated agent,
   benchmark-owned model calls, and meta agents.
2. Report time spent waiting for a slot separately from model latency.

### Local result

- `src/ahe_runner/config.py`: `TransportSettings.max_concurrent_requests`
  (at least 1; unset by default).
- `src/ahe_runner/meta/model_client.py`: one `threading.BoundedSemaphore` per
  endpoint URL for the whole process, created by the first recorded client
  and reused by the others. A client asking for a different limit on the
  same endpoint is rejected as a configuration error. The slot is held only
  around the HTTP call. `response.json` and `error.json` gain
  `slot_wait_seconds` when the queue is on.
- Tests in `tests/test_model_access.py`: three clients sharing a limit of 2
  never have more than 2 calls in flight and record a wait; without a limit,
  calls are not queued and no wait is recorded; a failed call records its
  wait; a conflicting limit and a limit of 0 are rejected.
- Offline checks on 2026-10-13: the full suite passed 199/199. ruff is
  clean. pyright reports no new errors.

## AHE-016: meta agents differ from the original AHE agent harnesses

### Plain-language summary

The runner's meta agents (Debugger, Evolve, Source and Web Explore) were
written for the runner and do not use the original AHE agents' tools or
middleware. The gaps were found one at a time (AHE-010, AHE-013, AHE-014)
and caused stops. In campaign 4 round 2, two debuggers read `.docx`
deliverables with `read_file`. The tool decoded the zip bytes as text,
about 36,000 tokens per file. The runner's compaction then stopped the agent
with `IntegrityError`, which blocks the stage permanently (AHE-011).

### Evidence

- Original AHE `8b2a55d`: `agents/evolve_agent/evolve_agent.yaml`, the
  `adb` debugger `runtime/agent_config.yaml` and `system_prompt.md`, and the
  explorer `agent.yaml` files. NexAU 0.3.9 in the runner's environment ships
  the same tool and middleware code (byte-identical tools; only import lines
  differ in the middleware).
- The original `read_file` reads lines (offset/limit, 2,000 lines, 2,000
  characters per line) and refuses binary files instead of decoding them.
  `LongToolOutputMiddleware` keeps every tool result under 10,000 characters
  and saves the full text to a file. `ContextCompactionMiddleware` has an
  emergency summary-and-retry path instead of a hard stop.
- The original debugger has 25 iterations, a hard prompt budget of 20, and
  its failures never block the run.
- Campaign 4 round 2 log: runbook section 5e.

### Approved local repair

The operator's decisions are listed in runbook section 5f: the original
tools behind a path guard, a Docker-sandboxed `run_shell_command`, the
original middleware and debugger budget, non-blocking debugger failures,
original role temperatures, readable evidence, and the
`nexau-evolution-guide` skill for Evolve.

### Upstream recommendations

1. Build the runner's meta agents from the original AHE harnesses (or
   document each difference), so known safety nets are not lost.
2. Keep path restrictions as a guard around the original tools rather than
   as separate tools with different behaviour.
3. Give meta agents a sandboxed shell; reading one field of a long JSON
   trace line needs one.
4. Let a failed per-task debugger degrade to "diagnosis unavailable" instead
   of blocking the round.
5. Provide readable views of binary deliverables and long trace lines.

### Local result

Implemented in `6f9d941` and tested offline. Campaign 5 is the first live
use.

What each meta agent now gets:

| | Debugger | Evolve | Source / Web Explore |
|---|---|---|---|
| File tools | original `read_file`, `search_file_content`, `glob`, `list_directory`; `write_file`, `replace`, `delete_path` only in its private `scratch` root | same tools; writes to the workspace | same tools; writes to `knowledge` |
| Shell | `run_shell_command` in Docker when `meta.shell.backend: docker` | same | same |
| Middleware | `LongToolOutputMiddleware` 10,000 / 50+30 lines / 5,000+5,000 characters; compaction 220,000 × 0.75, keep 10 | same | 20,000 / 100+80 lines; compaction 200,000 × 0.75, keep 15 |
| Model | `meta.roles.debugger` (campaign 5: 0.4, 20,000) | `meta.roles.evolve` (0.7) | `meta.roles.explorers` (0.2) |
| Skills | none | Explore skills + `nexau-evolution-guide` | none |

Main files:

- `src/ahe_runner/meta/guarded_tools.py` (new): wraps each original NexAU
  tool. A path must resolve, after symlinks, inside an allowed root; writes
  need a writable root; `.git` folders and the reserved roots stay
  read-only. Paths can be `root:relative`, absolute, or relative to the
  role's default root. It also builds the Docker command for the shell.
- `src/ahe_runner/meta/middleware.py` (rewritten): wires the original
  middlewares with the table's settings. The debugger access recorder now
  parses the original tools' output (line-numbered `read_file`, `L<n>:`
  search results) to know which lines were read.
- `src/ahe_runner/meta/tool_descriptions/`: the original YAMLs plus one
  paragraph on roots; `list_files`, `search_text` and `replace_text` are
  removed.
- `src/ahe_runner/meta/skills/nexau-evolution-guide/`: the original skill
  with its MIT licence.
- `harvey-ahe-project/docker/meta-shell.Dockerfile`: image
  `ahe-meta-shell:py313-v2` (Python 3.13, git, ripgrep, jq, file, unzip,
  PyYAML).
- `harvey-ahe-project/harvey_ahe/readable.py` (new): writes
  `readable/<task>/trial-<n>/trace.md` (each JSON event decoded, real line
  breaks, lines wrapped at 1,000 characters with `↪ `) and
  `deliverables/<name>.md` (LAB's `read_file_as_text`). They are listed as
  hashed `readable_artifacts` and checked by `verify_evidence_bundle`.
- `nexau_backend.py`: per-role settings and tools, a one-time check that the
  shell image exists, and the non-blocking debugger (any failure other than
  an infrastructure error writes an "unavailable" result; the overview tells
  Evolve to read that task's evidence directly).
- `preflight.py`: the model probe uses each role's temperature.

Tests: `tests/test_meta_guarded_tools.py`, `tests/test_meta_middleware.py`,
`tests/test_harvey_readable.py` (new), plus new cases in
`tests/test_meta_resources.py`, `tests/test_harvey_adapter.py` and
`tests/test_evaluation_evidence_contract.py`. One test runs the real Docker
shell. Offline checks on 2026-10-13: the full suite passed 227/227. ruff is
clean. pyright reports no new kinds of errors (test files still read the
backend's private methods, as before).

Differences from the original, kept on purpose:

1. Paths are limited to allowed roots by a guard around each tool.
2. The shell runs in a Docker container (no network, read-only root
   filesystem, read-only `.git` folders, memory/CPU/process limits) instead
   of on the host.
3. The debugger has a private, empty `scratch` root for its own files; the
   evidence stays read-only.
4. Debugger citations must be line ranges, and every cited line must have
   been returned whole by `read_file` or a search. Lines that `read_file`
   cut at 2,000 characters do not count.
5. A debugger that fails or never submits gives "diagnosis unavailable"
   instead of a fallback diagnosis. Endpoint failures still stop the stage
   so it can be retried.
6. `LoadSkill` and the stop tools are not shortened by the long-output
   middleware, so a skill or a final answer is never cut.
7. The compaction middleware's emergency summary keeps the role's
   temperature and max_tokens (NexAU forces temperature 0, which the
   runner's model client would reject). The long-output middleware measures
   and saves a tool's text field rather than its JSON, so the saved file has
   real line breaks and can be read with `read_file`.
8. `ag-ui-protocol` is pinned below 1.0, which NexAU 0.3.9 needs.
9. Unknown `name:` prefixes are left as written rather than read as file
   names, so a citation such as `http://...` is never mistaken for a path.

Remaining risks:

- A symlink swapped between the guard check and the tool call could escape
  a root (time-of-check/time-of-use). Agents cannot create symlinks with the
  file tools; the shell could, inside a writable root.
- Shell exit code 125 can mean either a Docker failure or the command's own
  code.
- Writing `SKILL.md` through the shell does not count for the Explore
  deliverable gate; the agents are told to use `write_file`.
- Both Explorers share the `knowledge` root.
- Lines seen only through the shell cannot be cited by the debugger.
- In raw files containing `\x0b` or `\u2028`, `read_file` and search may
  number lines differently. The readable views remove those characters.
- The preflight checks that the model accepts temperatures 0.7 and 0.4
  before each stage.


## AHE-017: change attribution never matches the predicted tasks

### Plain-language summary

After each round, AHE checks every change in Evolve's `change_manifest.json`
against the tasks that improved or regressed. Evolve names the tasks it
expects to fix using the debugger's task keys (`task-<hash>`, built from the
task id and the evidence hash, so they also change every round). The
attribution step compares those names with the evaluation's task keys (a
different hash). The two never match, so every change is reported as
INEFFECTIVE with 0 hits, every improvement as "unattributed", and every
regression as "unanticipated". The next round's Evolve reads this report
when it decides what to keep or revert.

### Evidence

- Campaign 3 C1: `chg-c1-systemprompt-coverage-review` 0/6, INEFFECTIVE.
- Campaign 4 C1: all three changes 0/4, 0/2, 0/4, INEFFECTIVE.
- Campaign 5 C1: all three changes 0/6, 0/3, 0/3, INEFFECTIVE, while three
  of the predicted tasks did improve.
- `meta/nexau_backend.py` `_debugger_task_key` builds the debugger keys;
  `history.write_transition` matched predictions verbatim.

### Approved local repair

Approved by the operator on 2026-10-13 ("fix attribution, redo C1
attribution, then launch round 2").

1. `CampaignController._attribution_task_aliases` maps the debugger key of
   the round, the task id and the evaluation task key to the evaluation task
   key.
2. `write_transition` resolves `predicted_fixes` and `risk_tasks` through the
   map, keeps Evolve's original lists, adds `predicted_fix_task_keys`,
   `risk_task_keys` and `unresolved_task_references`.
3. The alias map hash is a semantic input of `round.N.attribute`.

### Upstream recommendations

1. Use one stable task reference across debugger output, change manifests,
   evidence and attribution, or resolve aliases in one place.
2. Reject or warn on change-manifest task references that match no training
   task when the candidate is validated.

### Local result

- Tests: `test_transition_resolves_debugger_task_aliases` and
  `test_attribution_aliases_map_debugger_keys_to_task_keys`; the full suite
  passed 229/229; ruff is clean.
- Campaign 5 C1 recomputed offline with the fixed code: chg-1 3/6
  PARTIALLY_EFFECTIVE, chg-2 2/3 MIXED (risk task regressed), chg-3 1/3
  PARTIALLY_EFFECTIVE; 2 unanticipated regressions; no unresolved
  references. Campaign 5's stored report was left unchanged: the campaign
  identity fingerprints the runner package, so campaign 5 cannot resume
  under the fixed code. Campaign 6 reran the same configuration with the fix.

## AHE-018: a metric change does not flag stale meta-agent guidance

### Plain-language summary

Campaign 4 changed the primary metric from LAB's dual all-pass rate to the
mean criterion pass rate. The adapter, its benchmark context and
`benchmark_public/ADAPTER.md` were updated, but the Harvey role supplements
were not. These supplements are appended to the Debugger and Evolve system
prompts. Evolve's still said the all-pass rate was the sole target and the
criterion pass rate only a tie-breaker. So in campaigns 4 to 8 both meta
agents were told two different objectives. Nothing in AHE checks that the
supplements agree with the adapter's metric. The effect on results is
unknown.

### Evidence

- Commit `b7029c3` changed the metric in `harvey_ahe/adapter.py`,
  `benchmark_public/ADAPTER.md` and `ahe.yaml`, but not
  `harvey-ahe-project/meta_supplements/`.
- `meta_supplements/evolve.md` lines 7-9: "The sole optimization target is
  the dual all-pass rate", with the criterion pass rate a "tie-breaker and
  diagnostic only"; section 1 says one failed criterion fails the trial.
- `meta_supplements/debugger.md` lines 5-11 describe the trial score as the
  mean of the judges' all-pass values (0, 0.5 or 1).
- `meta_supplements/source.md` line 84 and `web.md` line 21 use milder
  all-pass framing.
- The stale text is in the rendered prompts of campaigns 7 and 8, for
  example `evolve/C1/attempt-0001/runtime/system_prompt.md`.

### Status

Recorded on 2026-10-14 at the operator's request. The supplements are hashed
into campaign identity (`resolved_meta_inputs`), so the repair waited until
campaign 8 had finished.

Fixed locally on 2026-10-14, with approval:

- `evolve.md`: the target is the mean criterion pass rate, with the dual
  all-pass rate reported and only breaking ties. Section 1 now says every
  criterion counts rather than that one failed criterion fails the trial.
  The analysis steps separate broad losses (most criteria failed at once)
  from partial losses, replacing "could never pass" and "near misses".
- `debugger.md`: the trial score is the share of criteria passed, averaged
  over both judges. The paired-comparison note no longer says training has
  one trial per task; it has had two since campaign 7.
- `source.md` and `web.md`: the all-or-nothing wording is replaced.
- Checks: the offline render check (as in AHE-009) renders all four roles
  byte-identical; the 230 runner tests and the adapter tests pass;
  `ahe validate` passes. The checklist below found no old-metric wording in
  the starting harness, `meta_prompt_context()` or `benchmark_public/`.

The corrected text applies from campaign 9. Report campaigns 4 to 8 with
this caveat.

### Rule for future metric or criteria changes

Whenever the primary metric, the pass criteria or their aggregation change,
review in the same change:

1. the role supplements (`meta.role_supplements`);
2. `meta_prompt_context()` (`benchmark_objective`, `evidence_description`,
   `failure_taxonomy`);
3. the public adapter documents under `meta_agent_source_roots`;
4. `compare()` and `selection_key()`;
5. the seed harness prompt and memory, if they state the scoring rule.

Searching these files for the old metric's name and phrasing (here
"all-pass") is a quick check.

### Upstream recommendations

1. Document this rule in the adapter guide's role-supplement section.
2. Optionally have `ahe validate` warn when a role supplement names a primary
   metric other than the adapter's.

## AHE-019: long-term memory leaks into the evaluated prompt without a separator

### Plain-language summary

AHE adds the harness's `LongTermMEMORY.md` to the end of the evaluated
agent's system prompt. It did this even when the file only described itself,
and it joined the text with no space or line break. The AHE template and the
Harvey starting harness (`41757d7`) both shipped such a self-description. So
in campaigns 1 to 8 every evaluated trial's system prompt ended:

```text
...Do not keep reading or editing after
  the work is complete.# Persistent harness memory

## Persistent harness memory: LongTermMEMORY.md

# Long-term memory

This file is attached to the root system prompt at session startup. Record only
reusable procedures and stable lessons about legal-document work, tool use, and
deliverable formatting. Never store task identifiers, task-specific facts or
answers, grading rubric contents, hidden state, credentials, or held-out
information.
```

That is 420 extra characters. The note was written for Evolve, not for the
agent doing the legal task, and it mentions grading rubrics. The Debugger and
Evolve prompts were not affected. The effect on scores is unknown.

### Evidence

- `src/ahe_runner/runtime.py` `_attach_long_term_memory` always built
  `"# Persistent harness memory\n\n## Persistent harness memory: <file>\n\n<text>"`
  and set it as `system_prompt_suffix`.
- NexAU `prompt_builder` does `rendered += system_prompt_suffix`, with no
  separator.
- Campaign 8,
  `evaluations/C0-train/runtime/022bcc79e3fe5efeafd0dc04a88c9513/attempt-1/model_calls/call-0001/request.json`:
  the system message ends as quoted above.
- Evolve edited the memory file in a few checkpoints (6 of 47 copies across
  campaigns differ from the original). Those checkpoints sent Evolve's text
  instead, still without a separator.
- It was missed because the starting-harness parity check compared
  `systemprompt.md` with LAB's prompt, not the assembled request.

### Approved local repair

Fixed on 2026-10-14 in `6f72430`, as the operator chose:

- memory files that are empty or whitespace add nothing;
- otherwise the suffix starts with a blank line;
- `LongTermMEMORY.md` is empty in `harness/`, `harness_lab/` and the AHE
  project template. Its usage note moved to the Evolve supplement's
  components table.
- Tests: `tests/test_harvey_lab_exact.py` checks the full request sent
  through NexAU (the system message equals LAB's prompt), the empty case and
  the separator.

Applies from campaign 9. Report campaigns 1 to 8 with this caveat.

### Upstream recommendations

1. Skip empty memory files and always separate the suffix from the prompt.
2. Keep notes about the memory file out of the file; put them in the Evolve
   instructions.
3. Parity checks should compare the assembled request, not the source files.

## AHE-020: tool schemas must set `additionalProperties: false`

### Plain-language summary

AHE conformance rejects any tool schema without `additionalProperties:
false`. NexAU sends the schema to the model as written. LAB's own tool
schemas leave the field out, so a harness meant to match LAB exactly would
show the model seven slightly different schemas.

### Evidence

- `src/ahe_runner/conformance.py` `_validate_tool_schema`.
- Campaign 8 requests show `"additionalProperties": false` in all 7 tools;
  LAB's `lab_core/harness/tools.py` has none.
- `ahe validate` on `harness_lab` failed with seven
  "additionalProperties must be false" errors.

### Approved local repair

Fixed on 2026-10-14 in `44ea8aa`. The new manifest setting
`tool_schemas.allow_omitted_additional_properties` defaults to false. When
true, the field may be left out; any value other than `false` is still
rejected. Tool bindings still may not take `**kwargs`, so unexpected arguments
cannot silently reach a tool. `harvey-ahe-project/harness_manifest_lab.yaml`
turns it on for campaign 9 only. The shared manifest stays strict. The
manifest is outside the harness, so Evolve cannot change it.

Remaining difference from LAB: LAB ignores an unexpected argument; NexAU
returns a tool error to the model.

### Upstream recommendations

1. Offer this as a documented manifest option for benchmark-parity
   harnesses.

## AHE-021: the model transport silently cut long prompts and ignored the effort

### Plain-language summary

The evaluated agent reaches Copilot models through GH Copilot Server, which
uses VS Code's language-model API. VS Code adds each model's default
settings to every request. For gpt-5.4, gpt-5.5 and gpt-5.6-sol that means
a 272k context size and "medium" effort. Copilot Chat then deleted the
oldest messages, including the system prompt and the task, until a prompt
fit about 270k tokens, without any error. It also ignored the effort that
the request asked for. Nothing in AHE could notice, because the transport
reports no token usage and no effort.

### Evidence

- Campaign 9 (`HARVEY_AHE_RUNBOOK.md` section 5m): in all 25 Copilot 400
  "No tool call found" errors, the cut falls exactly at a ~270k-token
  budget. 15 of 101 graded trials made silent cut calls and scored lower
  (mean criterion pass 0.429 against 0.522).
- Code: VS Code `sendChatRequest` merges schema defaults into
  `configuration`, which Copilot Chat reads as `modelConfiguration`
  (`contextSize`, `reasoningEffort`). It never reads the effort in
  `modelOptions`.
- Probe (gpt-5.4) before the patch: a code in the first message was lost
  in a 317k-token prompt, and effort "none" and "high" behaved the same.
  After the patch, both behaved as requested.

### Approved local repair

GH Copilot Server's `out/server.js` (host instance) now sets
`options.configuration.contextSize` to the model's `maxInputTokens` and,
when the request has one, `options.configuration.reasoningEffort`. The
probe script and its before/after results are kept in
`harvey-ahe-project/scripts/copilot_context_effort_probe.py` and
`harvey-ahe-project/operator-preflight/2026-10-14/copilot-server-patch/`.

### Upstream recommendations

1. Before a campaign, run a transport probe for each model role: recall of
   a code placed at the start of a prompt near the configured context
   limit, and a latency or quality check that the effort is applied.
   Record the result with the campaign.
2. Where a transport reports no usage, record the prompt size AHE sent and
   flag calls above any known transport limit.

## AHE-022: one hung model call fails the whole trial

### Plain-language summary

The runner never resends a model call (`max_retries: 0`), so that every
call the agent depends on is recorded exactly once. When a call hangs until
the request timeout, the trial stops with an infrastructure error, and
resume reruns the unit from its first step. This throws away completed
work. It also favours shorter trials: a trial that makes more calls is more
likely to meet a hang, so the trials that finish lean towards those that
made fewer calls. The judges already resend transient failures inside a
trial (`grade_with_retries`); the agent's own calls did not.

### Evidence

- Campaign 9 (runbook 5m): 52 of the evaluated agent's calls hung until
  GH Copilot Server's 300 s timeout. Copilot Chat logged each one as
  "cancelled" after about 300 s, so the request was still open upstream.
- The hangs are specific to gpt-5.6-sol. Over the same hour it hung on 2.6%
  of calls, against 0-0.03% for gpt-5.4, gpt-5.5 and claude-sonnet-5.
  Campaign 8 (gpt-5.4) had 20 hangs in 75,722 calls.
- They are stalls, not slow answers. Successful gpt-5.6-sol calls took at
  most 96 s (median 4 s), and none finished between 96 and 300 s. Prompt
  size (median 20k tokens) and concurrency (median 9 calls in flight) were
  normal.
- They cluster early in a trial: about 7% of calls 2-4, 4% of calls 5-9,
  under 1% later.
- About 27% of campaign 9's unit attempts were lost to hangs.

### Approved local repair

`transport.hung_call_retries` (default 0, at most 5). When a call fails
because a request timeout fired (the client's or the endpoint's "timed
out" error), the identical request is sent again, up to that many extra
times. Each failed attempt is written to `hung-attempt-N.json` in the call
folder and listed under `hung_attempts` in `response.json`, or in
`error.json` if every attempt fails. Other errors, such as 503 overload or
400, are never resent. The setting applies to every role that uses the
campaign transport. Tests: `tests/test_model_access.py`
(`test_hung_call_*`, `test_other_errors_are_not_resent`).

### Upstream recommendations

1. Offer per-call resending of hung requests as a documented transport
   option, with every attempt kept in the trace.
2. Report, per campaign, how many calls were resent and how many unit
   attempts were lost to infrastructure errors, so readers can judge any
   bias towards shorter trials.

### Outcome in campaign 9's rerun

Runbook 5n: 106 hung attempts on 95 of 5,120 calls (about 2%). 92 calls
succeeded on a resend; 3 hung three times in a row, so their trials were
rerun and then succeeded. A call that hung once hung again 12% of the
time, against 2% for a fresh call, so the stalls look tied to the request.

## AHE-023: NexAU's context check differs from LAB's

### Plain-language summary

Before every model call, NexAU estimates the prompt size and stops the
agent if the estimate is over `max_context_tokens`. By default it estimates
one token per four characters. LAB has no such check: it sends the prompt
and stops cleanly only when the provider says "prompt is too long" or
"context_length_exceeded". The estimate is wrong in both directions. For
prose it is 10-20% too high, so NexAU stops agents that would still fit.
For dense tables it is too low, so a prompt that does not fit is sent, and
the provider rejects it. GH Copilot Server reports that as a 500 "Message
exceeds token limit", which the runner treats as an infrastructure error,
so the trial is rerun from the start, a second chance LAB would not give.

### Evidence

- NexAU 0.3.9 `TokenCounter` defaults to `strategy="fallback"`,
  `(len(text) + 3) // 4` (`archs/main_sub/utils/token_counter.py`); the
  stop is in `execution/executor.py` around line 524.
- Campaign 9 rerun (runbook 5n): 14 trials were stopped by the estimate.
  On their last sent prompt the real o200k count was 0.80-0.96 times the
  estimate, so about 10 of them were probably still under 921,793 real
  tokens when stopped. All 14 wrote no output (mean criterion pass 0.12).
- Task 208: one tool result of 2,715,670 characters (a table listing)
  was estimated at about 680k tokens but is 946,107 real tokens; the
  request (962,286) was rejected, recorded as an infrastructure error, and
  rerun (second attempt 0.056).
- No answered call in the rerun exceeded 797k real tokens, so nothing was
  silently cut (AHE-021).

### Approved local repair (implemented)

1. `harness_lab/agent.yaml` sets `token_counter:
   harvey_ahe.token_count:count_prompt_tokens`. The new module
   `harvey_ahe/token_count.py` counts with o200k_base `encode_ordinary`
   (so document text such as `<|endoftext|>` is ordinary text) and keeps
   NexAU's per-message, per-block and per-tool overheads. It counts a
   2.7M-character table in about 0.3 s.
2. `max_context_tokens` is now 911,793: 10,000 below Copilot's 921,793.
   This margin is a local choice. Through Copilot, a prompt over the limit
   made of many messages is silently cut from the start (AHE-021), not
   rejected, and NexAU's count is within about 2k of Copilot's, so the
   harness must stop just before the limit. LAB has no cap because its
   providers reject such prompts.
3. The Harvey adapter's `_run_session` treats a provider "too long" error
   (LAB's "prompt is too long" and "context_length_exceeded", plus
   Copilot's "Message exceeds token limit") as a graded `context_overflow`
   stop, as LAB does, not as an infrastructure error. The final text is
   "[Error: Prompt too long, rejected by the provider: ...]". The trace of
   that trial is empty, because NexAU raises without returning messages.
4. Tests: `test_exact_token_counter_uses_o200k_with_nexau_overheads`,
   `test_harness_lab_matches_lab_loop_limits`
   (`tests/test_harvey_lab_exact.py`) and
   `test_provider_context_rejection_is_a_graded_context_overflow`
   (`tests/test_harvey_adapter.py`).
5. Campaign 9b (runbook 5o) evaluates all 250 tasks again with this fix.
   Campaign 9's results are kept with a caveat (runbook 5n).

### Upstream recommendations

1. Count tokens with the model's tokenizer, or make the check opt-in, and
   document which estimate a harness uses.
2. Classify a provider context-length rejection as an agent outcome, not
   an infrastructure failure, so it is graded rather than rerun.

## AHE-024: the exact token count assumes a GPT tokenizer

### Plain-language summary

The AHE-023 fix counts every prompt with o200k_base, the tokenizer of the
GPT models used up to campaign 9b, and stops the agent at
`max_context_tokens` in that count. Claude models use their own tokenizer.
For claude-opus-4.8, the same Harvey text is 1.65-1.68 times as many
tokens, so a limit chosen for GPT lets through far more text than a Claude
model accepts. The harness has one number and no record of which tokenizer
it is counted in.

### Evidence

- Probes on 2026-10-15
  (`harvey-ahe-project/operator-preflight/2026-10-15/copilot-server-claude-thinking-patch/`):
  three prompts of real tool output from campaign 9b traces were rejected
  by Anthropic with their Claude token counts. 925,080 o200k tokens were
  1,530,317 Claude tokens (1.654), 925,175 were 1,549,297 (1.675), and
  631,109 were 1,058,298 (1.677).
- Anthropic's limit for claude-opus-4.8 through Copilot is 1,000,000
  Claude tokens (`model_max_prompt_tokens_exceeded: prompt is too long: N
  tokens > 1000000 maximum`), about 597k o200k tokens of this text. The
  harness limit is 911,793 o200k tokens.
- Copilot Chat itself counts only with o200k_base or cl100k_base and cuts
  prompts above 935,793 for this model, so for Claude the provider always
  rejects first and Copilot never cuts silently (unlike AHE-021).

### Local decision

No patch. Campaign 9c (claude-opus-4.8) was planned to keep the harness
unchanged: an Opus prompt over the provider's limit would be rejected and
graded as a `context_overflow` (AHE-023), as LAB does. Two side effects
were expected: such a trial's trace is empty, because NexAU raises without
returning messages; and a prompt just under the limit leaves less room than
the 64,000-token output budget. The operator dropped 9c before launch, so
every campaign so far evaluates a GPT model, for which the count is exact.
Revisit this before evaluating any non-GPT model.

### Upstream recommendations

1. Count with the evaluated model's tokenizer, or with a per-model factor
   calibrated against the provider, and state the counting unit next to
   `max_context_tokens`.
2. Where the transport reports no usage, calibrate with an over-limit probe
   whose rejection reports the provider's count, and record the result with
   the campaign.

## AHE-025: concurrent trials share one Python process

### Plain-language summary

The Harvey adapter runs up to 20 trials at once as threads of the one
`ahe` process. The harness's `grep`, `glob` and file tools run on the host
inside that process, in Python. Python runs only one thread of
CPU-bound Python code at a time, so when several trials search the
shared document store at once, their searches wait for each other. A
search that takes about 8 seconds alone takes minutes in a campaign.
LAB starts a separate process for every task run, so its searches do not
wait for each other. Scores are not affected (no tool times out), but
trials take much longer than in LAB, and the share of time lost grows
with how often the tasks search.

### Evidence

- Alone, the seed `grep` (`harness_lab/tools/grep.py:49` reads every
  file with `read_text`) took 6.7-10.2 s over the 9,288-file shared store
  (494M characters).
- Campaign 10, C0 train, first 65 trials: the median time between a model
  reply that called only `grep` and the next model request was 257 s
  (90th percentile 662 s). `grep` took 53% of trial time. The median trial
  took 17.3 minutes, of which 1.8 minutes were model calls.
- Campaign 9b, C0, 253 trials: median 31 s, 90th percentile 281 s, 11% of
  trial time; median trial 8.3 minutes. Campaign 9b searched about three
  times less often per trial, so it was less affected.
- During campaign 10 the `ahe` process used about 108% CPU while the
  24-core host's load was about 2.3: one busy Python interpreter, not a
  busy machine.
- `harvey_ahe/adapter.py:565` (`_run_pending`) runs trials in a
  `ThreadPoolExecutor`. LAB's `lab_core/utils/sweep.py:327`
  (`_run_agent_worker`) starts `python -m lab_core.harness.run` as a
  subprocess for each run (line 336). LAB's own `grep` is also host-side
  Python.

### Local decision or repair

Campaign 10 was stopped on 2026-10-15 after 50 of its 120 C0 training
trials and restarted from scratch with a fix (runbook 5r).

The new adapter setting `trial_processes` (default off; on for campaign 10)
runs each trial's agent session in its own Python process, as LAB does:

- `src/ahe_runner/child_process.py` starts a fresh (spawned) child, sends it
  the trial over a pipe and returns its result or error. The child stops if
  the `ahe` process dies.
- The runtime's model settings and its already-passed model preflight are
  handed to the child (`RuntimeProcessSpec`), so the preflight is not
  repeated per trial.
- The shared limit on concurrent model requests (AHE-015, 24 slots) is now a
  semaphore that works across processes and is passed to every child.
- Grading, the check that the shared document store is unchanged, and all
  bookkeeping stay in the parent process.
- Errors are sorted into fatal, infrastructure or ordinary in the child,
  because Python loses an exception's cause when it is sent between
  processes.

Measured without model calls: 20 searches of the shared store at once took
241 s each as threads of one process and 10.5 s each as separate processes.

Known limits:

- A child that is killed while it holds a model-request slot does not give
  the slot back, so the limit drops by one for the rest of the run. A
  killed child also leaves its sandbox container behind.
- Harness module state is now per trial; before, trials running at the same
  time shared it.
- Each child adds about 230 MB of memory and 2-3 s of start-up.

### Upstream recommendations

1. Run each evaluated trial in its own process, or otherwise keep
   host-side harness tools of one trial from blocking another's.
2. Record per-tool wall time in traces, so that slow tools are visible
   without reconstructing them from model-call timestamps.

## AHE-026: an outage can block a stage for good

### Plain-language summary

A stage that fails because of infrastructure (a timeout, a dropped
connection) is retried when the operator resumes, up to
`recovery.maximum_stage_attempts` attempts. If the outage lasts longer than
those attempts, the stage is blocked, and AHE had no way to unblock it
(AHE-011). The only option was to start a new campaign, throwing away hours
of valid work, even though no attempt had produced a result and nothing
about the campaign was in doubt.

The outage itself also had a cause AHE could have helped with: the model
server runs inside a VS Code window that slowly uses more memory, and the
runner had no point at which an operator could check or restart it between
stages.

### Evidence

- Campaign 12 (gpt-5.6-sol, high) ran for about 34 hours. On 2026-10-16 at
  05:59:44 UTC the renderer process of the model host's VS Code window
  stopped with "V8 javascript OOM" after about 46 hours. Its extension host
  closed, and port 3141 stopped answering.
- In the C4 train evaluation, 41 of 160 trials had finished; the other 119
  failed with "Connection error". The two resumes that followed failed at
  once for the same reason, the stage reached its 3 attempts and was blocked,
  and the resume loop stopped.
- Rounds 0 to 3 and round 4's debugger, Evolve, freeze and validation stages
  were complete. The 41 finished C4 trials were written before the crash and
  are valid.

### Local decision or repair

The operator approved, on 2026-10-16:

1. `ahe retry-blocked-stage --config C --stage S --reason R [--apply]
   [--allow-runner-code-change]` (`controller.py`, `state.py`). It accepts
   only a blocked, retryable stage whose every attempt failed on
   infrastructure (or was abandoned by a killed process). It adds
   `recovery.maximum_stage_attempts` more attempts, recorded as
   `attempt_allowance` and an `operator_overrides` entry in the stage
   record, and appends an entry of kind `retry_blocked_stage` to
   `campaign_amendments.json`, chained like the `extend-rounds` entries.
   Protected inputs must be unchanged; changed runner files are accepted
   only with the flag and are listed. It is a dry run unless `--apply`.
2. Stages now store `retryable` in `stage.json`, so the check does not have
   to guess. For older records, a stage counts as retryable only if an
   earlier attempt failed on infrastructure without blocking it.
3. `AHE_PRE_STAGE_COMMAND`: an operator command run before every new stage
   attempt, with `AHE_STAGE_NAME` and `AHE_CAMPAIGN_DIR` set. If it fails or
   times out (`AHE_PRE_STAGE_TIMEOUT_SECONDS`, default 1200), the runner
   stops with an infrastructure error before the attempt is recorded, so no
   attempt is used. Harvey uses it to restart the model server's VS Code
   window when its memory is high (runbook section 5v).

### Upstream recommendations

1. Let an operator reopen a stage blocked only by infrastructure failures,
   with an audit record, instead of requiring a new campaign.
2. Give operators a supported hook between stages for health checks of
   external services.

## AHE-027: a closed campaign cannot be given more rounds

### Plain-language summary

`ahe extend-rounds` lets an operator raise `campaign.rounds` for a running
campaign. It refuses once evolution has closed, that is once any of the
stages `evolution.close`, `selection` or `final.complete` exists. Those
stages are written for the old round count: `evolution.close` stores
"the last checkpoint is C5", `selection` picked the best of C0..C5 on
validation, and `final.complete` tested that pick. With a larger round count
their recorded inputs no longer match, and the stage store stops with
"stage semantic inputs changed after creation".

So a finished campaign could only be continued by starting a new campaign,
which repeats every finished round (for campaign 12 about 20 hours of model
calls) and, because model calls are random, would not even reproduce the
same C1..C5.

### Evidence

- Campaign 12 (`harvey-lab-campaign-12-gpt-5.6-sol-high-80-20-20`) finished
  on 2026-10-16 at 18:29 UTC with 5 rounds. Validation selected C5; the final
  test gave C5 0.722 against C0 0.645 (runbook 5w).
- The operator asked to run 5 more rounds from C5 on the same model and
  endpoint. `extend_rounds` refuses: the campaign has all three closing
  stages.
- Nothing before closing depends on the round count. The round count is
  read only by `run`, `run_round`, `close_evolution` and `run_validation`
  (`controller.py`), and is a stage input only of `evolution.close`. No meta
  prompt, supplement or Evolve query mentions the total or a last round; the
  Evolve query names only the current checkpoint (`C{i}`).
- The meta agents cannot see what closing produced. Their read roots are the
  training evidence and raw training evaluation, the debugger output,
  history (built from training evaluations only, `history.py`), attribution,
  checkpoints, Explore skills and public sources (`run_evolve` in
  `meta/nexau_backend.py`). `evaluations/*-validation`,
  `evaluations/*-final-test`, `selection/` and `final/` are never mounted.

### Local decision or repair (proposed)

A new flag, `ahe extend-rounds --reopen-closed-evolution`. Without the flag
nothing changes: extending a closed campaign is still refused, and the error
names the flag.

With the flag, in addition to the usual `extend-rounds` checks:

1. Every stage of the campaign must be `completed`. A stage that is running,
   interrupted or blocked is refused, so nothing is reopened halfway.
2. The outputs of the closing stages are checked against their recorded
   hashes, and must lie under `selection/` or `final/`.
3. A dry run (the default) lists what would move, the previous selection and
   the previous test scores.
4. `--apply` moves, without copying or deleting:
   - the stage records `stages/evolution.close`, `stages/selection` and
     `stages/final.complete`, and
   - their outputs (`selection/evolution_closed.json`,
     `selection/selection.json`, `final/final_test.json`,
     `final/selected-harness`)

   to `superseded/closure-NN/`, keeping their relative paths, and writes
   `superseded/closure-NN/reopen.json`. It then appends the usual
   `extend_rounds` amendment with an extra `reopened_closed_evolution`
   block: the archive folder, each moved path with its hash, the closed
   round count, the previous selected checkpoint, and the previous
   validation and test primary metrics.
5. The next `ahe resume` reuses every other stage: all rounds so far, all
   train, validation and test evaluations. It continues with round
   `old + 1`, then closes evolution at the new round count, evaluates the new
   checkpoints on validation, selects the best of C0..C{new} by the same rule,
   and tests C0 and the new pick. A test evaluation that already exists for
   the same checkpoint is reused, as in any resume.

If the process stops after the moves but before the amendment is written,
every command refuses the larger round count (identity check), and running
`ahe extend-rounds --apply` without the flag records the amendment;
`reopen.json` still documents the move.

### What a report must say

The decision to continue was taken after the 5-round test result was known.
No meta agent can see validation or test results, so this cannot change what
Evolve does; it can only change whether a 10-round result exists. A report
should give both results: the 5-round closure (kept in
`superseded/closure-01/` and in the amendment) and the 10-round one, and say
that the campaign was extended after it had closed.

### Upstream recommendations

1. Let an operator continue a closed campaign for more rounds with an audit
   record of the superseded closure, instead of requiring a new campaign.
