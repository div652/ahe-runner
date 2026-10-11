# AHE Prompt and Meta-Agent Fidelity Matrix

This runner preserves the original roles' operating contracts where they
define the AHE method, while recording product adaptations and known
configuration differences. Similar role names or prompt lengths are not
evidence of fidelity; prompts, tools, roots, budgets, compaction, persistence,
and enforced deliverables must agree.

Protected core prompts are packaged runner resources. Projects may append a
hashed role supplement, but cannot replace the core procedure without creating
an explicit methodology fork.

## Source Explore

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Explain framework and harness interfaces deeply enough for later implementation | Preserved | Source skill must cover architecture, configuration, tools, and failures |
| Evidence inputs | Installed framework, seed implementation, exact interfaces and source evidence | NexAU source, seed harness, manifest, packaged method reference, and only operator-declared public adapter roots | Hidden adapter implementation and held-out fixtures are absent from authorized roots |
| Analysis workflow | Trace call paths, configurations, templates, and implementation constraints | Core prompt requires source-backed architecture and attachment analysis | Read/search tools are bounded to declared roots |
| Tools and writable roots | Read source; persist one knowledge artifact | Read-only source roots; write only `knowledge/source-exploration/SKILL.md` | Tool context contains no candidate or benchmark-private write root |
| Output contract | Substantial reusable source skill | Native `SKILL.md` with frontmatter and required topics; the runner's minimum-content gate is weaker than a proof of full original depth | Completion is rejected if the skill gate fails |
| Iteration/output budget | Public role used a long one-shot budget and large output allowance | Default 200 iterations; output tokens come from the configured meta model rather than a separate role setting | Generated `agent.yaml` records effective values |
| Middleware/compaction | Role-specific context policy, approximately threshold 0.75 and 15 retained results in the pinned setup | Shared tested compactor, threshold 0.72 and preferred 12-iteration window; this is a documented implementation substitution | Large-result compaction tests preserve latest structure and references |
| Lifecycle | One Explore run creates reusable knowledge | Run once and freeze as a read-only skill; later mutation is intentionally not implemented | Skill hash and read-only state are stage outputs |

## Web Explore

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Find evidence-backed, implementable agent-design methods | Preserved with benchmark-family context and leakage guard | Skill must contain sources, patterns, evidence, and recommendations |
| Evidence inputs | Seeded public URLs plus broad research | Explicit configured research seed URLs, then Web search/read tools | Seed URLs are present in the Web query and campaign identity |
| Analysis workflow | Many searches; exact methods, parameters, results, negative findings, and implementation examples | Core prompt requires at least four distinct authoritative sources and concrete mechanism/tradeoff/verification mapping; this is less prescriptive than the original search count | Source metadata and recommendation sections are required |
| Tools and writable roots | Public Web access; persist one knowledge artifact | `web_search`, `web_read`, bounded file tools, and only the knowledge write root | Research streaks are bounded by durable-write gates |
| Output contract | Reusable research skill | Native `SKILL.md` loaded by Evolve through `LoadSkill` | Frontmatter/content gate and stage hash |
| Iteration/output budget | Long one-shot role with large output allowance | Default 200 iterations and configured meta-model output limit | Runtime config records effective values |
| Middleware/compaction | Role-specific compaction, approximately threshold 0.75 and 15 retained results | Shared threshold 0.72/preferred 12-window compactor; documented substitution | Compaction preserves latest message/tool-call structure |
| Lifecycle | One-shot research seed | Run once, then copied read-only into Evolve | Changing URLs, prompt, supplement, or skill invalidates/recomputes the relevant identity |

## Debugger

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Objective | Turn scalar outcomes plus dense traces/verifier output into causal failure evidence | Preserved per training task | Every task produces a structured diagnosis or the stage fails |
| Evidence inputs | Dense task-local execution and verifier artifacts supplied to the bundled tool-using Debugger | Verified schema-V3 task detail with adapter-verified canonical trial facts plus authorized raw training artifacts; validation/test remain unavailable | Submitted citations must fall inside nonempty returned line/byte ranges or search hits |
| Analysis workflow | Identify causal failure and reusable correction | Requires earliest failure, paired pass/fail comparison when available, limitations, root cause, recommendation, confidence, and falsifier | Diagnosis validator enforces all fields and paired-analysis rules |
| Tools and writable roots | Bundled public Debugger registers file read/write/replace, search/glob/list, shell, Web, and completion tools over normalized trace paths | Runner intentionally narrows this to bounded list/read/search over authorized training roots and no candidate write root | Root allowlists, nonempty access ledger, and structured citation gate |
| Output contract | Dense diagnosis consumed by Evolve | Strict JSON diagnosis plus task result and aggregate overview | Pydantic validation and request hash |
| Iteration/output budget | Bundled Debugger uses 25 NexAU iterations and 20,000 output tokens at temperature 0.4 | Configurable bounded NexAU agent, default 80 iterations; output/sampling use explicit shared meta-model settings | Runtime config/request records effective values |
| Middleware/compaction | Bundled Debugger uses long-output truncation plus threshold-0.75 tool-result compaction with a 10-iteration window | Shared threshold-0.72/preferred-12 adaptive compactor also bounds old tool-call arguments and fails closed if the target remains impossible | Read-heavy, write-heavy, latest-turn, ID, and error-flag regressions |
| Lifecycle | Diagnose completed training experience | Parallel per-task units are independently resumable and immutable once verified | Request/output hashes permit reuse only for the same evidence |

## Evolve

| Original function | Runner translation | Preserved or approved deviation | Executable check |
|---|---|---|---|
| Optimization objective | Improve one shared harness from completed experience | Adapter-native training metric and task states; one shared checkpoint harness | Every produced checkpoint is evaluated, including the last |
| Evidence inputs | Aggregate results, debugger reports, raw artifacts, Explore knowledge, and history | Verified training evidence, all task diagnoses, raw training artifacts, immutable checkpoint history, attribution, and both skills | Validation/test roots are never mounted |
| Analysis workflow | Synthesize causes, choose component changes, predict effects/risks, validate, attribute, and pivot/rollback | Preserved with explicit `KEEP`, `IMPROVE`, or `ROLLBACK + PIVOT` and selective checkpoint restore | Strict per-change manifest must cover the complete final component diff |
| Tools and writable roots | Read evidence/source/history; edit and commit the harness | Bounded read roots, Git/workspace tools, validation, and selective restore; only the workspace is writable | Clean committed `HEAD` and final-HEAD validation are mandatory |
| Output contract | Executable updated harness plus hypothesis record | Git commit(s), strict `change_manifest.json`, successful candidate validation, and `complete_task` | Stage rejects no-op, dirty, unvalidated, or uncovered changes |
| Iteration/output budget | Pinned Evolve configuration used 500 iterations, high output allowance, and a different temperature | Default 500 iterations; model, temperature, reasoning, and output limit are explicit campaign settings, not silently claimed equal | Request-boundary policy rejects drift before provider contact |
| Middleware/compaction | Pinned setup used approximately threshold 0.75 and 10 retained tool results | Shared adaptive compactor at threshold 0.72/preferred 12-window; it compacts only older results/arguments, preserves the latest iteration, and blocks an irreducible request | Read-heavy and write-heavy histories stay below target or fail before provider access |
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
