# Harvey LAB evolution integration plan

Date: 2026-10-12.

**Implementation plan and progress.** This document tracks the approved
integration work. As of 2026-10-13 the adapter, seed harness, manifest,
campaign config, public contract, meta supplements, and runbook exist on
`harveybench` (see "Progress" in section 5). Offline Harvey tests are next.
No Harvey campaign, AHE CLI check, or live model call has been run.

### Implementation and execution ownership

The operator clarified the boundary on 2026-10-12:

- The assistant implements the project-level adapter, harness components, and
  domain supplements using the existing AHE operator-project structure.
- The operator runs the final `conformance`, `validate`, and `run` commands,
  all campaign phase/resume commands, and live benchmark/model calls.
- Before creating files, the assistant announces their paths and purposes.
  New files are allowed when needed; prefer filling the supplied project
  files and adding only actual components or necessary protected support.
- On 2026-10-12 the operator revised the defect procedure: explain each new
  issue and wait for understanding/approval, record detailed recommendations
  in `HARVEY_INTEGRATION_UPSTREAM_RECOMMENDATIONS.md`, then apply the approved
  minimal branch-local fix and proceed. This is not blanket approval to repair
  unrelated defects or redesign the AHE algorithm.

**Current status: approved fix AHE-001 completed before Harvey integration.**
The evaluated preflight-coverage gap was explained and the operator authorized
a local repair. The branch now wires the existing preflight into evaluated
attempts, preserves their proofs, and adds offline regression coverage. See the
separate recommendations record for the rationale and offline verification.
No final CLI commands or live probes have been executed. LAB-001's approved
helper callback and protected operator support files are now being added.

The operator subsequently approved a **skills-free initial evaluated
harness**: `skills: []`, no bundled skill manuals/scripts, and no `LoadSkill`
tool. Native LAB's skills-enabled baseline is a reference, not the intended
seed behavior. AHE-002 (skill-asset registration) is documented but not patched
because it is unnecessary for this initial scope. Meta-agent research and the
core evolution loop are unchanged; future scripted skills require revisiting
that limitation.

The operator also approved editable workspace-tool descriptions and
implementations, a grouped pilot of six training/three validation/three test
tasks excluding shared-DMS tasks initially, dense train-only evidence with
true training IDs, and Copilot routing for rubric judges and the filename
helper. Exact live model IDs/efforts remain operator-configured before running.
LAB-001 records the authorized transport hook without changing native metrics.

## 1. Checkpoint and source baseline

The completed AHE quick-start state was committed and pushed before this
investigation:

| Repository | Branch | Inspected revision / status |
|---|---|---|
| `AHE_Harvey` (`div652/ahe-runner`) | `harveybench` | `bc5ef29837a58a0afe2d621ae3c825c2b3c7acec`, pushed to `origin/harveybench` |
| `harvey-labs` (`harveyai/harvey-labs`) | `divyanshu_harvey_evolution` | `d7e56d4559ca8e3eee4db031c718e489b37d2220`, unchanged and not pushed by this task |

The checkpoint records the successful offline/model-backed toy campaigns and
ignores raw campaign artifacts. It does not establish Harvey compatibility.
Keep `my-ahe-project/` and its completed campaigns unchanged.

Relevant package declarations are AHE Runner `0.3.0`, NexAU `0.3.9`, and
`lab-core` `1.2.0`. The Harvey checkout includes a Copilot transport commit;
the release version alone is not a substitute for pinning that exact source.

## 2. What the actual benchmark contains

A read-only inventory of the checked-out `tasks/**/task.json` files found:

| Item | Observed value |
|---|---:|
| Tasks / practice areas | 2,010 / 27 |
| Rubric criteria | 114,437 |
| Criteria per task | 1 to 1,114 |
| Tasks below more than two directory levels | 867 |
| Criteria naming particular deliverables | 83,658 |
| Criteria falling back to the complete output directory | 30,779 |

The tasks are not one uniform schema:

| Family | Count | Loader/scoring implications |
|---|---:|---|
| Rich task schema | 1,262 | Contains `title`, `instructions`, `criteria`, `deliverables`, `work_type`, and `tags`; includes both flat and scenario-based paths |
| Minimal contracts schema | 498 | Contains only `title`, `instructions`, and `criteria`; do not require or invent top-level deliverables, tags, or work type |
| Shared-corpus firm knowledge | 250 | Adds an optional numeric-looking `id` and `docs_dir: ../../dms`; all 250 resolve to the same 9,288-file DMS |

