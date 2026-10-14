# Domain guidance: LAB legal-work tasks (evolution)

You are improving a legal-work agent. It reads matter documents in a
network-less sandbox and writes deliverable files to `/workspace/output`.
Two judges grade those files against a hidden rubric.

**The optimization target is the mean criterion pass rate**: the share of
rubric criteria each judge passes, averaged over the two judges and all
trials. LAB's dual all-pass rate (a judge passes every criterion) is
reported and only breaks ties.

# Core Principles for This Benchmark

## 1. Every criterion counts

Within a task, every rubric criterion carries equal weight, so a trial gains
with each additional criterion it passes. Gains come from removing the
specific failures that cost criteria:

- a missing or misformatted deliverable (this fails many criteria at once);
- an unread document;
- an omitted issue;
- a factual slip;
- an early finish.

Prefer changes that make every run reliably complete and correct. More prose
is not the goal.

## 2. Evidence-driven, per failed criterion

Before making any change, you must have:

1. **Failure evidence:** which training task keys failed, and which kinds of
   criteria both judges failed (coverage, accuracy, format, instruction
   following). Quote the debugger diagnoses or the judge reasoning.
2. **Root cause in the trace:** the earliest step where the run went wrong.
   For example: a document never opened, a deliverable written as `.md`,
   turns spent on a failing script.
3. **Targeted fix:** a change at the component that controls that step.
4. **Predicted impact:** which task keys should flip, and which trace
   feature should change.

## 3. Mechanisms over advice

Enforcement strength, from strongest to weakest:

1. tool implementation;
2. middleware;
3. tool description;
4. skill;
5. system prompt.

If a failure recurs after a prompt rule, move the fix to a mechanism (for
example, a stricter `finish` check or a middleware reminder) rather than
rewording the rule.

# Components in This Harness

| Component | Files | Behaviour specific to this harness | Typical use here |
|---|---|---|---|
| System prompt | `systemprompt.md` | Rendered as a Jinja template: never write Jinja markup (two opening curly braces, or an opening curly brace followed by a percent or hash sign) | Work protocol: inventory → read → notes → draft → verify → finish |
| Tool description | `tool_descriptions/*.tool.yaml` | Sent as an API tool schema (`openai` mode) | Path rules, output formats, pitfalls |
| Tool implementation | `tools/*.py` | Runs on the host; reaches the sandbox only via `handle_for(agent_state)`; must catch every exception | Deliverable checks, paged reading, previewing the judges' view |
| Middleware | `middleware/*.py` + `agent.yaml` `middlewares:` | Runs on the host; duck-typed hooks; can rewrite messages and tool results | Turn-budget reminders, compacting large tool results |
| Skill | `skills/<name>/SKILL.md` + `agent.yaml` `skills:` | Text only; its folder is not visible in the sandbox | Reusable how-tos (for example, a `.docx` with tables) |
| Sub-agent | `sub_agents/<name>/agent.yaml` + `systemprompt.md` + `agent.yaml` `sub_agents:` | Shares the parent's sandbox; forced onto the evaluated model; `max_iterations` at most 200 | A bounded document reader or reviewer |
| Long-term memory | `LongTermMEMORY.md` | Appended to the system prompt | Durable procedures, never task facts |
| Short-term memory | `ShortTermMEMORY.md` | Runtime-owned | Do not modify |

## Registering New Components

**Creating a file is NOT enough. Register it in `agent.yaml`:**

- **New tool:** a `.tool.yaml` (with `binding: tools.<module>:<function>`),
  a `tools/<module>.py`, and a `tools:` entry with `name` and `yaml_path`.
- **New middleware:** a `middleware/<module>.py` and a `middlewares:` entry
  with `import: middleware.<module>:<Class>` and `params:`.
- **New skill:** a `skills/<name>/SKILL.md` with `name` and `description`
  frontmatter, and a `skills:` entry with the folder path.
- **New sub-agent:** a `sub_agents/<name>/agent.yaml` and a `sub_agents:`
  entry with `name` and `config_path`.

The harness folder is on `sys.path`, so `binding: tools.finish:finish` loads
`tools/finish.py`. Confirm exact formats in the framework skill before
editing.

# Protected Invariants (the adapter or manifest rejects violations)

- **Fixed fields:** `finish` stays the stop tool. `stop_tools: [finish]`,
  `max_iterations: 200`, `tool_call_mode`, and the `llm_config` model fields
  are fixed. Sub-agents may not exceed 200 iterations.
- **Sandbox access:** harness Python reaches the sandbox only through
  `harvey_ahe.workspace.handle_for(agent_state)`, called on every tool call.
  Never cache a handle or keep per-trial data in module globals: trials run
  concurrently in one process.
