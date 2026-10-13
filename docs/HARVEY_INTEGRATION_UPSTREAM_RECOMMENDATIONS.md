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
| AHE-002 | Registered skill folders' helper/resource files are not recognized as active skill components | Explained; operator chose a skills-free initial harness instead of a repair | No patch; outside the initial harness scope, potentially relevant to future scripted skills |
| LAB-001 | Filename helper bypasses configured Copilot routing and suppresses its API failure | Explained in plain language; repair authorized on 2026-10-12 | Fixed in LAB `86678eba6` and AHE `f0e3b0b`; 92 LAB and 19 AHE offline cases passed |
| AHE-003 | Candidate harness folders can shadow protected Python modules during evaluation and grading | Explained in plain language; adapter-level repair authorized on 2026-10-12 | Adapter-level rejection in Harvey `validate_candidate`; AHE source unchanged |
| AHE-004 | `ahe-runner` cannot be declared as a dependency of an operator package under uv, because of its NexAU git-URL pin | Explained; recording authorized on 2026-10-12 | Worked around in the Harvey `pyproject.toml`; AHE source unchanged |
| AHE-005 | V2 offers no way to give editable harness tools a benchmark-owned isolated execution environment | Explained; option A (adapter-owned per-session container lookup) chosen on 2026-10-12 | Adapter-owned sandbox handle in `harvey_ahe.workspace`; its token-in-context routing was replaced under AHE-006 |
| AHE-006 | Routing a session to its resource through session `context` is unsafe: NexAU's current context is one process-wide slot that concurrent sessions' sub-agents can read | Explained in plain language; on 2026-10-13 the operator chose orchestrator-owned binding (the runner hands each session its own resources) | Branch-local `AgentSessionRequest.session_resources` in `runtime.py`; Harvey adapter binds the sandbox handle through it |
| AHE-007 | The static import scan misreads `from pkg import name` as `pkg.n.a.m.e` | Explained with AHE-006; recording only | No patch; the Harvey seed imports helpers as `from tools._common import ...`, which is unaffected |
| AHE-008 | Meta-agents have no sandbox work folder, so NexAU copies the Explore skills, read-only, into the operator's current directory; a later Evolve run there fails with `Permission denied` | Explained in plain language; on 2026-10-13 the operator approved a private per-run folder and deleting the leftover `AHE_Harvey/.skills` | Branch-local `LocalSandboxConfig(work_dir=<runtime_dir>/sandbox)` in `meta/nexau_backend.py`, plus a regression test |
| AHE-009 | Project role supplements are rendered as Jinja together with the core meta prompt, so template markup in a supplement crashes that meta-agent at its first model call; config loading does not check this | Explained in plain language; recording approved on 2026-10-13 | No AHE patch; the Harvey supplements avoid Jinja markup, and an offline render check passes for all four roles |
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
