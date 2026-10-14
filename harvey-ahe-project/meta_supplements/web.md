# Domain guidance: LAB legal-work tasks (web research)

Use the current year in searches about recent agent techniques.

In this campaign an Evolve Agent improves a NexAU agent that does
document-heavy legal work. **The Evolve Agent has NO pre-existing knowledge
of how reliable agents read long document sets, cover a review task
completely, or produce office files offline.** Your skill is its **sole
reference** for those techniques. Provide concrete, specific, implementable
knowledge, organised in the required SKILL.md sections.

# The benchmark family

- **Input.** Each task gives a legal instruction and a set of matter
  documents (agreements, filings, correspondence, spreadsheets) in a
  read-only folder.
- **Output.** The agent must produce one or more files in an output folder:
  a memo, an issues list, a marked-up draft, a spreadsheet, or a short deck.
- **Grading.** Two judge models grade the files' text against a hidden
  rubric of several dozen criteria, one pass or fail per criterion. The
  score is the share of criteria passed, averaged over the two judges.
- **Text conversion.** Judges read text extracted from the files:
  - `.docx` through pandoc;
  - `.xlsx` through pandas, which reads only the cell values saved in the
    file;
  - `.pptx` through markitdown;
  - `.pdf` through pdfplumber.
- **Tools and limits.** The agent has a sandboxed shell and file tools, no
  network, and at most 200 model turns.

# ⚠️ CRITICAL RULES

1. **Record exact, implementable detail. Reject vague summaries.**
   - ✅ "openpyxl stores formulas as text and never computes them; a reader
     that loads saved values sees an empty cell until LibreOffice or Excel
     recalculates the file" (plus the doc URL)
   - ❌ "be careful with spreadsheet formulas"
   - ✅ "keep a scratch file listing each document, pages read, and issues
     found; re-read it before drafting" (plus the source and its reported
     effect)
   - ❌ "use good note-taking"
2. **Cite every claim** with its URL, and the library version when relevant.
3. **Translate every finding into a mechanism in this harness** (see the
   recommendation format below).
4. **Write early, update often**, as the core rules require.

# Research Protocol

## Phase 1: seed URLs (MANDATORY)

Read the seed URLs in the task query first: NexAU, python-docx, openpyxl,
python-pptx. Extract the API facts that decide what judges will see:

- headings, tables, comments, and tracked changes in python-docx (what it
  supports and what needs raw XML);
- formulas, number formats, and multiple sheets in openpyxl;
- slide text, notes, and tables in python-pptx.

Then write the skill file.

## Phase 2: conceptual searches (target 12-18)

Example directions. Adapt the wording and add the current year where
relevant:

1. LLM agent long-document reading: chunking, paging, and structured notes
2. Multi-document review: cross-referencing definitions, dates, amounts,
   parties
3. Public contract-review clause and issue taxonomies
4. Legal memo structure (IRAC, CREAC) and pinpoint citation practice
5. python-docx tracked changes (`w:ins`/`w:del`) and comments
6. Converting markdown to docx with pandoc: reference docs, tables, track
   changes
7. openpyxl formulas and cached values; headless LibreOffice recalculation
8. markitdown and pdfplumber: what they keep and what they drop
9. Agent self-verification and pre-completion checklists
10. Context compaction and tool-output truncation in agent harnesses
11. Turn-budget awareness and stopping policies for long agent tasks
12. Reader or reviewer sub-agents: cost and integration patterns
13. LLM-as-judge rubric grading: failure modes for completeness and
    precision (to design self-checks, not to game judges)
14. Negative results: agent techniques that hurt long-task reliability

For each result, skip overview articles. Prefer documentation, papers with
experiments, and open-source agent code. If a page is inaccessible, write
`INACCESSIBLE: <url>` and move on.

# Where findings go in the required sections

| Topic (priority order) | Required section(s) |
|---|---|
| 1. Reading long document sets reliably | `# Memory and Context Management`, `# Transferable Patterns` |
| 2. Complete coverage of a review task | `# Evidence-grounded State Management` |
| 3. Grounded legal writing | `# Transferable Patterns` |
| 4. Producing office files offline, and what survives extraction | `# Tool-use Reliability` |
| 5. Instruction compliance and checking before finishing | `# Recovery and Stopping Policies` |
| 6. Turn budget, compaction, sub-agents | `# Memory and Context Management`, `# Sub-agent and Delegation Patterns` |
| Negative results | `# Anti-patterns and Failure Modes` |

# Recommendation format (`# Recommendations for This Harness Family`)

Give the top 8-10 recommendations, each with:

- **What:** the exact change.
- **Why:** the source and any measured effect (`NO DATA` if none).
- **How in this harness:** the file and component:
  - `systemprompt.md`;
  - `tool_descriptions/*.tool.yaml`;
  - `tools/*.py` (Python on the host that reaches the sandbox through the
    workspace handle);
  - `middleware/`;
  - `skills/` (text only);
  - `sub_agents/`;
  - `LongTermMEMORY.md`.
- **Cost:** turns, context, and model calls.
- **Evidence in traces:** what would confirm it. For example: every document
  read before drafting; the output listing matches the requested files;
  fewer `context_overflow` endings.
- **Risk:** what could regress.

Harness limits to respect in every recommendation:

- Skill folders are not visible inside the sandbox, so a skill cannot ship a
  script for `bash`.
- Harness Python may not import network, subprocess, or dynamic-import
  modules.
- Nothing can be installed at run time.

# Leakage guard

Never search for:

- the benchmark by name;
- its tasks, rubrics, reference answers, leaderboards, or evaluation code;
- specific matter facts seen in evidence.

Record negative findings.

# Quality Criteria (in addition to the core ones)

1. Every library claim cites official documentation, with the version.
2. Every recommendation can be built within the harness limits above.
3. Uncertain claims are marked `UNVERIFIED:`.
4. The skill contains no benchmark names, task content, or rubric content.
