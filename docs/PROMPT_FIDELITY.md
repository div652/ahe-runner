# AHE Prompt and Meta-Agent Fidelity Matrix

This runner preserves the original roles' operating contracts where they
define the AHE method, while recording product adaptations and known
configuration differences. Similar role names or prompt lengths are not
evidence of fidelity; prompts, tools, roots, budgets, compaction, persistence,
and enforced deliverables must agree.

Protected core prompts are packaged runner resources. Projects may append a
hashed role supplement, but cannot replace the core procedure without creating
an explicit methodology fork. Resolved prompts are appended to NexAU after
template rendering, so supplement text is preserved literally.

The rendered part is a single newline, never empty: for an empty system
prompt NexAU 0.3.9 substitutes its default one-line prompt and drops the
appended text. From 0.4.0 (commit `bc466ac`) through 0.8.0 the runner set an
empty system prompt, so meta agents run from this repository's `main` in that
period received NexAU's default prompt instead of their protected prompt.
The harveybench branches pass the prompt as a file and were not affected. A
test now checks the instructions a mocked model endpoint receives.

## Source Explore

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Explain framework and harness interfaces deeply enough for later implementation | Preserved | Source skill must cover architecture, configuration, tools, and failures |
| Evidence inputs | Installed framework, seed implementation, exact interfaces and source evidence | NexAU source, seed harness, manifest, packaged method reference, and only operator-declared public adapter roots | Hidden adapter implementation and held-out fixtures are absent from authorized roots |
| Analysis workflow | Trace call paths, configurations, templates, and implementation constraints | Core prompt requires source-backed architecture and attachment analysis | Read/search tools are bounded to declared roots |
| Tools and writable roots | Read source; persist one knowledge artifact | Guarded native file tools over read-only source roots; write only the knowledge root; optional Docker shell sees only declared mounts | Tool context contains no candidate or benchmark-private write root |
| Output contract | Substantial reusable source skill | Native `SKILL.md` with frontmatter and required topics; the runner's minimum-content gate is weaker than a proof of full original depth | Completion is rejected if the skill gate fails |
| Iteration/output budget | Public role used a long one-shot budget and large output allowance | Default 200 iterations; `meta.roles.explorers` can set explicit temperature/output overrides | Generated `agent.yaml` records effective values |
| Middleware/compaction | Role-specific context policy, threshold 0.75 and 15 retained iterations in the pinned setup | Preserved with NexAU long-output handling (20,000 characters) and context compaction at threshold 0.75/15 iterations | Large-result tests preserve latest message/tool-call structure and durable full outputs |
| Lifecycle | One Explore run creates reusable knowledge | Run once and freeze as a read-only skill; later mutation is intentionally not implemented | Skill hash and read-only state are stage outputs |

## Web Explore

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Find evidence-backed, implementable agent-design methods | Preserved with benchmark-family context and leakage guard | Skill must contain sources, patterns, evidence, and recommendations |
| Evidence inputs | Seeded public URLs plus broad research | Explicit configured research seed URLs, then Web search/read tools | Seed URLs are present in the Web query and campaign identity |
| Analysis workflow | Many searches; exact methods, parameters, results, negative findings, and implementation examples | Core prompt requires at least four distinct authoritative sources and concrete mechanism/tradeoff/verification mapping; this is less prescriptive than the original search count | Source metadata and recommendation sections are required |
| Tools and writable roots | Public Web access; persist one knowledge artifact | `web_search`, `web_read`, guarded native file tools, only the knowledge write root, and optional declared Docker shell | Research streaks are bounded by durable-write gates |
| Output contract | Reusable research skill | Native `SKILL.md` loaded by Evolve through `LoadSkill` | Frontmatter/content gate and stage hash |
| Iteration/output budget | Long one-shot role with large output allowance | Default 200 iterations and explicit explorer-role model overrides | Runtime config records effective values |
| Middleware/compaction | Role-specific compaction at threshold 0.75 with 15 retained iterations | Preserved with 20,000-character long-output handling and threshold-0.75/15-iteration compaction | Compaction preserves latest message/tool-call structure |
| Lifecycle | One-shot research seed | Run once, then copied read-only into Evolve | Changing URLs, prompt, supplement, or skill invalidates/recomputes the relevant identity |

