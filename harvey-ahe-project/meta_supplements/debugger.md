# Domain guidance: LAB legal-work tasks

## Scoring facts that shape the diagnosis

- Each judge passes a trial only if **every** criterion passes. Trial score is
  the mean of the two judges' all-pass values (0, 0.5, or 1). One failed
  criterion is enough to fail a judge, so identify the specific failed
  criteria, not just the overall impression.
- Criteria failed by **both** judges are the strongest signal. A criterion
  failed by only one judge is borderline; say so.
- Outcome `judges_split` means one judge all-passed and the other did not.
- `[harvey-ahe judge-input limit]` in a judge's reasoning means the deliverable
  text was too large to grade and was failed without a model call.
- Judges read text extracted from the output files (`.docx` through pandoc,
  `.xlsx` through pandas, `.pptx` through markitdown, `.pdf` through
  pdfplumber). If the judge reasoning says content is absent, check whether
  it was in the file in a form the extraction drops.

## Where the evidence is

The task-detail document lists each trial with:

- `termination_reason`, `outcome`, `score`;
- `native_result`: `agent_error`, `finish_called`, `finish_summary`,
  `tool_call_counts`, `assistant_turns`, `wall_clock_seconds`,
  `output_files` (path, size, hash), `workspace_files`;
- `judges[]`: per judge `all_pass`, `n_passed`, `n_criteria`, and
  `criteria_results` (each with its verdict and reasoning);
- `artifacts`: the trial `result`, the `trace` (JSON lines: the user
  instruction, then every message with tool calls and tool results, then a
  termination event), the judges' `evaluator` score files, and copies of each
  graded `deliverable`;
- `readable_artifacts` (listed in the query): `readable_trace` is the same
  trace as `trace.md`, with one `## jsonl line N` header per event and real
  line breaks; `readable_deliverable` is each output file as the exact text
  the judges read (`.docx` through pandoc, `.xlsx` through pandas).

Read the readable trace and the deliverable text, not only the summary
fields. Do not open `.docx` or `.xlsx` files with `read_file`; they are
binary. Search the readable trace for the failed criterion's key terms to find
where the agent handled (or skipped) that point.

## Diagnosis order for this benchmark

Work through these in order and stop at the earliest consequential failure:

1. **How the run ended.**
   - `max_turns_exceeded`: where did the turns go (re-reading, retries,
     failing scripts, long drafting in many small edits)? Was anything written
     to `/workspace/output` before the limit?
   - `context_overflow`: which tool results were largest (often a whole
     long document returned by `read`)?
   - `no_tool_calls`: the agent replied with text and never called `finish`;
     were the deliverables already written?
   - `agent_error`: quote the error. A tool exception points to a tool
     implementation; a model or infrastructure error is
     `framework_or_adapter` or `environment`.
   - Rejected `finish` calls: what was missing?
2. **Deliverables.** Compare the instruction's requested files (names,
   formats, count, required sections) with `output_files`. Common failures:
   a file missing, in `/workspace` instead of `/workspace/output`, in the
   wrong format (text or markdown where `.docx` or `.xlsx` was asked for),
   empty or truncated, or a script that errored so the file was never
   produced.
3. **Reading.** Did the agent open every relevant document? Look for `read`
   results starting with `Error`, parse failures, partial reads through
   `offset`/`limit` that never continued, or decisions based on `grep` hits
   alone.
4. **Substance.** For each failed criterion, classify the judge's reason:
   an issue not covered, a factual or numerical error versus the documents,
   a required section or element missing, a wrong format or structure, or an
   instruction not followed (audience, length, scope). Group failures by
   class.
5. **Process.** Did the agent plan, keep notes, and verify its output before
   finishing? Did it finish while known gaps remained?

## Mapping to components (typical, not automatic)

- Missing or misformatted deliverables, early finish: `system_prompt`, the
  `finish` tool implementation or description, or `memory`.
- Incomplete reading, context overflow from large reads: `tool_implementation`
  (`read` paging or summaries), `tool_description`, or `middleware`.
- Turn exhaustion: `system_prompt` (planning), or `middleware` (budget
  reminders).
- Coverage and accuracy gaps: `system_prompt` (review protocol), `skill`
  (text-only procedure), or `sub_agent` (bounded reviewer or reader).
- Tool exceptions or confusing tool errors: `tool_implementation` or
  `tool_description`.

## Paired comparison and reporting

With one trial per task, a within-task passing/failing pair is usually
unavailable; give "single trial" as the limitation. You may still contrast
passed and failed criteria inside the same trial in the root-cause text.

Use generic `recurrent_pattern_key` labels that can recur across tasks, for
example `deliverable-format-mismatch`, `incomplete-document-reading`,
`turn-budget-exhaustion`, `context-overflow-large-read`, `issue-coverage-gap`,
`factual-error-vs-source`, `early-finish`, `tool-error-unhandled`.

Describe mechanisms, not legal content. Do not copy criterion wording, party
names, figures, or conclusions from a training task into recommendations.
