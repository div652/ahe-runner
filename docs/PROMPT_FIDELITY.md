# AHE Prompt and Mechanism Fidelity

The reusable prompts are benchmark-generic, but they retain the operating
mechanisms that make the original AHE agents effective. This matrix prevents a
future refactor from replacing full contracts with short role summaries.

| Dimension | Source Explore | Web Explore | Debugger | Evolve |
|---|---|---|---|---|
| Full versioned system prompt | Yes | Yes | Yes | Yes |
| Explicit evidence boundary | Source/seed only | Public, leakage-guarded | Training task only | Training evidence only |
| Mandatory investigation procedure | Framework call paths | Multi-source research | Earliest causal failure | History-to-change workflow |
| Concrete deliverable schema | `SKILL.md` | `SKILL.md` | Strict JSON | Git state + change manifest |
| Deliverable validation | Frontmatter/content gate | Frontmatter/content gate | Field/type/range checks | Candidate validator + clean Git |
| Durable partial progress | Valid skill reused | Valid skill reused | Per-task result reused | Committed `HEAD` reused |
| Context compaction | Yes | Yes | Direct bounded task payload | Yes |
| Model identity fail-closed | Every call | Every call | Every call | Every call |
| Explicit stop mechanism | `complete_task` | `complete_task` | Valid JSON result | `complete_task` |

## Explore handoff

| Aspect | Original AHE mechanism | Reusable runner |
|---|---|---|
| Source output | Framework-internals `SKILL.md` | `source-exploration/SKILL.md` |
| Web output | Agent-research `SKILL.md` | `web-research/SKILL.md` |
| Registration | Evolve `skills` list | Evolve `skills` list |
| Access | `LoadSkill` | `LoadSkill` |
| Disclosure | Description first, content on demand | Description first, content on demand |
| Mutability | Generated before Evolve | Copied read-only before Evolve |
| Lifecycle | Run once by controller | Run once by controller |

The runner intentionally does not register live Source/Web agents as the
primary Evolve handoff. Their durable skills are the authoritative mechanism.
This is closer to the original AHE flow and avoids silently replacing
progressive disclosure with ad hoc `read_file` access.

## Preserved Evolve semantics

The Evolve prompt retains:

- complete-history review before local edits;
- aggregate plus task-level causal synthesis;
- all Debugger outputs, not cherry-picked examples;
- prior-change attribution;
- seven explicit evolvable component classes;
- protected benchmark/controller/model boundaries;
- benchmark-memorization prohibition;
- coherent intervention design;
- regression-risk and falsifier reasoning;
- framework verification before configuration changes;
- Git commits as the executable deliverable;
- a committed change manifest;
- candidate validation after the final commit;
- context-management and recovery instructions;
- completion only after clean, validated state.

## Generic adaptations

Some wording must differ because the runner supports arbitrary typed benchmark
adapters:

- benchmark-specific metrics and domain terminology come from
  `meta_prompt_context()`;
- native evaluation schemas are normalized into the standard training evidence
  bundle;
- held-out split details remain adapter-owned;
- source roots use the installed framework and selected adapter rather than
  hardcoded repository names;
- the Debugger's failure taxonomy includes adapter/environment causes but
  cannot edit those protected surfaces.

These are parameterizations of the method, not reductions of the prompt.
