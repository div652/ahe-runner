# Domain guidance: LAB legal-work tasks (source exploration)

In this campaign the evaluated agent is a legal-work agent. It reads matter
documents inside a network-less podman sandbox and writes deliverable files
(memos, issue lists, marked-up drafts, spreadsheets, decks) that two judge
models grade against a hidden rubric.

**The Evolve Agent has NO pre-existing knowledge of this sandbox, the
workspace handle, the seed tools, or how judges read deliverables.** Your
skill is its **sole reference** for them. A wrong or missing fact here
becomes a broken candidate later. Everything below adds detail to the
required SKILL.md sections; it does not replace them.

# Where the project facts live (READ ONLY)

| Root | Read for this project |
|---|---|
| `adapter` | `ADAPTER.md`, the public adapter contract (sections 1-7). The adapter implementation is protected and not readable; cite `ADAPTER.md` section numbers. |
| `seed_harness` | `agent.yaml`, `systemprompt.md`, `tool_descriptions/*.tool.yaml`, `tools/*.py`, especially `tools/_common.py` |
| `manifest` | `harness_manifest.yaml`: components, `attachments`, `protected_agent_fields`, `limits` |
| `framework` | NexAU: tool execution, stop tools, prompt rendering, middleware, skills, sub-agents |

# Exploration Guide: what to extract

Each section names the required SKILL.md section where its findings belong.

## §H1. Sandbox access through the workspace handle (HIGHEST PRIORITY)

Put in `# Benchmark Adapter Contract` and `# Tools`. Answer:

- What does `handle_for(agent_state)` return, what is it bound to (the
  trial's NexAU session and its GlobalStorage), and what does a
  `LookupError` mean?
- Every `WorkspaceHandle` member: signature, return type, and which paths
  are writable (`/workspace`, `/workspace/output`) or read-only
  (`/workspace/documents`).
- `exec`: a fresh `bash -lc` per call; the timeout range and default;
  `ExecResult` fields; what `returncode` is on timeout. Make explicit that
  nothing (working directory, variables, background jobs) survives between
  calls.
- How the seed wraps the handle in `tools/_common.py`: `run_tool`,
  `error_text`, `resolve_read_path`, `resolve_write_path`,
  `resolve_search_path`. Give each path-resolution order with `file:line`.
- Why handles must never be cached and per-trial data must never live in
  module globals (trials run concurrently in one process).

## §H2. The seed tools, one by one (HIGHEST PRIORITY)

Put in `# Seed Harness and Evolvable Components`. Fill this table from the
YAML and Python, citing both:

| Tool | Arguments (required) | Path resolution | Output shape and caps | Error text | `disable_parallel` |
|---|---|---|---|---|---|
| `bash` | ... | ... | ... | ... | ... |
| `read` | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... |

At minimum, record:

- how `read` parses PDF/DOCX/PPTX/XLSX (the `parse-doc` command and its
  timeout);
- that `read` returns the whole parsed document unless `offset`/`limit` (in
  lines) are given;
- the `glob` and `grep` result caps;
- the `finish` rejection logic and its rejection counter in global storage.

## §H3. Stopping and run endings (HIGH PRIORITY)

Put in `# Agent Execution Loop`. Trace in NexAU, with `file:line`:

- how a stop tool's return value decides stop versus continue (the
  `{"status": "error", ...}` dict);
- every run ending and the `termination_reason` it produces (`ADAPTER.md`
  sections 1 and 3);
- what the model sees when a tool returns a string (`{"result": ...}`), and
  what happens when a tool raises (`agent_error`);
- how `max_iterations` and `max_context_tokens` are enforced, and what final
  text the run produces when each limit is hit.

## §H4. How deliverables are judged (HIGH PRIORITY)

Put in `# Benchmark Adapter Contract`. From `ADAPTER.md` section 2:

- the per-criterion verdicts, the trial score (share of criteria passed,
  averaged over both judges), the primary metric, the all-pass tie-breaker,
  and `judges_split`;
- the text extraction for each file format, and what it means for producing
  files. For example: a `.docx` is read as pandoc markdown, and pandoc is
  also in the sandbox, so an agent can preview the judges' view; spreadsheet
  formulas need saved values.
- deliverable-name matching and the judge-input size limit;
- the tools the image provides for producing files (section 6), and that
  nothing can be installed.

## §H5. Where each component attaches in this harness (HIGH PRIORITY)