Use the full task-directory path as the stable task identity, not the optional
`id` field. Discovery must recurse; fixed two-level directory assumptions
would miss valid tasks.

Four concrete examples examined:

| Task path | Source documents | Criteria | Output shape |
|---|---:|---:|---|
| `corporate-ma/review-data-room-red-flag-review` | 13 | 50 | DOCX memo and XLSX tracker |
| `real-estate/extract-psa-key-terms/scenario-01` | 3 | 75 | DOCX term sheet |
| `contracts/banking/account-control-agreement-counterparty-paper-review/scenario-01` | 8 | 47 | Filenames appear in instructions; criteria have no deliverable lists |
| `firm-knowledge/tasks/001` | Shared DMS: 9,288 | 7 | `response.md` |

Task-local document inventory includes DOCX, XLSX, PPTX, EML, TXT, and JSON.
The native tool also supports PDF. These are filesystem/document tasks, not
typed multimodal API inputs: retain native text extraction rather than sending
Office files directly as model message blocks.

### Native execution and grading

```text
task.json -> instructions only -> first user message
LAB system prompt + eagerly loaded document skills -> system message
model -> bash/read/write/edit/glob/grep/finish -> per-run workspace
saved deliverables -> protected rubric scorer -> one or two LLM judges
```

The rollout agent does not receive `criteria`, `match_criteria`, or additional
expected filenames taken from task metadata. `finish` checks only files the
agent claims to have created, not the hidden expected deliverables.

The native loop executes tool calls sequentially in model order. It stops
after an accepted `finish`, a call-free response, the total turn limit, or a
recognized context overflow. The finish gate permits two missing-file
rejections before accepting completion anyway; missing/poor deliverables can
then receive a valid low score.

For each judge, a task passes only if every criterion passes. The standard
judge pair is `claude-sonnet-4-6` and `gpt-5.5`. Its headline is the mean of
their binary all-pass values: a single trajectory can score `0`, `0.5`, or
`1`. The stricter "both judges all-pass" flag is separate.

## 3. Proposed integration boundaries

The target is an AHE-owned NexAU agent that does legal work against LAB's
native documents and grader. It is not a wrapper that runs LAB's existing
model loop and labels its results as NexAU evaluations.

```text
AHE controller
  -> immutable NexAU harness checkpoint
  -> HarveyBenchmarkAdapter.evaluate(request, runtime)
     -> one isolated LAB workspace per task/trial/attempt
     -> runtime.open_session(...) -> NexAU Agent/Executor
     -> LAB-compatible workspace tools and document skills
     -> saved outputs + native LAB grading
     -> verified EvaluationResult
  -> training-only evidence -> Debugger -> one global Evolve update
  -> held-out validation selection -> final C0/selected test -> export
```

Keep task selection, rubrics, judging, sandbox mount/network policy, model
routing, resource ceilings, result verification, and held-out data outside
`harness/`.

Put agent behavior that is approved to evolve into visible, runtime-attached
harness files. Preserve all seven available classes: system prompt, tool
descriptions, tool implementations, middleware, skills, sub-agents, and
long-term memory. Native LAB has no middleware, sub-agents, compaction, or
long-term memory; do not add dummy components or silently change the baseline
just to populate every folder.

### Tool-ownership decision: settle this before implementation

| Option | Advantages | Limits / work required |
|---|---|---|
| File-backed, evolvable LAB tool wrappers using a trusted sandbox capability | Best matches the goal of evolving actual tool descriptions and implementations; native filesystem restrictions remain protected | Prove how the supplied runtime binds the LAB sandbox, preserves sequential execution, and keeps candidate code away from grading/task metadata; if a required public contract is absent, halt and report it upstream |
| Native LAB tools as runtime-injected protected actions | Reuses the existing V2 action-yield API and native `ToolExecutor`; the adapter can serialize calls and preserve native strings | Freezes the seven native schemas/implementations, restricting their evolution; terminal `finish`, mixed internal/protected calls, and sub-agent access need explicit treatment |

The first is the proposed target for full-component evolution. The second is
an explicitly narrower alternative, not a silent fallback. Do not implement
both paths before the operator selects the scope.

