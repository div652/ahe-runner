# Public Adapter Contract: LAB legal-work tasks

This is the public contract between the evolvable NexAU harness and the
protected LAB adapter. Source Explore, Debugger, and Evolve may read it. The
adapter code, task definitions, documents, rubrics, and held-out splits are
protected and are not readable by meta-agents.

## 1. What one trial is

1. The adapter copies one task's matter documents into a fresh, per-trial
   scratch directory and starts a LAB podman sandbox over it. The sandbox has
   no network (`--network=none`) and drops all capabilities.
2. Inside the sandbox:

   | Path | Meaning | Writable |
   |---|---|---|
   | `/workspace/documents` | the matter documents (PDF, DOCX, XLSX, PPTX, ...) | no |
   | `/workspace/output` | deliverables; **only files here are graded** | yes |
   | `/workspace` | scratch space for notes, scripts, intermediate files | yes |

3. The evaluated NexAU agent receives the task instructions as its single user
   message. The instructions name the deliverables expected in the output
   directory.
4. The run ends at the first of: a successful `finish` call, a model reply with
   no tool call, `max_iterations` (200, protected), or a context-window
   overflow. Any other exception ends the trial as `agent_error`; it is still
   graded on whatever is in the output directory.
5. The adapter copies `/workspace/output` out, stops the sandbox, and grades
   the copy.

## 2. How a trial is scored

- Two judge models grade the output independently against the task's rubric,
  one verdict (pass or fail) per criterion. The rubric is never shown to the
  agent or to meta-agents.
- A judge "all-passes" a trial only if **every** criterion passes.
- Trial score = mean of the two judges' all-pass values, so 0, 0.5, or 1.
- **Primary metric**: mean trial score (`primary_all_pass_rate`). Higher is
  better. Ties are broken by `mean_criterion_pass_rate`, which is otherwise
  diagnostic only.
- Diagnostic class: a trial is `passing` only when **both** judges all-pass.
  A 0.5 trial (judges disagree) is `failing` and its outcome is `judges_split`.
- Judge input limit: if the formatted judge prompt for a criterion would
  exceed 240,000 characters, that criterion is scored `fail` without a model
  call and the reasoning contains `[harvey-ahe judge-input limit]`. Very large
  or padded deliverables therefore fail rather than error.
- Deliverable matching: expected deliverable names are matched to produced
  files exactly, then fuzzily, then by a helper model. Unmatched deliverables
  are graded as missing.
- Judges read text extracted from the output files, not the files
  themselves: `.docx` through pandoc markdown (tracked changes accepted,
  unless a criterion asks to see redlines; Word comments appended), `.xlsx`
  through pandas (one table per sheet; pandas reads the cell values saved in
  the file, so a formula written by openpyxl and never recalculated reads as
  empty), `.pptx` through markitdown, `.pdf`
  through pdfplumber, and other files as plain text. Anything the
  conversion drops (images, layout, colour) is invisible to judges. An
  unreadable file becomes an `(error reading <name>: ...)` line.
- The `finish` summary and the final assistant text are telemetry only. They
  are never graded.

## 3. Debugger evidence per training trial

Each training trial records:

- the task instructions and the full NexAU message trace (assistant text,
  tool calls, tool results);
- `termination_reason`: `finish_tool`, `no_tool_calls`,
  `max_turns_exceeded`, `context_overflow`, or `agent_error` (with the error
  text);
- `finish_called`, `finish_summary`, tool-call counts, assistant turns, and
  wall-clock seconds;
- listings with hashes of `/workspace/output` and `/workspace`, and copies of
  the graded output files;
- both judges' per-criterion verdicts and reasoning.

Validation and test tasks are never shown during evolution.

## 4. How harness tools reach the sandbox

Harness Python (tools, middleware) runs **in the AHE host process**, not in
the sandbox. It reaches the trial's sandbox only through one protected module:

```python
from harvey_ahe.workspace import handle_for, OUTPUT_PATH


def my_tool(path: str, agent_state) -> str:
    handle = handle_for(agent_state)  # this session's sandbox
    result = handle.exec(f"wc -c {path}", timeout=60)
    return result.stdout
```

`handle_for(agent_state)` returns this session's `WorkspaceHandle`. The adapter
binds it to the trial's own NexAU session (one NexAU `GlobalStorage` per
session), so sub-agents of the same trial share it and other trials cannot
reach it. Do not cache handles in module globals: trials run concurrently in
one process.

