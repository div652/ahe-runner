# Core Implementation

This change implements the reusable substrate described in
`docs/ARCHITECTURE.md` before adding meta-agents or campaign orchestration.

## Included

- strict YAML configuration with exact unprefixed model IDs;
- project-local typed benchmark adapter loading;
- typed native metric, split, evidence, validation, transition, and prompt
  context contracts;
- generic NexAU harness-manifest validation;
- protected agent-field checks;
- path, symlink, size, Python compilation, task-ID memorization, and NexAU
  config-load checks;
- immutable hash-verified campaign stages with preserved attempts;
- explicit resumable infrastructure failures versus blocked logic failures;
- Git-backed Evolve workspaces;
- continuation from the last successful Evolve commit in a new attempt
  workspace;
- archival of uncommitted work rather than destructive in-place reset;
- immutable C0 and evolved checkpoint snapshots;
- stable evaluation and unit identities;
- contiguous absolute evaluation-attempt numbering;
- hash-verified completion markers;
- rejection of changed inputs and changed completed outputs.

## Recovery semantics

The runner never mutates a failed Evolve attempt to make it look successful.
A continuation clones the previous workspace's committed `HEAD` into a new
attempt directory. The previous dirty diff, staged diff, status, and untracked
files remain archived. This makes the "last successful checkpoint" concrete:
it is a Git commit, not a partially written state flag.

Evaluation-unit recovery follows the same rule. Existing attempts remain
immutable, the next attempt number is absolute, and a valid completed unit is
never retried regardless of score.

## Deliberate boundary

This commit does not yet contain the campaign controller, default NexAU
meta-agent backend, operator templates, or tests. Those are separate logical
changes so their behavior and validation remain attributable.