There is no separately registered `EvolutionAdapter` interface in AHE V2.
The requested "evolution adapter" is the domain integration implemented through
training evidence, `compare`, `selection_key`, candidate validation,
`MetaPromptContext`, and the four append-only role supplements. Reuse the
runner's evolution loop; do not create another controller.

## 4. Approval gates

These choices remain proposals, not selected experiment settings:

| Decision | Required resolution |
|---|---|
| Task coverage | Pilot subset versus broader coverage; practice areas, task families, and repeated-trial counts |
| Split policy | Group related scenarios/templates; choose whether firm knowledge uses approved shared-corpus query holdout or stricter corpus separation |
| Evolvable surfaces | Decide native-tool ownership, skill/script editability, allowed middleware, advisory sub-agents, and memory scope |
| Baseline fidelity | Approve any eager-to-lazy skill change, additional model-visible tools, altered termination, or total-call accounting; runtime extensions are upstream work, not this adaptation |
| Judging | Single versus dual profile, exact models/transports, reasoning, output limits, and filename-matching helper policy |
| Native metrics | Preserve averaged all-pass as headline; approve any additional selection tie-break beyond the runner's earliest-checkpoint rule |
| Training supervision | Dense train-only rubric/verdict/trace evidence versus a declared sanitized ablation; true task-ID visibility |
| Trust/isolation | Existing AHE trusted-local Python model versus stronger candidate-worker isolation |
| Live budget | Endpoint instance, exact model IDs for every role, rounds, task scope, trials, concurrency, deadlines, and retry ceilings |

A shared DMS is a substantive split issue. Holding out queries/rubrics while
sharing the native source corpus is not the same as holding out documents.
Neither claim may be made implicitly, and corpus sharing must not expose
held-out query definitions or rubrics to evolution.

## 5. Implementation task list

The table below is the original plan. Status is tracked in "Progress" after
it. Dependencies name actual prerequisites; table order alone does not imply a
dependency.