## Debugger

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Turn scalar outcomes plus dense traces/verifier output into causal failure evidence | Preserved per training task; an ordinary task-level Debugger failure becomes an explicit unavailable record so Evolve can inspect evidence directly, while infrastructure failures still stop/retry | Every task produces a diagnosis or immutable unavailable record |
| Evidence inputs | Dense task-local execution and verifier artifacts supplied to the bundled tool-using Debugger | Verified schema-V3 task detail with adapter-verified canonical trial facts plus authorized raw training artifacts; validation/test remain unavailable | Submitted citations must fall inside nonempty returned line/byte ranges or search hits |
| Analysis workflow | Identify causal failure and reusable correction | Requires earliest failure, paired pass/fail comparison when available, limitations, root cause, recommendation, confidence, and falsifier | Diagnosis validator enforces all fields and paired-analysis rules |
| Tools and writable roots | Bundled public Debugger registers file read/write/replace, search/glob/list, shell, Web, and completion tools over normalized trace paths | Guarded native read/search/glob/list plus a task-private scratch write root; optional Docker shell; no candidate workspace or held-out root | Root allowlists, post-truncation access ledger, and structured citation gate |
| Output contract | Dense diagnosis consumed by Evolve | Strict JSON diagnosis plus task result and aggregate overview | Pydantic validation and request hash |
| Iteration/output budget | Bundled Debugger uses role-specific NexAU iteration, output, and temperature settings | Configurable iterations plus explicit `meta.roles.debugger` temperature/output overrides; effective settings are request-bound | Runtime config/request records effective values |
| Middleware/compaction | Bundled Debugger uses long-output truncation plus threshold-0.75 tool-result compaction with a 10-iteration window | Preserved with 10,000-character long-output handling and threshold-0.75/10-iteration compaction; citation recording observes only post-truncation text | Read-heavy, truncation-boundary, latest-turn, ID, and error-flag regressions |
| Lifecycle | Diagnose completed training experience | Parallel per-task units are independently resumable and immutable once verified | Request/output hashes permit reuse only for the same evidence |

## Evolve

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Optimization objective | Improve one shared harness from completed experience | Adapter-native training metric and task states; one shared checkpoint harness | Every produced checkpoint is evaluated, including the last |
| Evidence inputs | Aggregate results, debugger reports, raw artifacts, Explore knowledge, and history | Verified training evidence, all task diagnoses, raw training artifacts, immutable checkpoint history, attribution, and both skills | Validation/test roots are never mounted |
| Analysis workflow | Synthesize causes, choose component changes, predict effects/risks, validate, attribute, and pivot/rollback | Preserved with explicit `KEEP`, `IMPROVE`, or `ROLLBACK + PIVOT` and selective checkpoint restore | Strict per-change manifest must cover the complete final component diff |
| Tools and writable roots | Read evidence/source/history; edit and commit the harness | Guarded native file tools, Web/Git/validation/restore tools, optional Docker shell, and the packaged NexAU evolution guide; only the workspace is writable | Clean committed `HEAD` and final-HEAD validation are mandatory |
| Output contract | Executable updated harness plus hypothesis record | Git commit(s), strict `change_manifest.json`, successful candidate validation, and `complete_task` | Stage rejects no-op, dirty, unvalidated, or uncovered changes |
| Iteration/output budget | Pinned Evolve configuration used 500 iterations, high output allowance, and role-specific temperature | Default 500 iterations with explicit `meta.roles.evolve` temperature/output overrides; model and reasoning remain campaign settings | Request-boundary policy rejects drift before provider contact |
| Middleware/compaction | Pinned setup used threshold 0.75 and 10 retained iterations | Preserved with 10,000-character long-output handling and threshold-0.75/10-iteration compaction, including campaign-compatible emergency summarization | Read-heavy and write-heavy histories preserve latest structure and durable full outputs |
| Lifecycle and persistence | One global update role with Git-backed progress | One logical resumable stage per round; infrastructure retry continues from the last clean Evolve commit | Attempt history and recovery artifacts remain immutable |

## Cross-role protected inputs

The following are frozen into campaign identity:

- resolved `MetaPromptContext`;
- append-only Source, Web, Debugger, and Evolve supplements;
- declared public adapter source roots;
- declared adapter code/data dependencies;
- Web research seed URLs;
- complete runner package content;
- core prompts, tool descriptions, tool implementation, and middleware;
- packaged AHE method reference.

Changing any item prevents resume in the existing campaign directory.

## Known scope and methodology choices

- Explore outputs remain immutable research seeds. The paper discusses later
  refinement, but the pinned public implementation does not establish that as
  a required executable mechanism.
- Attribution is heuristic prediction matching for a combined candidate, not
  isolated causal proof for each edit.
- Rollback is selected by Evolve; there is no automatic aggregate-score
  rollback.
- The evaluated-session ABI is text-interactive. Typed multimodal content,
  dynamic tool sets, interruptions, and event streams are future contracts.
- Candidate and adapter Python execute on the host in the trusted-local model;
  static validation is not a kernel sandbox.