- **Banned imports and calls.** Harness Python may not import:
  - `ahe_runner`, `benchmark_adapter`, or `lab_core`;
  - any `harvey_ahe` module other than `harvey_ahe.workspace`;
  - process, network, FFI, or dynamic-import modules.

  It may not call `eval`, `exec`, `compile`, `__import__`, `os.system`,
  `os.popen`, `os.fork*`, `os.exec*`, or `os.spawn*`.
- **Module names:** top-level harness files and folders must not reuse the
  name of a standard-library, protected, or installed module (for example
  `docx.py` or `json/`).
- **Identifiers:** no training task identifiers anywhere, and no grader
  identifiers in `.py` or `.yaml` files.

# Mechanics You Must Get Right

- **New or changed tool.** The Python parameters, minus `agent_state`,
  `global_storage`, and `sandbox`, must equal the schema `properties`.
  Parameters without defaults must equal `required`. Catch every exception
  and return error text; a raised exception ends the trial as `agent_error`.
  Keep `disable_parallel: true` for tools that write to the sandbox.
- **Shell.** Every `exec` is a fresh shell (default timeout 60 s, maximum
  600). Multi-step work must be one command, or a script written to
  `/workspace` first. There is no network, and nothing can be installed.
- **Rejecting `finish`.** Return `{"status": "error", "error": "..."}`. Keep
  a cap on rejections so a run cannot loop.
- **Skills.** Their folder files cannot be run by `bash`. To run helper
  code, embed the script in a tool implementation that writes it into the
  sandbox.
- **Middleware.** Keep any rewriting auditable, and never drop the
  instruction or the deliverable requirements.
- **What judges read.** Judges read extracted text:
  - `.docx` through pandoc (the agent can run pandoc in the sandbox to
    preview it);
  - `.xlsx` through pandas, which reads only saved cell values, so
    recalculate formulas with LibreOffice or write the values;
  - `.pptx` through markitdown;
  - `.pdf` through pdfplumber.

  Images and layout are invisible to judges. A criterion whose judge input
  exceeds 240,000 characters fails without a judge call.
- **Context.** `read` returns whole documents. Several long documents can
  exhaust `max_context_tokens` (200000). If you change that value, keep it
  within the evaluated model's real context window.

# Analysis Approach for This Benchmark

Within the core evidence order:

1. **Tabulate each training task key:** `termination_reason`, `score`, and
   each judge's `n_passed`/`n_criteria`.
2. **Separate broad losses from partial losses.**
   - Broad loss: a missing deliverable, `agent_error`, or a turn or context
     limit fails most criteria at once.
   - Partial loss: most criteria pass, some fail.

   Fix broad losses first: they are usually cheap and cost the most
   criteria.
3. **Check deliverables first.** Confirm in `output_files` that each one
   exists, in the requested format and location, before reading the judge
   reasoning.
4. **Group partial losses.** Group their failed criteria by class across tasks,
   and give the most weight to criteria both judges failed.
5. **Verify every diagnosis in the raw trace.** Do not rely on summaries
   alone.
6. **Round 2 onward:** list which task keys flipped and which regressed. If
   regressions outnumber flips, diagnose them before adding anything new.

# Intervention Families (options, not a required list)

| Family | Evidence that calls for it | Usual component | Main risk |
|---|---|---|---|
| Deliverable contract: extract the requested files, formats, and sections; `finish` checks existence, extension, non-empty content; preview the judges' view | Missing or misformatted deliverables, early finish | `finish` tool + system prompt | Blocking legitimate finishes |
| Document coverage: inventory first, read everything (page long files), notes in a scratch file | Unopened documents, coverage gaps | System prompt + `read` tool | Turn and context cost |
| Context and turn budget: page or summarize large reads; remaining-turn reminders; write deliverables early | `context_overflow`, `max_turns_exceeded` | Middleware + `read` tool | Losing facts in summaries |
| Review before finishing: re-check each deliverable against the checklist and sources; optional bounded reviewer sub-agent | Accuracy or instruction failures in otherwise complete work | System prompt, skill, or sub-agent | Cost; reviewer overwriting files |
| Durable procedures in memory | The same how-to failure across tasks | `LongTermMEMORY.md` or skill | Prompt growth |

# Regression Risks to State in the Change Manifest

- Longer prompts consume context.
- Aggressive compaction can lose facts needed later.
- Stricter `finish` checks can block legitimate completions.
- Sub-agents multiply cost and can overwrite each other's files in the
  shared sandbox.
- Any new tool error path can end a trial if it raises.

# Safety Constraints for This Benchmark

- Never copy training task identifiers, party names, figures, legal
  conclusions, or criterion wording into the harness. Learn general classes
  from judge reasoning; do not turn individual criteria into a checklist.
- Never add text addressed to the judges, pad deliverables, or try to
  influence grading in any way other than doing the work better.
- Treat runtime-injected benchmark actions and all project supplements as
  protected, unmodifiable campaign inputs.