| ID | Task / deliverable | Depends on | Acceptance criterion |
|---|---|---|---|
| H01 | Freeze the decisions above in an experiment/protocol declaration | Operator decisions | Scope, metric, supervision, tool rights, judge roles, and permitted deviations are explicit; no inherited toy-run defaults |
| H02 | Prepare a pinned combined AHE/LAB environment and native document toolchain | H01 | Exact source revisions and resolved dependencies recorded; host Pandoc and sandbox document-generation tools available; image identified by digest, not just `latest` |
| H03 | Create a separate `harvey-ahe-project/` operator project and artifact policy | H01 | Sample project untouched; protected support/splits/public docs are outside `harness/`; credentials/raw outputs excluded from Git |
| H04 | Implement a native-compatible task catalog/loader and protected input manifest | H02, H03 | All three schemas and variable-depth IDs load; `docs_dir` resolves correctly; selected instructions/documents/rubrics and relevant file metadata have stable provenance |
| H05 | Implement immutable grouped train/validation/test definitions | H01, H04 | Nonempty disjoint task sets; scenario/template and shared-corpus policy enforced; deterministic regeneration and overlap checks |
| H06 | Implement the per-trial sandbox/workspace lifecycle and trusted execution boundary | H02, H04 | Read-only input documents, writable scratch/output, no network, no task JSON/rubric mount; unique workspaces and ownership-scoped cleanup |
| H07 | Prove the NexAU/LAB compatibility contract for the selected tool route | H01, H02, H06 | Scripted turns preserve tool order/results, finish rejection/acceptance, terminal pairing, cumulative limits, and asset attachment; missing required runner contracts cause a halt and upstream report, not a repair |
| H08 | Build the file-backed skills-free LAB-derived base harness | H03, H07 | Preserve native prompt intent, task-as-user-message, and workspace tools; omit all initial skill manuals/scripts per operator approval; no task answers in the seed |
| H09 | Implement Harness ABI V2 manifest and component registrations | H07, H08 | All evolvable files are actually attached; schema/signature checks pass; model/routing/ceiling fields protected; recursive sub-agents conform if enabled |
| H10 | Integrate native criterion grading and the deliverable-matching helper | H01, H02, H04 | Native scoping/extraction/prompt and profile retained; every model-calling helper declared; incomplete/invalid judging never becomes a valid zero reward |
| H11 | Implement native summaries, task states, comparisons, and selection key | H01, H10 | Exact `0/0.5/1` dual behavior; averaged all-pass primary; macro/pooled criterion diagnostics and both-agree rate separate; fractional gains/regressions retained |
| H12 | Implement `HarveyBenchmarkAdapter(BenchmarkAdapterV2)` evaluation driver | H04, H05, H06, H07, H09, H10, H11 | Uses the supplied runtime; exact task-by-trial coverage; honors concurrency and seeds; persists native/runtime artifacts through `EvaluationStore` |
| H13 | Implement independent artifact verification, completion, and recovery | H11, H12 | Recomputes facts/metrics from protected native records; verifies input/output hashes; contiguous immutable attempts; completed-zero units skipped on resume |
| H14 | Implement schema-V3 training evidence and artifact indexes | H01, H11, H13 | One canonical envelope per trial, exact source identity/hashes, bounded-readable native traces and rubric diagnostics; no validation/test evidence roots |
| H15 | Implement Harvey-specific candidate and leakage checks | H01, H09 | Reject evaluator imports, task/rubric lookup, routing changes, path escapes, answer memorization, and unsupported tool changes; do not grade held-out tasks during validation |
| H16 | Implement the evolution/domain adapter and prompt-fidelity matrix | H01, H14, H15 | `MetaPromptContext` plus Source/Web/Debugger/Evolve supplements translate legal workflows without replacing core procedures, budgets, lifecycle, or deliverables |
| H17 | Wire frozen campaign settings, dependencies, public roots, and supported model policies | H05, H09, H10, H12, H16 | Every protected behavior/data dependency declared; separate judge/helper settings validated; missing required runner preflight enforcement is reported upstream rather than added as an adapter workaround |
| H18 | Implement per-run/checkpoint reporting and a reproduction runbook | H11, H13, H14, H17 | Reports retain all trials/checkpoints and native judge artifacts; commands, hashes, environment, profiles, costs/unknowns, and deviations reconstruct the experiment |
| H19 | Add offline contract, native-parity, isolation, and recovery coverage; hand off final conformance/validation to the operator | H13, H14, H15, H16, H17, H18 | Fixtures cover all task families, fractional scores, grading failures, tool/finish semantics, evidence tampering, held-out boundaries, and resume; assistant does not execute the final AHE CLI checks |
| H20 | Operator runs the train-only live baseline and matched native LAB comparison | H19, operator execution | Real documents produce valid deliverables; all required judging completes; differences are explained as runtime/harness behavior rather than grader changes |
| H21 | Operator runs one evolution-round pilot with training evidence only | H20 | C0 evaluated; Explore/Debugger/Evolve deliverables verified; committed candidate validated/frozen; C1 evaluated and attributed; no held-out tuning |
| H22 | Operator closes evolution, selects on validation, performs final test, and verifies export | H21 | All candidate checkpoints validated after closure; deterministic selection; only C0/selected tested; export hash matches selection; test never triggers evolution |
| H23 | Operator scales to the research scope using the prepared reporting/handoff | H18, H22, operator execution | Calls/storage/runtime budget measured first; new frozen configuration/campaign directory when scope changes; no silent profile or split drift |

H07 is an early design gate, not a request to implement runtime changes.
Missing contracts must be resolved upstream before a dependent harness port.
H19's final CLI checks and all execution in H20-H23 are operator-owned.

### Progress (2026-10-13)

"Implemented" means written and statically checked offline; offline tests
are H19.

