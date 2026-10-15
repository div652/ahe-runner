# Campaign 9b: agent trajectory walkthroughs

This report walks through six campaign 9b runs one call at a time. It focuses
on the tool calls: what the agent called, why, what came back, and what went
wrong.

- **Campaign:** `harvey-lab-campaign-9b-lab-exact-token-fix`. It used the
  LAB-exact NexAU harness plus the AHE-023 token fix.
- **Agent:** gpt-5.6-sol with high reasoning effort.
- **Judges:** judge 1 is claude-sonnet-5 and judge 2 is gpt-5.5.
- **Tools offered:** glob, grep, read, bash, write and finish, plus the docx,
  pptx and xlsx skills.

## How to read this

- **"Line"** means a line number in that run's `trace.jsonl`. A call on line
  N gets its result on line N+1. A parallel batch of k calls gets results on
  N+1 to N+k.
- In a parallel batch, results come back in **finish order, not call order**.
- **"Output chars"** means the characters of tool output fed back to the
  model.
- Files for each run are in
  `harvey-ahe-project/campaign-artifacts/harvey-lab-campaign-9b-lab-exact-token-fix/evaluations/C0-train/units/<unit>/attempt-1/`:
  - `trace.jsonl`;
  - `result.json`;
  - `grading/grading-1/scores_judge-{1,2}.json`;
  - `run/output/response.md`.

  `campaign-artifacts/` is git-ignored, so these files exist only on the
  machine that ran the campaign.
- Section 5o of `harvey-ahe-project/HARVEY_AHE_RUNBOOK.md` has campaign 9b's
  setup and overall results.

## How the runs were picked

- The pool was all 250 units, using the latest attempt of each.
- Picks used random seed `20261015`. Task 187 was excluded because it had
  already been walked through.
- The first four picks contained no all-pass run. The fifth pick was
  therefore swapped for a random all-pass unit; 51 of the 250 units are
  all-pass.
- All five picks turned out to be attempt-1.
- Task 187, the earlier walkthrough, is included as run 0 for reference.

## Summary

| Run | Task | Unit | Question (short) | Turns | Calls | Wall | Output chars | Judge 1 | Judge 2 | All-pass |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 187 | `96695a99…` | Indemnification-cap trend, software/SaaS deals 2022–24 | 19 | 35 | 382 s | 2.69M | 5/6 | 6/6 | No |
| 1 | 196 | `00201383…` | Pull the executed SPA for Cascade Retail | 10 | 21 | 180 s | 0.49M | 2/2 | 2/2 | **Yes** |
| 2 | 030 | `3499d4e3…` | Restructurings that involved DIP financing | 13 | 41 | 705 s | 1.44M | 5/6 | 5/6 | No |
| 3 | 007 | `ecd0c9f7…` | Antitrust matters that made an HSR filing | 50 | 50 | 1,115 s | 0.29M | 1/3 | 1/3 | No |
| 4 | 115 | `313d0f24…` | General-litigation cases with a forensic-accounting expert | 37 | 64 | 889 s | 2.28M | 5/6 | 5/6 | No |
| 5 | 140 | `6c9ca379…` | Life-sciences M&A deals that used R&W insurance | 19 | 36 | 308 s | 0.83M | 10/11 | 9/11 | No |

Full unit IDs:

- run 0: `96695a999a1959f7834a196b21933b79`;
- run 1: `00201383378751ab959a596cb4c5a043`;
- run 2: `3499d4e33e125f779446a90ef6de2a10`;
- run 3: `ecd0c9f7189b59bfbfb8026a3f79e62d`;
- run 4: `313d0f242d1159f2aa7439ff5b45b80d`;
- run 5: `6c9ca379a72e558cbeb741f93183d1c8`.

### Tool mix

