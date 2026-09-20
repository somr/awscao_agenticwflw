# CAO agent profiles

These Markdown files are the repository-owned CAO profiles used by the three workflows. Install the profiles before starting a live workflow; the workflow installers do not install the planning or delivery profiles automatically.

## Profile catalog

| Workflow | Profiles | Write boundary |
|---|---|---|
| Planning | `sdlc_context_normalizer`, `sdlc_planning_analyst`, `sdlc_plan_author`, `sdlc_plan_reviewer` | The instructed answer file under `.agentic-sdlc/runtime/` |
| Delivery | `sdlc_code_supervisor`, `sdlc_implementer`, `sdlc_pr_reviewer`, `sdlc_remediator` | Implementer/remediator: `app/**` plus their runtime answer file; supervisor/PR reviewer: runtime answer file only |
| Source review | `sdlc_source_mapper`, `sdlc_source_correctness`, `sdlc_source_security`, `sdlc_source_validator`, `sdlc_source_feedback` | The isolated source-review workspace and its runtime answer file |

All profiles use the `claude_code` provider. The delivery supervisor proposes assignments to registered workers and selects required skills. Python validates and executes those assignments and owns stage ordering, retries and artifact persistence. Agents do not launch other agents directly. See [hybrid delivery and extension guide](../workflows/hybrid-delivery.md).

## Install planning profiles

From the repository root:

```bash
bash install-profiles.sh
```

The script validates and installs all four planning profiles, including the context normalizer required by `sdlc_dev_plan`.

## Install delivery profiles

Validate and install the delivery profiles explicitly:

```bash
for profile in code-supervisor implementer pr-reviewer remediator; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
```

Install the delivery workflow separately:

```bash
bash .agentic-sdlc/cao/workflows/install_deliver.sh "$PWD"
```

## Source-review installation

Workflow 3 installs its five profiles as part of its dedicated installer and refuses to overwrite an existing installation:

```bash
bash .agentic-sdlc/cao/workflows/install_source_review.sh "$PWD"
```

See the [source-review guide](../workflows/source-review.md) for its prerequisites, isolation model, inputs and upgrade procedure.

## Write scope and validation warnings

The profiles grant `fs_write` so agents can save their final answers. That grant is constrained by the repository's trusted `PreToolUse` hook in `.claude/settings.json`; CAO's `--dangerously-skip-permissions` means Claude permission rules alone cannot provide this path restriction. Read [answer-file delivery and the write-scope hook](../workflows/planning.md#answer-file-delivery--the-write-scope-hook) before changing profiles, the hook or runtime answer paths.

CAO's validator may warn that `fs_write` is outside its recognized vocabulary. The installed Claude Code mapping supports it, and the warning does not affect validation or installation in the tested CAO version. Do not silence the warning by granting broader tools.

To verify installed names:

```bash
cao profile list
cao profile show sdlc_context_normalizer
cao profile show sdlc_planning_analyst
cao profile show sdlc_plan_author
cao profile show sdlc_plan_reviewer
```

Do not launch these profiles with `--yolo`; that overrides their tool restrictions. They intentionally omit `@cao-mcp-server` because they are workers, not orchestrators.
