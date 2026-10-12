# Runtime-Backed Example Contract

`RuntimeExampleAdapter` evaluates two text-interactive behaviors through the
runner-owned NexAU agent session:

1. return one exact direct response;
2. call `lookup_code(label)` and ground the final response in the protected
   result.

The public protected-action schema is:

```json
{
  "name": "lookup_code",
  "arguments": {
    "label": "string"
  }
}
```

The runner injects this schema into the in-memory evaluated-agent
configuration. The adapter executes the action and resumes the same session
with the exact tool-call ID. The implementation, held-out code values and
labels, held-out task prompts, and scoring fixtures remain outside meta-agent
source roots.

The native metric is mean exact task-trial success. Training evidence includes
the prompt, model activations, action arguments/results, final text, and
outcome. Validation and test traces remain controller-only.