| Run | glob | grep | read | bash | write | finish |
|---|---:|---:|---:|---:|---:|---:|
| 0 (187) | 7 | 2 | 15 | 9 | 1 | 1 |
| 1 (196) | 3 | 4 | 7 | 5 | 1 | 1 |
| 2 (030) | 14 | 1 | 18 | 6 | 1 | 1 |
| 3 (007) | 2 | 0 | **0** | **46** | 1 | 1 |
| 4 (115) | 1 | 11 | 27 | 23 | 1 | 1 |
| 5 (140) | 1 | 0 | 21 | 12 | 1 | 1 |

### Problems per run

| Run | Rejected bash arguments | 60 s timeouts | Broken heredocs | Results over 100k chars | Largest result |
|---|---|---:|---:|---:|---:|
| 0 (187) | 2: a stray key and `timeout` | 1 | 0 | 12 | 238k |
| 1 (196) | 0 | 0 | 0 | 1 | 233k |
| 2 (030) | 1: `timeout` | 2 | 0 | 4 | 243k |
| 3 (007) | 4: `timeout`, `max_output_chars` ×2, `yield_time_ms` | 2 | 0 | 0 | 52k |
| 4 (115) | 3: `timeout` ×3 | 1 | 4 | 3 | **860k** |
| 5 (140) | 3: `timeout`, `yield_time_ms`, `timeout_ms` | 1 | 0 | 0 | 94k |

---

## Run 0 — task 187: indemnification-cap trend

**Question:** "How did the caps trend across our software and SaaS acquisition
deals from 2022 to 2024?"

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths only, newest first, with no notice that the list was cut. Only matters 1044–1046 were visible. |
| 6 | 2 × grep (on `*.docx` and `*.xlsx`) + glob `**/*agreement*.docx` | Find software and indemnity documents; list agreements | Both greps found **no matches**, because grep can't see inside zipped docx/xlsx. The glob again stopped at 100. |
| 10 | glob `matters/{1041-*,1042-*,…}/**/*` | List four matter groups at once | No match, because glob doesn't support `{a,b}`. |
| 12 | 4 × glob, one per prefix 1041–1044 | Same goal, one group at a time | File lists, each under 100. |
| 17 | 5 × full `read` of merger agreements and SPAs | Find the caps | About 1.06M chars (187k–238k each). |
| 23 | 4 × `read`: a diligence memo, an LOI, an engagement letter, and **1041-00003's merger agreement again** | More detail | The repeated read cost another 229k chars. |
| 28 | bash: Python scan of every `.docx` (zipfile + XML) for software and cap terms | Work around grep's blind spot | **Timed out at 60 s.** Partial output: about 25k chars and 180 hits. |
| 30 | 5 × full `read` of APAs and merger agreements | Check the hits | About 872k chars. |
| 36 | bash with a broken argument key: the model's own note, `"} malformed? I mistakenly included quote…"`, became the key | — | Rejected with a TypeError. The traceback leaked the host path. |
| 38 | The same scan, fixed | — | 45k chars of hits. |
| 40 | bash with `timeout: 120000` | A wider scan | Rejected. |
| 42 | The same scan without `timeout` | — | Found "Indemnification Cap" in a 1008-00008 redline. |
| 44 | `read` the 1008-00008 executed merger agreement | Confirm the cap | 220k chars. |
| 46–52 | 4 × bash Python checks | Deal dates, values and caps; 2022 candidates | Small outputs. |
| 54 | write `response.md` (5.2k) | — | — |
| 56 | finish | — | — |

**Outcome:**

- The answer found the right three deals:
  - 1042-00004: 35% cap, 2023;
  - 1041-00003: 15% cap, 2024;
  - 1008-00008: 30% cap, 2024.
- It correctly said there was no qualifying 2022 deal and that the trend was
  downward.
- Judge 1 failed precision because the answer named the deals by the other
  party (Codelaunch, Cloudium, Prismyx) rather than the rubric's names
  (Brightfield, Solara, Lumos). Judge 2 passed it.

**Notes:** 95% of the output chars came from full-document reads, including
one repeated 229k read.

---

## Run 1 — task 196: executed SPA for Cascade Retail (all-pass)