| ID | Status | Where |
|---|---|---|
| H01 | Done: decisions recorded above, in the issue records, and in the runbook | this file, `HARVEY_AHE_RUNBOOK.md` |
| H02 | Partly: AHE venv with editable `lab-core`; operator builds or pulls the image; each trial records the image ID | `requirements.txt`, runbook section 2 |
| H03 | Done | `harvey-ahe-project/`, `.gitignore` |
| H04 | Done for the pilot scope (no `docs_dir` tasks) | `harvey_ahe/tasks.py` |
| H05 | Done: 6/3/3 grouped pilot, hash-pinned | `splits.json` |
| H06 | Implemented | `harvey_ahe/adapter.py`, `harvey_ahe/workspace.py` |
| H07 | Runner seams added with approval (AHE-005, AHE-006, AHE-008); scripted tool tests pending | `src/ahe_runner/runtime.py`, recommendations doc |
| H08 | Implemented: skills-free LAB tool set and prompt | `harness/` |
| H09 | Implemented; NexAU load and tool-signature checks pass offline | `harness_manifest.yaml` |
| H10 | Done (LAB-001, LAB-002) | `harvey_ahe/grading.py`, `copilot_deliverable_matcher.py` |
| H11 | Implemented (LAB-003) | `harvey_ahe/adapter.py` |
| H12 | Implemented | `harvey_ahe/adapter.py` |
| H13 | Implemented | `harvey_ahe/adapter.py` |
| H14 | Implemented | `harvey_ahe/adapter.py` |
| H15 | Implemented; the seed passes offline | `harvey_ahe/candidate_checks.py` |
| H16 | Approved by the operator on 2026-10-13. Source, web, and evolve supplements follow the official AHE explore/evolve prompt style; debugger supplement, `meta_prompt_context()`, and seed URLs approved as written. AHE-009 found and recorded: supplements must avoid Jinja markup; an offline render check passes for all four roles. | `meta_prompt_context()` in `adapter.py`, `meta_supplements/`, `benchmark_public/ADAPTER.md` |
| H17 | Implemented with placeholder model IDs for the operator | `ahe.yaml` |
| H18 | Runbook written; reports are the adapter's per-trial artifacts | `HARVEY_AHE_RUNBOOK.md` |
| H19 | Pending: offline tests | - |
| H20-H23 | Operator | - |

### Project layout

Actual layout on `harveybench` (originally proposed with an
`adapter_support/` folder; the protected helpers became the installable
`harvey_ahe` package instead):

```text
harvey-ahe-project/
  ahe.yaml                     # campaign config; operator fills model IDs/budget
  harness_manifest.yaml        # Harness ABI v2 manifest and protected fields
  benchmark_adapter.py         # entry point: re-exports HarveyAdapter
  harvey_ahe/                  # protected adapter package (not meta-readable)
    adapter.py                 # BenchmarkAdapterV2: evaluate/verify/evidence
    workspace.py               # WorkspaceHandle + handle_for (harness-importable)
    tasks.py                   # pinned task loader and splits
    grading.py                 # LAB rubric grading with recorded Copilot judges
    copilot_deliverable_matcher.py  # LAB-001 filename helper callback
    candidate_checks.py        # Harvey candidate rules
  pyproject.toml, requirements.txt
  splits.json                  # protected split identities and pins
  benchmark_public/ADAPTER.md  # public contract (meta-readable)
  meta_supplements/
    source.md
    web.md
    debugger.md
    evolve.md
  harness/
    agent.yaml
    systemprompt.md
    LongTermMEMORY.md
    ShortTermMEMORY.md
    tool_descriptions/         # bash, read, write, edit, glob, grep, finish
    tools/                     # same seven + _common.py
    skills/                    # README only (skills-free seed)
    middleware/                # README only
    sub_agents/                # README only
  HARVEY_AHE_RUNBOOK.md
  campaign-artifacts/          # ignored; native and AHE artifacts
```

Split support modules only where responsibilities justify it. Prefer native
LAB functions or a small protected shared-helper extraction over duplicating
grading/aggregation logic. Any necessary LAB source change belongs on its
prepared branch and needs explicit benchmark-change approval.

## 6. Exact adapter responsibilities

| V2 method | Harvey implementation |
|---|---|
| `identity()` | Pin LAB source, adapter/protocol version, judge/helper profile, native metric, and supervision policy |
| `split()` | Return the frozen grouped task lists and identities |
| `benchmark_interface()` | Declare prompt/document/workspace inputs; protected tools only if that route is approved |
| `evaluate(request, runtime)` | Prepare isolated units, run NexAU, save deliverables and traces, grade natively, and complete verified units |
| `verify_evaluation()` | Recompute criterion counts, per-judge outcomes, trial facts, and aggregates from hashed native artifacts, without fresh judge calls |
| `materialize_training_evidence()` | Emit train-only schema-V3 facts, selected rubric diagnostics, trace indexes, and source lineage |
| `validate_candidate()` | Enforce Harvey-specific tool, data, leakage, import, and runtime constraints |
| `compare()` | Track native task-score changes, judge disagreement, criterion progress, and regressions without rounding half-points |
| `selection_key()` | Rank by the approved native validation metric; any secondary ranking must be predeclared |
| `meta_prompt_context()` | Describe legal work products, evidence, failure taxonomy, protected protocol, safe research, and leakage terms |

