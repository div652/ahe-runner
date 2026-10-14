# Tool descriptions

Model-facing NexAU tool YAML files, registered under `tools` in `agent.yaml`.

- `name` must equal the registration name, and `description` must be
  substantive.
- `input_schema` must be a JSON Schema object with
  `additionalProperties: false`.
- Its properties must equal the bound function's parameters, excluding the
  injected `agent_state`. The required properties must equal the parameters
  without defaults.
- `binding` has the form `tools.<module>:<function>`. Tools are not lazy.
- `disable_parallel: true` makes the tools run one call at a time, as in LAB's
  native loop.