`WorkspaceHandle` API:

| Member | Behaviour |
|---|---|
| `exec(command, *, cwd="/workspace", timeout=None)` | `bash -lc command` in a **fresh** `podman exec`; no state (cwd, variables, background jobs) survives between calls. `timeout` is an int in 1..600 seconds; the default is 60. Returns `ExecResult` with `stdout`, `stderr`, `returncode` (`None` on timeout), `timed_out`, and `ok`. |
| `read_file(path)` / `write_file(path, content)` | bytes in, bytes or str out; writes only under `/workspace` and never in `documents` |
| `exists(path)` | sandbox path test |
| `to_host(path)` | resolved host path behind a sandbox path, for host-side traversal (rejects mount escapes; guard symlinks yourself) |
| `assert_sandbox_path(path)` / `is_writable(path)` | path policy checks (static) |
| `mark_finished(summary)` | latches an accepted finish (telemetry) |
| `DOCUMENTS_PATH`, `OUTPUT_PATH`, `WORKSPACE_PATH` | the three paths in section 1 |

After the trial ends the handle is revoked; late calls raise `LookupError`.

## 5. NexAU tool facts that matter here

- Tool-call mode is structured `openai` (protected). Tool descriptions travel
  as API tool schemas, not as system-prompt text.
- A tool that returns a string reaches the model as `{"result": "<string>"}`.
- NexAU re-raises tool exceptions, which ends the trial as `agent_error`.
  Tools must catch their own exceptions and return error text.
- `finish` is the protected stop tool. NexAU stops the run when a stop tool
  returns anything other than a dict with `"status": "error"`. To reject a
  finish attempt and keep the agent working, return
  `{"status": "error", "error": "<reason>"}`.
- Each tool's Python signature, minus the injected `agent_state`,
  `global_storage`, and `sandbox`, must match its YAML `input_schema`
  properties, and parameters without defaults must equal `required`.
- Skills: NexAU copies skill folders into its own host working directory,
  **not** into the podman sandbox. Skill text can guide the agent, but files
  shipped inside a skill folder are not reachable from the sandbox `bash`.
  Code that must run in the sandbox has to be written there by a tool (for
  example `write`) or embedded in a tool implementation.

## 6. The seed harness

The seed ports LAB's native tool set and system prompt onto NexAU: `bash`,
`read` (parses PDF/DOCX/PPTX/XLSX with `parse-doc`), `write`, `edit`,
`glob`, `grep`, and `finish`. Calls run one at a time, as in LAB. The seed
has no skills, sub-agents, or middleware. Differences from LAB's native
harness:

- No skills: LAB's built-in skill files are not shipped.
- Tool descriptions drop references to those skills; `bash` states that each
  call is a fresh shell and that the sandbox has no network.
- `finish` rejections use the `status: error` dict above instead of LAB's
  plain string.

The sandbox image provides Python (python-docx, openpyxl, python-pptx,
docxtpl), Node.js (`docx`, `pptxgenjs`; `NODE_PATH` is set), pandoc, and
LibreOffice for producing binary deliverables. Nothing can be installed at run
time (no network).

## 7. Candidate rules enforced by the adapter

In addition to the AHE harness manifest checks, a candidate is rejected if:

- it has no tool named `finish`, or a sub-agent with `max_iterations` above
  200;
- harness Python imports `ahe_runner`, `benchmark_adapter`, `lab_core`, or
  any `harvey_ahe` module other than `harvey_ahe.workspace` (importing
  `nexau`, for example to subclass `Middleware`, is allowed);
- harness Python imports process, network, FFI, or dynamic-import modules
  (for example `subprocess`, `socket`, `ssl`, `urllib`, `requests`, `httpx`,
  `http`, `ctypes`, `multiprocessing`, `pty`, `importlib`, `builtins`), or
  calls `eval`, `exec`, `compile`, `__import__`, `os.system`, `os.popen`,
  `os.fork*`, `os.exec*`, `os.spawn*`, or `os.posix_spawn*`;
- a top-level harness module or package name collides with a protected,
  standard-library, or already-installed module (`tools` is fine; `json` or
  `nexau` is not);
- `.py` or `.yaml` files contain grader-internal identifiers, or any text
  file contains a training task identifier.

These are static checks, not an operating-system boundary. Treat them as
rules, not as obstacles to work around.