Canonical trial facts must distinguish a valid low/partial reward from an
ungraded trajectory. `TaskState.successes` is an integer: do not encode a
fractional dual score there. Keep native fractional score and judge-level
flags in the score/metrics fields, and declare how `passing`, `failing`, and
`other` diagnostic classes handle judge disagreement.

## 7. Compatibility and methodology traps to address

### A. Native tools are not automatically NexAU tools

LAB's `ToolExecutor` is constructed with a live `Sandbox`. V2 session input
currently exposes text-valued context and protected action schemas, not an
explicit LAB executor/capability parameter. Prove a clean binding for
file-backed tools before choosing it.

Do not bypass AHE's runtime with `lab_core.harness.run`, mutate
`session._agent`, or depend on a process-global mutable `LAB_ROOT` changing
under concurrent workers. If the needed public contract is missing, halt and
report it rather than adding a runner extension.

### B. Ordering, terminal results, and total budgets need explicit parity

LAB executes all tools in order and appends results before checking the finish
latch. NexAU normally permits parallel internal tool execution. The selected
bridge must preserve baseline ordering, including multi-call finish turns.

For the protected-action route, `session.run(tool_results)` initiates another
generation. An accepted terminal `finish` must not incur that extra call just
to append results. Persist the terminal native tool-result pairing and define
the closed-session history contract. If the agreed contract cannot be
represented through the public API, halt and report it upstream.

LAB's `max_turns` is a cumulative trajectory limit. Repeated NexAU activations
must not reset the effective allowance. Count internal/sub-agent generation
where enabled, and distinguish model calls from outer activations.

The current V2 boundary rejects turns mixing protected actions with internal
tools or sub-agent calls. Adding lazy skills/advisory tools must account for
that rule rather than assuming arbitrary mixed batches are supported.

### C. Skills and executable assets need a real attachment model

LAB eagerly appends all three document manuals and copies their scripts to
`/workspace/skills/<name>/scripts/`. NexAU normally exposes skill metadata and
loads full text through `LoadSkill`, mapping assets under `.skills/`.

Preserve eager behavior in a declared baseline or approve a lazy-skill
variant. Verify every manual's command paths and Python/JS/shell dependencies.
Also prove that editable executable assets are represented in ABI
registrations/change attribution, not merely copied into a visible folder.
If the current ABI cannot represent a required dependency, halt and report it
upstream. Do not silently freeze an intended evolvable component as a workaround.

### D. The scorer has an additional model-calling role

`_llm_match_deliverables()` can make a hard-coded Anthropic
`claude-sonnet-4-6` call when filename heuristics cannot resolve an output.
Choosing Copilot criterion judges does not reroute or disable this helper.

Declare its provider/model, credentials, request logging, identity evidence,
concurrency, and failure behavior. Native code catches helper failure and
continues unmatched; that must not silently turn provider unavailability into
an agent failure in the integration. Preserving, configuring, or changing the
helper requires an explicit protocol decision and parity coverage.

### E. Preserve the scorer's actual file-loading behavior

The current `score_rubric()` builds filename expectations from criterion-level
`deliverables`; it does not depend on every task having a top-level map.
Criteria without such lists load the complete output directory.

Preserve exact/fuzzy/helper matching, missing-file treatment, accepted DOCX
text, margin comments, and criterion-requested redline mode. The inspected
dataset currently has no `include_docx_redlines` flags; do not enable them
globally or rewrite rubrics as an unapproved "fix."

Host Pandoc absence is a grading prerequisite error, not a zero reward.
Judge exceptions become native `error` verdicts; accept a unit only when the
required judge profile is fully and validly graded. Preserve successful judge
artifacts when another judge fails.

### F. Dual scores and profile labels must not be simplified

For task/trial `u` and judge `j`:

```text
judge_all_pass[u,j] = 1 if every required criterion passes, else 0
native_score[u] = mean_j(judge_all_pass[u,j])
primary_all_pass_rate = mean_u(native_score[u])
both_agree_rate = mean_u(all required judges all-pass)
```

Retain native macro-by-task and pooled-by-criterion diagnostics separately.
Do not replace the headline with criterion pass rate, binary both-agree, or
Tau2/coding `pass^k`/`pass@k` semantics.