**Question:** "Pull the executed SPA from Cascade Retail Holdings Inc.'s
acquisition" for the closing binder.

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths (capped). |
| 6 | grep `Cascade`, no file filter | Find the client | Hits only in `.eml` files, which are plain text. They pointed to 1038-00009 and 1041-00003. |
| 8 | 3 × `read` of emails + glob of 1038-00009 and 1041-00003 | Identify the matter and its files | 1038-00009 is the Cascade / NovaMart stock purchase. |
| 14 | 4 × `read`: SPA execution version (233k), closing checklist xlsx (91k), signing summary letter, engagement letter | Confirm which copy is the executed one | Execution version confirmed. |
| 19 | bash: `unzip -l` (fails because unzip isn't installed), then a Python list of the zip parts; a search for "fully executed" | Inspect the file itself | Lists media, customXml and docProps. |
| 23 | bash: sha256, plus a count of `<w:ins` / `<w:del` | Check for tracked changes | 1,418. This was a **false positive**: `<w:ins` also matches longer tag names. |
| 25 | bash: proper check with lxml | Re-check | No tracked changes. |
| 27 | Searches for amendments and closing items, plus a zip namelist | Look for later amendments | None. |
| 31 | write `response.md` | — | — |
| 33 | finish | — | — |

**Outcome:**

- The answer named
  `1038-00009/Transaction Documents/stock-purchase-agreement-execution.docx`
  and gave its SHA-256.
- It noted that the signature pages are blank and that a conformed set is
  "to follow".
- Both judges gave 2/2.

**Why it worked:**

- It was a single-document lookup.
- The client name appears in plain-text emails, so grep worked.
- It needed only 21 calls and 180 s.

---

## Run 2 — task 030: DIP financing experience

**Question:** "How many of our past restructurings involved DIP financing in
any capacity (for the debtor, the DIP lender, or as a creditor responding to a
DIP in the case), and which were they?"

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths (capped). |
| 6 | bash `find -maxdepth 1` | List the matter folders | The full list. This gets past the glob cap. |
| 8 | glob `*dip*`, `*DIP*`, `*restruct*`, `*chapter*` + grep `debtor-in-possession` | Find DIP files | `*dip*` hit. `*DIP*` found nothing, because glob is case-sensitive and filenames are lowercase. grep hit only 3 `.eml` files. |
| 14 | 5 × glob: 1044-00003, 1024-00001, 1020-00002, 1011-00002, 1006-00002 | List the candidate matters | File lists. |
| 20 | **15 × full `read` in parallel** | Read the DIP documents | About 1.0M chars in one batch. |
| 36 | bash: full-corpus text scan | Catch missed matters | Timed out, with no output. |
| 38 | bash with `timeout: 300` | Retry with more time | Rejected. |
| 40 | bash: multiprocessing-pool scan | Go faster | Timed out again. |
| 42 | bash: filename-only scan for bankruptcy terms | Cheaper fallback | Fast, and it worked. |
| 44 | bash: count engagement and opening files | Size check | 388. |
| 46 | 4 × glob + 3 × `read` | Check the remaining candidates | — |
| 54 | write `response.md` | — | — |
| 56 | finish | — | — |

**Outcome:**

- The answer counted four DIP matters: 1006-00002, 1020-00002, 1024-00001 and
  1011-00002.
- It excluded 1044-00003 (Thalassa Shipping) as "contingency planning" where
  no DIP was actually done.
- The rubric counts Thalassa, so both judges failed that criterion. Both gave
  5/6, and precision passed.

---

## Run 3 — task 007: HSR filings

**Question:** "How many of our Antitrust & Competition matters have actually
involved making an HSR filing, and which ones were they?"

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths (capped). |
| 6 | bash: list matter folders; count files by type | Size the corpus | 8,055 docx, 615 eml, 573 xlsx, 45 pptx. |
| 8 | bash with `timeout: 120000` | — | Rejected. |
| 10 | bash: filename scan for hsr / antitrust | Candidates | A candidate list. |
| 12 | glob `*.json` | Look for metadata | None. |
| 14 | bash: list files in 1001-00001, 1003-00001, 1001-00004, 1032-00005, 1038-00009 | Inspect candidates | 1003-00001 has an "HSR Filing" folder with a transmittal letter and a withdrawal letter. |
| 18 | bash: write `/tmp/searchdocs.py`, a text search writing to `/tmp/ac.txt` | Search inside docx | — |
| 20 | bash with `max_output_chars` | — | Rejected. This happened again at line 54. |
| 22 | `cat` the result | — | — |
| 24 | bash: `/tmp/index.py` builds a full-corpus text index, `/tmp/corpus.jsonl` | One index for many queries | Timed out at 60 s with 95 MB written. |
| 28 | bash with `yield_time_ms` | — | Rejected. |
| 30 | bash: `nohup python /tmp/index.py &` | **Run it in the background to dodge the 60 s cap** | Started. |
| 32–50 | **10 × `sleep 30–35`** + check | Wait for the index | About 5 minutes. Done at 518 MB. |
| 52–98 | About 24 Python queries over the index | Find "Antitrust & Competition" matters and evidence that a filing was made | Line 64 timed out. 1003-00001 scored "STRONG" (lines 53, 63, 69). Line 73 showed it as "Closed — Terminated, FTC Transaction No. 2418-7603". |
| 100 | write `response.md` | — | — |
| 102 | finish | — | — |

**Outcome:**

- The answer listed 10 matters. The rubric set is 1001-00001, 1003-00001,
  1032-00001, 1038-00001 and 1041-00001.
- It **missed 1003-00001**. The index clearly showed an HSR filing (an FTC
  transaction number), but the matter was "Closed — Terminated". The answer
  never mentions it and lists 1003-00003, a different Harrowgate deal,
  instead.
- The answer does explain why it left out 1003-00004, but not 1003-00001.
- 7 of the listed matters were outside the set, so precision failed. It read
  "Antitrust & Competition matters" as any deal that filed HSR.
- Both judges gave 1/3.

**Notes:**

- Smart workaround: a background index with polling.
- It made **zero `read` calls**. Everything went through bash.

---

## Run 4 — task 115: forensic-accounting experts

**Question:** "Across our general litigation docket, list every case where
we've retained a forensic-accounting expert and who it was."

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths (capped). |
| 6 | grep (files) `forensic\|accounting expert\|retained…` | Find candidates | 14k chars, `.eml` files only. |
| 8 | grep (content) `forensic.{0,40}account` | Same | 2.8k chars of `.eml` hits. |
| 10 | bash `find` by filename (expert, forensic, account, damages, valuation) | Filename candidates | A 15.5k list. |
| 12 | bash: Python extracts all docx + eml text into `scratch_corpus.txt` | Build a searchable corpus | **Timed out.** |
| 14 | bash with `timeout` | Check the file | Rejected. |
| 16 | The same without `timeout` | — | 0 lines. The file was empty because the job was killed before writing. |
| 18 | 7 × full `read` of retention and engagement letters | Read the obvious candidates | About 247k chars. |
| 26 | bash: count files | — | 9,288 files, 8,055 of them docx. |
| 28 | bash: `ProcessPoolExecutor` with 16 workers scanning docx + eml | **Parallelise to beat 60 s** | Worked: 4,286 lines in `forensic_hits.txt`. |
| 30 | bash: list the hit files | — | 46k chars. |
| 32 | bash with `timeout: 1000`, which also writes `/tmp/listret.py` | — | Rejected, **so the script was never written**. |
| 34 | Run `/tmp/listret.py` | — | "No such file". The model assumed the rejected call had run. |
| 36 | Rewrite the script in the working directory and run it | — | 5.9k chars of file headers. |
| 38 | awk over the hits | — | 77k chars. |
| 40 | bash heredoc ending with the line `PY > explicit.txt` | Save results to a file | **Broken heredoc:** bash needs `PY` alone on its line, so Python received the shell lines too, giving a SyntaxError. |
| 42 | The same as a script file | — | 4.1k chars. |
| 44 | bash heredoc ending with `PY > expert_headers.txt` | Save headers to a file | Broken the same way, but this time `PY > expert_headers.txt` is valid Python, so the script ran. It printed **90k chars into the context** instead of the file, then raised a NameError. |
| 46 | 7 × full `read` of retention letters | Confirm experts | About 236k chars. |
| 54 | bash heredoc ending with `PY > direct_sentences.txt` | Save sentences to a file | Same bug: **860k chars into the context**, the largest single result in these six runs. |
| 56 | bash: list expert files in 13 candidate matters | — | 4.1k chars. |
| 58 | 7 × full `read` (retention letters, closing memos, case assessment) | Confirm | About 414k chars. |
| 66 | grep 1034-00002 for the "Hague Thornton" expert | Confirm one expert | 1 `.eml` hit. |
| 68 | grep `*.eml` for retained … forensic | — | None. |
| 70 | grep 1035-00005 for "Dennis Chua" | — | None, because grep can't see docx. |
| 72 | bash `cpa_scan.py` | Broader scan | Too broad: nearly every matter. |
| 74 | bash `strict_scan.py` + a heredoc ending `PY > strict_summary.txt` | Narrower scan | Broken heredoc again, giving a SyntaxError. |
| 76 | Rerun `strict_scan.py` | — | 308 files across about 40 matters. |
| 78 | bash with `timeout: 1000` | — | Rejected. |
| 80 | `python -c` one-liner | Summarise | 113k chars. |
| 82 | bash: search for 6 named experts in their matters | Confirm names | 116k chars. |
| 84 | bash: matters mentioning both "Litigation (General)" and "forensic account" | Filter by practice area | 20 matters. |
| 86 | 5 × grep, one per matter | Check retention wording | All "No matches", because docx are invisible to grep. |
| 92 | grep 1044-00006 | — | None. |
| 94 | 6 × `read` with `limit` 30–80 | Quick confirms | 3–7k chars each. This was the **first time it used `limit`**. |
| 101 | write `response.md` (8k) | — | — |
| 103 | finish | — | — |

**Outcome:**

- The answer listed 12 matters.
- It found all five rubric matters: 1006-00005, 1012-00006, 1021-00006,
  1023-00002 and 1024-00003. 1035-00004 was also allowed.
- It added six matters outside the set: 1005-00005, 1006-00004, 1019-00001,
  1032-00002, 1034-00002 and 1044-00001. Precision failed.
- Both judges gave 5/6.

**Notes:**

- The broken-heredoc habit happened 4 times. Twice it dumped huge output
  into the context.
- The 16-process scan shows the 60 s cap can be beaten with parallelism.

---

## Run 5 — task 140: life-sciences M&A with R&W insurance

**Question:** "Pull our M&A matters in [healthcare-services, biotech, or
pharma] where we used rep-and-warranty insurance so I can look at the policy
documentation."

