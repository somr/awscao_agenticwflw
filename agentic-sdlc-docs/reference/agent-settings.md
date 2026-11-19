# Provider and model per agent profile

A project can choose, per agent profile, which provider and which model its agents run on: for example a stronger
model for the PR reviewer, or a cheaper one for the Source review mapper. Without settings, every agent runs as
before: on the `claude_code` provider with the model Claude Code picks by default.

## At a glance

| | |
|---|---|
| Where | `agent_settings` in the project's `agentic-sdlc-project.json` |
| What can be set | `provider` and `model`, as a `default` for every profile and per profile |
| Providers allowed | `claude_code` only, until another provider has its own verified write enforcement |
| Effort | Not configurable here; the machine-wide setting applies |
| Who uses it | Every workflow, for every agent call |
| Trust | Read from the checkout the workflow runs from, never from a reviewed change; no agent may write the file |
| Changing a setting | Edit the file; the next run uses it. No reinstall of profiles or workflows |
| Records | Each step's evidence and each run's manifest or report say what was used |

## Writing the settings

```json
{
  "version": 1,
  "agent_settings": {
    "default": {"model": "sonnet"},
    "profiles": {
      "sdlc_pr_reviewer": {"model": "opus"},
      "sdlc_source_mapper": {"model": "haiku"}
    }
  }
}
```

| Key | Required | Rules |
|---|---|---|
| `default` | no | An object with optional `provider` and `model`, applied to every profile. |
| `profiles` | no | Profile name to an object with optional `provider` and `model`. The name must be one of the installed profiles of this project (`sdlc_…`). |
| `provider` | no | Only `claude_code`. Any other value stops the run before the first agent. |
| `model` | no | A model alias or id as the provider's CLI accepts it, such as `haiku`, `sonnet`, `opus` or `claude-opus-5-5[1m]`: letters, digits and `.` `_` `:` `/` `[` `]` `-`, at most 100 characters, starting with a letter or digit. |

A profile entry overrides the default field by field. A field set nowhere means "as before": the default provider,
and no model passed, so the provider uses its own default. Any other key, including `effort`, is an error. An invalid
setting stops the run before any agent starts, in every workflow.

The workflow does not check that your account can use a model. A model the CLI rejects fails at that agent's first
turn.

## How the workflows use it

Each workflow reads the settings once, when it starts, from the same trusted checkout as its other configuration
(Source review and the follow-up from `repository_root`, never from the PR). Every agent call of the run, including
JSON repair turns, retries and parallel calls, then uses the settings of its profile. The model is passed to CAO for
that one call, so nothing is installed per project; installed profiles stay shared by every project on the machine.

`sdlc_implementer` is used by Delivery's single-mode implementer, the hybrid workers, integration and verification
repair, so a setting for it applies to all of them.

## Records

- Each step writes `<step_id>.agent-settings.json` beside its evidence: the profile, provider and model, and where
  each came from (`profile`, `default` or `unset`).
- Planning's `execution-manifest.json`, Delivery's `delivery-manifest.json`, Source review's `code-review.json`, the
  follow-up's `follow-up.json` and the source remediation manifest hold `agent_settings`: the default provider, the
  profiles that have a provider or model set, and the SHA-256 of the project file.
- Delivery's human review brief lists those profiles under "Review target".
- A resumed Delivery uses the current settings, so a human can choose another model after a failure. When they differ
  from the run it continues, the manifest records `agent_settings_changed` and `previous_agent_settings`, and the
  review prompt, PR body and brief say so.

## Other providers

The field `provider` exists so the format does not change when another provider is allowed. Allowing one needs more
than a setting: the write-scope hook is a Claude Code hook, and other CAO adapters start with their sandbox or
approvals off. See [Change one profile from Claude to another provider](agent-profiles.md#change-one-profile-from-claude-to-codex-gemini-or-another-provider)
for what has to be proved first.