Current native profile tagging compares raw judge strings to the unprefixed
standard pair. A `copilot/` pair is therefore tagged `custom-dual` by the
inspected implementation, even when its suffixes match the standard IDs.
Record actual behavior; do not silently relabel results as standard.

### G. Native dashboards do not aggregate AHE repeated trials/checkpoints

`compare.collect_runs()` keeps only the latest run per model label and task.
Its model label does not identify an AHE checkpoint. Using it unchanged would
discard repeated trials or merge C0/C1 results.

Index reports by campaign/checkpoint/task/trial/unit identity. Preserve native
per-run report compatibility where practical, but derive research aggregates
from the verified AHE unit set, not timestamp-based discovery. Keep actual
model IDs unchanged; checkpoint labels belong in report metadata.

### H. Sandbox and model claims must remain accurate

Native LAB tools isolate workspace commands and document parsing through
Podman. AHE candidate Python/middleware is host-imported in the existing
trusted-local model. The LAB container does not automatically isolate every
NexAU import or middleware hook. Static checks are not a kernel boundary.

If required isolation is unsupported, halt and report it upstream rather than
altering the runner or claiming that isolation is already provided.

AHE config has evaluated/meta models, not a top-level judge section. Place
validated judge/helper settings under the supported `project.adapter_settings`
extension point. Runner schema changes are upstream work.

The inspected baseline lacked evaluated-role preflight coverage. Approved
branch-local fix AHE-001 adds it to the evaluated runtime using the existing
preflight function, rather than an adapter workaround. The separate
recommendations record distinguishes that repair from upstream suggestions.
Judge/helper integration remains benchmark-owned and must declare its own
native model contract.

For Copilot, AHE uses exact unprefixed model IDs; LAB's `copilot/` prefix
selects its transport. Confirm fresh settings before any new live job.
Record synthesized-label limits, unavailable usage/cost, ignored temperature,
and upstream retry uncertainty. Do not rank checkpoints by fabricated
zero-token/zero-cost values.

## 8. Validation and rollout gates

These checks are future tasks, not commands run during planning. The final
AHE CLI checks and every live baseline/campaign/model call are operator-owned:

| Gate | Required coverage |
|---|---|
| Offline data | Rich/minimal/shared-corpus tasks; nested IDs; absent optional metadata; corpus resolution; grouped split overlap |
| Native execution parity | Ordered calls; real binary outputs; read offsets; path restrictions; finish accepted/rejected/capped; no-tool stop; cumulative limits |
| Grading parity | Scoped/all-output criteria; exact/fuzzy/helper filenames; missing/corrupt files; comments/redlines; single/dual score including `0.5` |
| Failure handling | Missing converter; judge unavailable/malformed/mismatched model; interrupted sandbox; legitimate command/agent failures; valid zero preserved |
| AHE artifact contracts | Foreign request/split rejection; changed files/hashes; missing/gapped attempts; complete artifact map; canonical facts and schema-V3 lineage |
| Evolution protection | No held-out roots; no rubric access during rollout; no protected imports/routing edits; actual component attachment; clean committed candidate |
| Live baseline | Approved training units only; native grader and complete role-wide model proofs; labelled LAB-derived NexAU baseline |
| One-round pilot | C0 -> evidence -> Explore/Debugger -> committed C1 -> train evaluation -> attribution |
| Held-out finish | Evolution closure -> all-checkpoint validation -> selection -> C0/selected final test -> hash-verified export |

For each implementation chunk, prepare its explanatory record and commit it
before running its checks. Prefer targeted existing tests plus new adapter
fixtures; expand to full suites only when needed. Any failures are reported
before benchmark/methodology repairs.

After those implementation gates, the operator can use the runner's phase
controls for a one-round training pilot rather than an early full `ahe run`
that also consumes validation/test. The assistant supplies these commands
but does not execute them:

```bash
.venv/bin/ahe conformance --config ./harvey-ahe-project/ahe.yaml
.venv/bin/ahe validate --config ./harvey-ahe-project/ahe.yaml
.venv/bin/ahe run-round --config ./harvey-ahe-project/ahe.yaml --round 1
.venv/bin/ahe close-evolution --config ./harvey-ahe-project/ahe.yaml
.venv/bin/ahe run-validation --config ./harvey-ahe-project/ahe.yaml
.venv/bin/ahe run-final-test --config ./harvey-ahe-project/ahe.yaml
```