Put in `# Configuration and Loading`, `# Skills and Progressive Disclosure`,
`# Middleware and Context Management`, `# Memory and Tracing`, and
`# Model Routing and Sub-agents`. Verify in NexAU source:

- **System prompt:** `system_prompt_type: file`. Where is it rendered as a
  Jinja template, and what happens to Jinja markup (two opening curly braces,
  or an opening curly brace followed by a percent or hash sign) in
  `systemprompt.md`?
- **Long-term memory:** the manifest attaches `LongTermMEMORY.md` as a
  system-prompt suffix. Where is it added, and when?
- **Tools:**
  - the `tools:` entry format (`name`, `yaml_path`);
  - the `binding` in the tool YAML;
  - how `tools.<module>:<function>` resolves (the harness directory on
    `sys.path`);
  - which parameters are injected (`agent_state`, `global_storage`,
    `sandbox`).
- **Middleware:**
  - the `middlewares:` entry format (an import string, or
    `{import, params}`) and how `params` reach `__init__`;
  - which hook methods are called, and that hooks are duck-typed;
  - hook order;
  - which hook inputs carry `agent_state`, so middleware could call
    `handle_for`.
- **Skills:** the `skills:` entry format (folder paths), frontmatter, and
  `LoadSkill`. Skill folders are copied to a host directory, not into the
  sandbox (`ADAPTER.md` section 5).
- **Sub-agents:**
  - the `sub_agents:` entry format (`name`, `config_path`);
  - shared GlobalStorage, so a sub-agent works in the same sandbox;
  - forced use of the evaluated model;
  - `max_iterations` of at most 200;
  - what a sub-agent can overwrite in `/workspace/output`.

## §H6. Copy-paste templates (HIGH PRIORITY)

Put in `# Safe Change Patterns`. Provide templates that you have checked
against the installed NexAU source and the seed. Mark any part you could not
verify as `UNVERIFIED`.

1. A new sandbox tool: a `.tool.yaml`, a Python function using `run_tool`
   and the handle, and the `agent.yaml` entry.
2. A tool that writes a helper script into `/workspace` with
   `handle.write_file` and runs it with one `exec`. Example uses: convert or
   preview a deliverable, or check that it opens.
3. A stop-tool rejection that returns `{"status": "error", ...}` with a
   capped retry counter in global storage.
4. A minimal middleware class and its `middlewares:` registration, for
   example an `after_tool` hook that shortens a very long tool result.
5. A text-only skill folder and its `skills:` registration.
6. A sub-agent folder (`agent.yaml`, `systemprompt.md`) and its
   registration, with `max_iterations` at most 200.

## §H7. Candidate rules (MEDIUM PRIORITY)

Put in `# Protected Boundaries` and `# Validation Checklist`. List:

- the rules in `ADAPTER.md` section 7;
- the manifest's `protected_agent_fields`;
- the manifest's `limits` (file and tree bytes).

For each rule, give one example edit that would be rejected.

## §H8. Gotchas: harnesses that look valid but break (MEDIUM PRIORITY)

Put in `# Failure Modes and Debugging`:

| Gotcha | Source (`file:line` or `ADAPTER.md` section) | Symptom in a trial |
|---|---|---|

Include at least:

- a tool signature that does not match its YAML schema;
- a tool that raises instead of returning error text;
- a cached handle or module-level per-trial state;
- Jinja syntax in the prompt;
- a tool file that is not registered;
- a skill that ships a script expected to run in `bash`;
- shell state assumed to survive between calls;
- deliverables written outside `/workspace/output`;
- a text or markdown file where `.docx` or `.xlsx` was requested;
- spreadsheet formulas without saved values;
- a whole long document read at once, causing context overflow;
- `finish` rejections exhausted.

# HARD RULES for this project

- Cite `file:line_range` or `ADAPTER.md` section numbers for every project
  claim. If a claim depends on the protected adapter code, write "per
  `ADAPTER.md` section N; implementation not readable".
- Describe mechanisms only. Do not guess task content, rubrics, or held-out
  tasks, and do not give task-specific advice.

# Quality Criteria (in addition to the core ones)

1. Using only the §H6 templates, an Evolve Agent can add a working sandbox
   tool, a middleware, a skill, and a sub-agent.
2. The §H2 table documents every seed tool.
3. Every run ending maps to its `termination_reason` and to the component
   that controls it.
4. Every gotcha has a citation and an observable symptom.