| Line | Call | Purpose | What came back |
|---|---|---|---|
| 4 | glob `**/*` | Map the workspace | 100 paths (capped). |
| 6 | bash: `find` all files into `/tmp/files.txt`; list the matters | Full inventory | 9,288 files and the matter list. |
| 8 | bash with `timeout: 200000` | Filename scan for rwi / insurance | Rejected. |
| 10 | The same without `timeout` | — | An 8k list. |
| 12 | bash: list files in 11 candidate matters | — | 43k chars. |
| 14 | **8 × full `read`** of RWI memos and binders | Read the policy documents | About 613k chars in one batch. |
| 23 | bash with `yield_time_ms` | Full text scan | Rejected. |
| 25 | The same without it | — | Timed out. |
| 27 | bash with `timeout_ms: 300000` | — | Rejected. |
| 29 | bash: split the scan into **5 background shards** (`&` … `wait`) | Beat the 60 s cap | Crashed with an IndexError from a path-split bug. |
| 31 | `sed` fix, then rerun the shards | — | Worked: 50k chars of hit paths. |
| 33 | 6 × `read` with `limit: 50` | Check new candidates | Small outputs. |
| 40 | bash: list RWI files in 8 matters | — | — |
| 42 | 3 × `read` with `limit` | — | — |
| 46 | bash: R&W context in 1006-00008 and 1007-00006 | Confirm RWI use | 16k chars. |
| 48 | bash: count life-sciences terms per matter | Check each matter's sector | Term counts. |
| 50 | 4 × `read` with `limit` | Final confirms | — |
| 55 | write `response.md` (8k) | — | — |
| 57 | finish | — | — |