The pilot configuration must declare one round. These lines are an
operator-only handoff, not permission for the assistant to execute them.

One full-dataset, one-trial dual grading pass entails approximately
`114437 * 2 = 228874` criterion calls before helper calls, retries, or agent
generation. Pilot first and measure actual storage/runtime/call demand.
Fix benchmark/algorithm settings before held-out evaluation, and use a new
campaign directory whenever frozen inputs change.

## 9. Source map

Paths below are relative to the two local checkouts pinned in section 1.
Line references identify inspected contracts, not a claim that every
implementation task has already been proven executable.

| Source | Relevant contract |
|---|---|
| `AHE_Harvey/README.md:107-144` | Custom benchmark procedure and required documentation |
| `AHE_Harvey/docs/OPERATOR_GUIDE.md:29-172` | Project boundary, manifest, adapter dependencies, public roots |
| `AHE_Harvey/docs/HARNESS_ABI.md` | All seven component schemas/attachments and host-import limitation |
| `AHE_Harvey/docs/ADAPTER_GUIDE.md:110-446` | Splits, evaluated sessions, verification, V3 evidence, metrics, candidate validation |
| `AHE_Harvey/docs/PROMPT_FIDELITY.md` | Protected procedures, append-only supplements, fidelity/deviation matrix |
| `AHE_Harvey/docs/FUTURE_FEATURES.md:5-13` | Evaluation units are atomic; no generic mid-episode resume |
| `AHE_Harvey/src/ahe_runner/adapter.py:34-244` | Typed interface, fractional scores, trial facts, metric/resource shapes |
| `AHE_Harvey/src/ahe_runner/runtime.py:40-221,255-369,411-489,565-608` | Session API, model injection, capture boundary, mixed-call rejection |
| `AHE_Harvey/src/ahe_runner/evaluation_store.py:24-197` | Stable identities, immutable attempts, verified completion |
| `AHE_Harvey/src/ahe_runner/example/runtime_adapter.py:172-403` | V2 implementation and V3 evidence reference |
| `AHE_Harvey/src/ahe_runner/controller.py:121-131,168-266` | Full-run and phase-control ordering |
| `harvey-labs/lab_core/harness/run.py:32-71,211-254,291-390` | Native loading, shared corpus, eager manuals/scripts, task user message |
| `harvey-labs/lab_core/harness/agent_loop.py:24-151` | Ordered tools, termination, transcript/metrics |
| `harvey-labs/lab_core/harness/tools.py:38-231,382-576,708-788` | Seven tool schemas, native dispatch/parsing, finish gate and metrics |
| `harvey-labs/lab_core/sandbox/sandbox.py:1-29,147-235` | Native workspace, no-network sandbox, per-run lifecycle |
| `harvey-labs/lab_core/harness/skills/{docx,xlsx,pptx}/SKILL.md` | Baseline Office-document workflows and executable assets |
| `harvey-labs/lab_core/evaluation/scoring.py:152-204,268-399,433-551` | Extraction, matching helper, criterion scoping, grading errors |
| `harvey-labs/lab_core/evaluation/judge.py:74-120,251-323` | Judge clients, Copilot checks, prompt loading |
| `harvey-labs/lab_core/evaluation/prompts/rubric_criterion.txt` | Native criterion judge prompt |
| `harvey-labs/lab_core/evaluation/run_eval.py:26-82,85-171,176-258` | Task validation, per-judge records, dual profile and arithmetic |
| `harvey-labs/lab_core/evaluation/compare.py:128-345` | Native metrics and latest-run/report aggregation limits |
| `harvey-labs/lab_core/harness/adapters/copilot.py:23-83,104-177` | Exact loopback Chat Completions and unavailable usage |
| `harvey-labs/lab_core/paths.py:16-51` | `LAB_ROOT` and global task/results paths |
| `harvey-labs/tests/test_task_integrity.py`, `test_scoring.py`, `test_eval_strategies.py`, `test_sandbox.py`, `test_adapters.py` | Existing fixtures and targeted regression coverage |

Task schema/count findings were computed by parsing all 2,010 task JSON files
and resolving their document roots without executing an agent or grader.
Scenario-parent grouping in that inventory is only a structural observation,
not a selected or fully deduplicated research split.
