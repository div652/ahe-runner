# Evolution Algorithms

The runner separates **what is evolved and how it is measured** from **how
the search proceeds**. You bring a NexAU harness and a benchmark adapter; you
choose an evolution algorithm; the runner runs the campaign.

| Built-in | `algorithm.name` | Summary | Guide |
|---|---|---|---|
| AHE | `ahe` (default) | Explore once; per round, a per-task Debugger and one global Evolve update; every round's checkpoint is the next parent | [ARCHITECTURE.md](ARCHITECTURE.md) |
| RRSI | `rrsi` | Regularized Recursive Self-Improvement: analyst and digesters, several proposer variants per round with an annealed edit budget, a leakage critic, and noise-adjusted, cost-aware selection | [RRSI.md](RRSI.md) |

A campaign that does not mention `algorithm` runs AHE exactly as before.

## Who owns what

The **runner (kernel)** owns everything that does not depend on the search
strategy:

- the frozen baseline checkpoint C0 and its campaign identity;
- evaluation through the adapter, its verification, and its stage records;
- training evidence bundles;
- candidate workspaces, generic and adapter validation, and immutable
  checkpoints, recorded with their parent (a lineage graph);
- priced cost per trial of the evaluated agent;
- meta-agent execution: model routing, call recording, path-guarded tools,
  long-output handling, context compaction;
- durable stages and resume;
- the closing lifecycle: selection and the final test of C0 against the
  shipped checkpoint, and the export of that harness.

The **algorithm** owns the search:

- which checkpoints to build, from which parents;
- which roles to run, with which prompts and tools (its packaged resources);
- which candidates to keep;
- which checkpoint it would ship, and which checkpoints are worth validating.

An algorithm never sees the validation or test split. The kernel exposes the
training split only.

```text
ahe run
 ├─ prepare: freeze C0, campaign identity                    (runner)
 ├─ evolve: algorithm.evolve(kernel) -> outcome              (algorithm)
 │    builds checkpoints C1, C2, ... through kernel services
 ├─ evolution.close: records the outcome                     (runner)
 ├─ selection: policy "validation" or "algorithm"            (runner)
 └─ final test: C0 and the selected checkpoint on test       (runner)
```

## Configuration

```yaml
algorithm:
  name: rrsi                 # ahe (default), rrsi, or module.path:Class
  settings: {}               # validated by the algorithm's settings model
  role_supplements:          # optional, per role the algorithm declares
    proposer: ./rrsi_supplements/proposer.md

selection:
  policy: null               # null: the algorithm's default
                             # validation: evaluate the nominees (and C0) on
                             #   the validation split; adapter.selection_key
                             #   picks, earliest checkpoint on ties
                             # algorithm: ship the algorithm's pick; no
                             #   validation evaluation

models:
  evaluated:
    pricing:                 # optional; needed by algorithms that use cost
      currency: USD
      input_per_million: 1.25
      cached_input_per_million: 0.125
      cache_write_per_million: 1.25
      output_per_million: 10.0
      long_context:          # optional tier for large prompts
        above_prompt_tokens: 200000
        input_per_million: 2.5
        output_per_million: 15.0
```

The algorithm's name, version, settings, code and resource hashes, and role
supplements enter the campaign identity when the algorithm is not the
default AHE. Changing any of them requires a new campaign directory.

`ahe validate` and `ahe conformance` report the algorithm, its resolved
settings, the selection policy, and any problems the algorithm finds with the
campaign (for example, RRSI without pricing).

## The selection policies

Every algorithm returns an outcome:

- `pick`: the checkpoint it would ship;
- `nominees`: the checkpoints worth validating (the runner always adds C0).

| Policy | Validation evaluations | Selected checkpoint |
|---|---|---|
| `validation` | C0 and every nominee | highest `adapter.selection_key`, earliest on ties |
| `algorithm` | none | the algorithm's pick |

AHE nominates C0..CN and defaults to `validation`, which is the original AHE
lifecycle. RRSI nominates the incumbents along its accepted path and
defaults to `algorithm`, as in the paper, which ships the final incumbent.

Either way, the final test evaluates C0 and the selected checkpoint on the
test split and reports the paired task-bootstrap interval.

## Cost

When `models.evaluated.pricing` is set, the runner prices the evaluated
agent's recorded token usage per completed trial (`cost.py`):

