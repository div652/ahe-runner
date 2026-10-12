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
| LAB-001 | Filename helper bypasses configured Copilot routing and suppresses its API failure | Explained in plain language; repair authorized on 2026-10-12 | Optional LAB callback and protected Copilot implementation added; offline verification pending |

Other potential integration limitations in the implementation plan still
require investigation and the operator's case-by-case approval.

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
