# Skills

Create one directory containing `SKILL.md` per NexAU skill and list it under
`skills` in `agent.yaml`. The agent sees each skill's frontmatter description
and loads the full text with NexAU's `LoadSkill` tool.

- A skill is instructions only. NexAU copies skill folders into its own local
  work folder on the host, not into the trial's podman sandbox, so files in a
  skill folder are not reachable from `bash`.
- To give the agent runnable scripts, put them under the harness's
  `workspace_seed/` folder. The adapter copies that folder's contents into
  every trial's `/workspace` before the agent starts, so
  `workspace_seed/skills/docx/scripts/x.py` becomes
  `/workspace/skills/docx/scripts/x.py`. Seed files run only inside the
  sandbox; host-side harness code may not import or bind them.
- Never place task identifiers, task-specific content, or grading rubric
  details in a skill or a seed file.
