# Skills

Create one directory containing `SKILL.md` per NexAU skill and list it under
`skills` in `agent.yaml`. The agent sees each skill's frontmatter description
and loads the full text with NexAU's `LoadSkill` tool.

- A skill is instructions only. NexAU copies skill folders into its own local
  work folder on the host, not into the trial's podman sandbox. Helper scripts
  inside a skill folder are therefore not reachable from `bash`. To give the
  agent a script, put its source in the `SKILL.md` text and have the agent write
  it into `$WORKSPACE_DIR` before running it.
- Never place task identifiers, task-specific content, or grading rubric
  details in a skill.