**Outcome:**

- The answer gave five qualifying matters.
- It put **1018-00008** (biotech) in a separate section: the RWI binder was
  bound, but the deal was terminated.
  - The rubric counts it.
  - Judge 1 passed it as "identified".
  - Judge 2 failed it because the answer said it doesn't qualify. The judges
    disagree here.
- Both judges failed "proof document — 1006-00008". The rubric wants
  `secretary-certificate-buyer.docx` or `equity-commitment-letter.docx`; the
  answer cited a closing-deliverables letter and the APA.
- Precision passed.
- Judge 1 gave 10/11 and judge 2 gave 9/11.

---

## Patterns across the six runs

1. **glob silently stops at 100 results.**
   - Every run started with `glob **/*` and got 100 paths, newest first, with
     no notice that the list was cut.
   - Runs 2–5 switched to bash `find` to get the full list. Run 0 globbed one
     prefix at a time.
2. **grep can't read docx/xlsx/pptx, which are 87% of files.**
   - grep hits came only from `.eml` files.
   - Runs 0 and 4 got "No matches" on matters that do contain the terms.
   - So 5 of 6 runs wrote their **own Python zip/XML text extractor** in
     bash. Only the all-pass run didn't need one, because its client name was
     in plain-text emails.
3. **The 60 s bash limit shaped every hard run.**
   - There were 7 timeouts across 5 runs.
   - Workarounds:
     - a background index with `nohup` plus sleep polling (run 3, about 5
       minutes of waiting);
     - a 16-process pool (run 4, worked);
     - 5 background shards (run 5, worked);
     - a filename-only scan (run 2).