- four disjoint categories: uncached input, cache reads, cache writes, and
  output (reasoning tokens are billed as output);
- a call whose prompt exceeds `long_context.above_prompt_tokens` uses that
  tier;
- only calls of sessions marked as evaluated-agent sessions count, so judge
  and helper calls an adapter records are excluded;
- only the attempt that completed each unit counts, so an attempt abandoned
  on infrastructure is excluded. This needs the adapter to pass `unit_id`
  and `attempt` in `AgentSessionRequest` (see ADAPTER_GUIDE.md).

Relative cost changes depend only on price ratios. Absolute prices make
reports readable. Provider prices differ widely: output costs 5 to 8 times
input on current frontier models, and cache reads about a tenth of input.

## Writing an algorithm

An algorithm is a subclass of `ahe_runner.algorithms.EvolutionAlgorithm`:

```python
from pydantic import BaseModel

from ahe_runner.algorithms import (
    AlgorithmIdentity,
    EvolutionAlgorithm,
    EvolutionKernel,
    EvolutionOutcome,
)


class MySettings(BaseModel):
    candidates: int = 3


class MyAlgorithm(EvolutionAlgorithm):
    settings_model = MySettings
    default_selection_policy = "validation"
    roles = ("proposer",)  # names accepted in algorithm.role_supplements

    def identity(self) -> AlgorithmIdentity:
        return AlgorithmIdentity(name="mine", version="1", description="...")

    def resources(self):
        return (MY_PROMPTS_DIR,)  # hashed into the campaign identity

    def check(self, kernel: EvolutionKernel) -> list[str]:
        return []  # problems that make the campaign unrunnable

    def evolve(self, kernel: EvolutionKernel) -> EvolutionOutcome:
        ...
```

Reference it as `algorithm.name: my_package.module:MyAlgorithm`; the module
is imported from the project root, like the adapter.

### Kernel services

| Service | What it does |
|---|---|
| `kernel.rounds`, `train_split`, `train_trials` | campaign facts; only the training split |
| `kernel.evaluate_train(label, repeat=1)` | durable training evaluation; `repeat` > 1 is an independent re-evaluation with other seeds |
| `kernel.train_evaluation_dir(label)` | the evaluation's folder (units, runtime traces) |
| `kernel.evidence(label)` | the verified training-evidence bundle |
| `kernel.cost(label)` | priced cost per trial (`mean_cost_per_trial`, token means) |
| `kernel.allocate_checkpoint(key)` | a stable label (C1, C2, ...) for a candidate key |
| `kernel.candidate_workspace(label=, parent=, attempt=, recovery_dir=)` | a Git workspace seeded from any checkpoint |
| `kernel.validate_candidate(label=, parent=, workspace=)` | generic and adapter validation; reports instead of raising |
| `kernel.freeze_candidate(...)` | the immutable checkpoint, with its parent |
| `kernel.component_changes(parent, label)` | changed files with their NexAU component class |
| `kernel.run_role(RoleSpec(...))` | runs one meta-agent role (prompt, query, roots, tools, stop tool) |
| `kernel.stage(name, semantic_inputs=, action=)` | any other durable step, named `<algorithm>.<name>` |
| `kernel.algorithm_dir` | a folder for the algorithm's own artifacts |

### Determinism and resume

`evolve` runs again from the start on every `ahe run` or `ahe resume`.
Completed stages return their recorded data, so a correct algorithm:

- decides only from stage data and kernel results, never from the clock,
  unseeded randomness, or mutable files it did not record;
- keeps stage outputs immutable after completion (the runner re-hashes them
  on every replay);
- records Git commit hashes rather than declaring a mutable workspace as a
  stage output.

### Roles

A `RoleSpec` names a packaged prompt, the query, the roots it may read and
write, runner tools by name (`read_file`, `write_file`, `replace`,
`run_shell_command`, `validate_candidate`, ...), the algorithm's own tools
(`RoleTool`: a NexAU `*.tool.yaml` and a `module:function` binding that
receives `agent_state`), and the stop tool. A stop tool that returns
`{"status": "error", ...}` lets the agent fix its submission and call again.
The operator's supplement for the role is appended to the prompt as literal
text.

Tests run roles with `meta.backend: scripted`; a `RoleSpec.scripted`
callable supplies the deterministic stand-in.