4. **13 rejected bash arguments across 5 runs.**
   - The arguments were `timeout` ×7, `max_output_chars` ×2,
     `yield_time_ms` ×2, `timeout_ms` ×1, and one broken key.
   - These look like arguments from other agent tools the model knows; this
     is a guess.
   - Each rejection wastes a turn and leaks the host path in the traceback.
   - Side effects silently don't happen: in run 4 at line 34, a script that
     was never written was then run.
5. **Full-document reads dominate the context.**
   - `read` returns the whole file by default. Agreements are 150k–240k
     chars each.
   - There were parallel batches of up to 15 reads (about 1M chars at once).
   - Only runs 4 (late) and 5 used `limit`.
   - Total output chars per run ranged from 0.29M to 2.69M.
6. **Shell mistakes:**
   - broken heredocs (`PY > file` as the end line), 4 times in run 4,
     dumping up to 860k chars into the context;
   - the `<w:ins` false positive in run 1.
7. **No skill use.** None of the six runs touched the docx, pptx or xlsx
   skill scripts. They are for creating documents, and these tasks only read.
8. **Lost points are mostly judgment, not search.** The agents usually found
   the right documents.
   - **Too broad:** extra matters failed precision in run 3 (7 extra) and
     run 4 (6 extra).
   - **Too strict about deals that didn't happen.** In three runs, the agent
     excluded or dropped a matter because the deal was terminated or only
     contingent, but the rubric counts it:
     - run 2: Thalassa, a contingency DIP;
     - run 3: 1003-00001, terminated after the HSR filing;
     - run 5: 1018-00008, terminated after the RWI was bound.
   - **Names:** run 0 used the counterparty names instead of the rubric's
     names (judge 1 only).
9. **The judges disagreed in 2 of 6 runs:** run 0 (names) and run 5
   (1018-00008).
