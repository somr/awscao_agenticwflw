# CAO agent profiles

These Markdown files are the repository-owned CAO profiles used by the three workflows. Install the profiles before starting a live workflow; the workflow installers do not install the planning or delivery profiles automatically.

Checked on **2026-09-21** against the current repository working tree and the locally installed **CAO 2.5.0** implementation. Provider identifiers and launch behavior are version-dependent; verify the installed server as well as the CLI when upgrading.

## Profile catalog

| Workflow | Profile (source file) | Responsibility and output |
|---|---|---|
| Planning | [`sdlc_context_normalizer`](../../.agentic-sdlc/cao/profiles/context-normalizer.md) | Normalize retrieved requirements while preserving provenance; return structured Planning Context. |
| Planning | [`sdlc_planning_analyst`](../../.agentic-sdlc/cao/profiles/planning-analyst.md) | Inspect repository impact, dependencies, ambiguities and evidence; write repository analysis. |
| Planning | [`sdlc_plan_author`](../../.agentic-sdlc/cao/profiles/plan-author.md) | Author/revise the Development Plan using context, analysis and developer guidance. |
| Planning | [`sdlc_plan_reviewer`](../../.agentic-sdlc/cao/profiles/plan-reviewer.md) | Independently review the plan, including previous findings; return structured findings. |
| Delivery | [`sdlc_code_supervisor`](../../.agentic-sdlc/cao/profiles/code-supervisor.md) | Propose ordered assignments to registered workers and select skills; return a task graph. |
| Delivery | [`sdlc_implementer`](../../.agentic-sdlc/cao/profiles/implementer.md) | Implement approved tasks and required tests; also perform hybrid integration and verification repair; return a completion summary. |
| Delivery | [`sdlc_pr_reviewer`](../../.agentic-sdlc/cao/profiles/pr-reviewer.md) | Review the candidate diff against requirements, plan and verification evidence; return findings for deterministic routing. |
| Delivery | [`sdlc_remediator`](../../.agentic-sdlc/cao/profiles/remediator.md) | Fix only assigned, eligible review findings; return a remediation summary. |
| Source review | [`sdlc_source_mapper`](../../.agentic-sdlc/cao/profiles/source-mapper.md) | Map changed files, callers, coverage gaps and protected boundaries in a pinned PR snapshot. |
| Source review | [`sdlc_source_correctness`](../../.agentic-sdlc/cao/profiles/source-correctness.md) | Identify substantiated correctness regressions in production or test source. |
| Source review | [`sdlc_source_security`](../../.agentic-sdlc/cao/profiles/source-security.md) | Independently inspect security and reliability regressions. |
| Source review | [`sdlc_source_validator`](../../.agentic-sdlc/cao/profiles/source-validator.md) | Accept, reject or deduplicate every candidate against source evidence; reassess fix eligibility. |
| Source review | [`sdlc_source_feedback`](../../.agentic-sdlc/cao/profiles/source-feedback.md) | Explain validated findings without changing severity or routing; Python renders canonical feedback. |

All profiles use the `claude_code` provider. The delivery supervisor proposes assignments to registered workers and selects required skills. Python validates and executes those assignments and owns stage ordering, retries and artifact persistence. Agents do not launch other agents directly. See [hybrid delivery and extension guide](../workflows/hybrid-delivery.md).

All 13 files explicitly set `role: reviewer`, including the implementer and supervisor. Their explicit `allowedTools` overrides CAO role defaults: planning/delivery use `@builtin`, `fs_read`, `fs_list`, `fs_write`; source review uses only the three filesystem categories. Do not infer permissions from the profile name. Python owns Git operations and verification; human approval is recorded separately. Delivery currently prepares local PR artifacts, while source review can inspect an existing GitHub PR.

The profiles instruct each agent to write one answer file. **Current hooks enforce a broader runtime directory boundary**, as shown below; exact per-step output authorization is proposed in [the hardening plan](../../hardening-plan.md).

| Profiles | Current enforced write scope |
|---|---|
| Planning, code supervisor, PR reviewer | Repository `.agentic-sdlc/runtime/**` |
| Implementer, remediator | Runtime tree plus configured source roots (default `app/**`), narrowed by `write_profiles` where configured |
| Source-review profiles | Only `.agentic-sdlc/runtime/**` inside the isolated run workspace; exported source and other workspace files are not writable |

## Install planning profiles

From the repository root:

```bash
for profile in context-normalizer planning-analyst plan-author plan-reviewer; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
```

This validates and installs all four planning profiles, including the context normalizer required by `sdlc_dev_plan`. Confirm with `cao profile list | rg sdlc_`.

## Install delivery profiles

Validate and install the delivery profiles explicitly:

```bash
for profile in code-supervisor implementer pr-reviewer remediator; do
  cao profile validate ".agentic-sdlc/cao/profiles/$profile.md"
  cao install ".agentic-sdlc/cao/profiles/$profile.md"
done
```

Install the delivery workflow separately, after the profiles (the default hybrid mode needs `sdlc_code_supervisor`). After pulling changes to a profile, reinstall it before the workflow; `cao install` overwrites the installed copy:

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

The profiles grant `fs_write` so agents can save their final answers. For planning and delivery, that grant is constrained by the repository's trusted `PreToolUse` hook in `.claude/settings.json`; source review generates its own guard in the isolated workspace. CAO's `--dangerously-skip-permissions` means Claude permission rules alone cannot provide this path restriction. The roots the implementer and remediator may write are configuration, not profile text: they come from `source_roots` and `write_profiles` in `.agentic-sdlc/cao/specialists.json` (see the [registry reference](../workflows/delivery.md#registry-reference)), so changing them does not require reinstalling a profile. Read [agent answers and write scope](write-scope-hook.md) alongside the actual hooks before changing profiles or runtime answer paths; the table above distinguishes intended answer-only behavior from current enforcement.

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

## Change one profile from Claude to Codex, Gemini or another provider

**This project currently requires more than a profile edit.** CAO supports multiple providers, but [runtime.py](../../.agentic-sdlc/cao/sdlc_workflows/runtime.py) passes the fixed `PROVIDER = "claude_code"` to every `step(PROVIDER, agent, ...)`. Its Python workflow API uses that explicit provider. The profile-based inheritance described for CAO handoff/assign does not automatically change these workflow calls. The existing path guards are also Claude-specific.

The following is migration guidance; per-profile workflow routing and equivalent non-Claude enforcement have not been implemented or live-validated here.

### 1. Select an identifier supported by the installed CAO

| Desired CLI | CAO provider ID | Notes |
|---|---|---|
| Claude Code | `claude_code` | `claude` is the executable name, not this provider ID. |
| OpenAI Codex | `codex` | Requires the Codex CLI and authentication on the CAO host. |
| Google-backed CLI / Gemini migration | `antigravity_cli` | The inspected CAO release uses Antigravity and launches `agy`; it has no `gemini` or `gemini_cli` provider enum. Installing the old `gemini` executable alone does not satisfy this adapter. |
| Other documented providers | `kiro_cli`, `kimi_cli`, `copilot_cli`, `opencode_cli`, `omp`, `hermes`, `cursor_cli` | Install/authenticate the corresponding CLI and check its adapter's permissions and model support. |
| Additional adapters in the inspected installation | `grok_cli`, `mcode` | Present in its enum/manager; recheck availability in the server version you actually deploy. |

Consult [CAO multi-provider support](https://awslabs.github.io/cli-agent-orchestrator/docs/features/multi-provider/) and the [provider enum](https://github.com/awslabs/cli-agent-orchestrator/blob/main/src/cli_agent_orchestrator/models/provider.py). The latter also contains `mock_cli`, a test adapter. To inspect the installed enum without launching an agent, run this using the Python environment that contains **the server's** CAO package:

```bash
python3 -c 'from cli_agent_orchestrator.models.provider import ProviderType; print("\n".join(p.value for p in ProviderType))'
```

The project's Python environment may not contain CAO when it was installed with `uv tool`. Provider IDs name CLI adapters; `model:` separately chooses a model accepted by that CLI. A Gemini model available through another supported adapter does not create a provider named `gemini`.

### 2. Preserve the profile contract and configure the target CLI

For example, change the frontmatter of `.agentic-sdlc/cao/profiles/pr-reviewer.md` to select Codex while retaining its name, description, role, explicit tools, Markdown instructions and answer schema:

```yaml
provider: codex
codexProfile: sdlc_pr_review
```

These are fields to merge into the existing frontmatter, not a complete replacement profile. `sdlc_pr_review` is a named **Codex configuration profile** provisioned on the CAO host; it is distinct from the CAO agent name `sdlc_pr_reviewer`. CAO passes it as `codex --profile sdlc_pr_review`. Check the installed Codex version's profile-file format against [official Codex configuration/security guidance](https://learn.chatgpt.com/docs/agent-approvals-security): older CAO examples use `[profiles.<name>]` tables, while current Codex documentation describes named profile files.

For the Google-backed adapter, use `provider: antigravity_cli`, remove Codex-specific settings, and provision `agy` as described in [CAO's Antigravity guide](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/antigravity-cli.md). For another adapter, use its exact installed provider ID and supported settings. Remove or replace incompatible model names and provider-specific options such as `permissionMode` or `native_agent`; do not put credentials in profile files.

### 3. Establish equivalent enforcement before enabling the profile

Changing providers does not transfer `.claude/settings.json` hooks. The generated source-review hook is Claude-specific too. Verify that the replacement permits required answer delivery while denying source edits for reviewers and preventing changes to tooling, records and other forbidden paths. Implementers need their configured source roots as well. Cover command execution and tool-based writes, not just prompt instructions.

For **Codex**, the inspected CAO adapter defaults to `--yolo`, bypassing sandbox and approvals. Setting `codexProfile` avoids that default unless unrestricted tools (`"*"` or a yolo override) force it back on. `codexConfig` supplies inline configuration overrides but does not, by itself, select the non-yolo launch path. Its `allowedTools` restrictions are injected as instructions, not an equivalent native tool allowlist. See [CAO's Codex adapter guide](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/codex-cli.md).

A read-only Codex sandbox cannot write this project's answer file; a repository-wide `workspace-write` sandbox permits more than a reviewer needs. Use an enforced narrow output policy or an isolated read-only source workspace with a controlled output mount, and adapt answer delivery where necessary. Headless policy must fail denied operations without waiting for interactive approval; the workflow rejects `waiting_user_answer`. Test the effective launch arguments and actual denied operations rather than assuming a named configuration preserves the boundary.

For **Antigravity**, the inspected adapter launches `agy --dangerously-skip-permissions` and uses prompt-based tool restrictions. Provide external isolation or another verified enforcement mechanism before running these profiles on the real repository. Apply the same adapter review to any other provider; `role: reviewer` alone is not containment.

### 4. Route that profile in the Python workflow

Add trusted per-profile provider selection at the shared `_run_stabilized_step()` call. For a small first implementation, the required shape is:

```python
# Illustrative runtime change; this routing does not exist yet.
PROFILE_PROVIDERS = {"sdlc_pr_reviewer": "codex"}

# Inside _run_stabilized_step(), before the existing step() call:
provider = PROFILE_PROVIDERS.get(agent, PROVIDER)
# Pass provider as step()'s first argument; retain its other arguments.
```

Validate routing against installed profiles/providers and reject mismatches before dispatch. For broader use, load the mapping from trusted configuration rather than model output. Do not change the global `PROVIDER` to switch only one profile: that selects the new provider for every shared-runtime step. Adding a provider field to a specialist registry entry also has no effect unless the runtime is changed to read it.

Record the actual provider per step in evidence and update planning/delivery manifests that currently report one global provider. Keep routing stable for a run and its retries. If switching `sdlc_implementer`, remember that it is reused by hybrid workers, integration and verification repair; all those uses change. A new profile name instead requires updating workflow references, registry entries and write-profile authorization.

### 5. Validate, install and exercise the complete path

After implementing routing and enforcement, validate/install the changed profile from the repository root:

```bash
cao profile validate .agentic-sdlc/cao/profiles/pr-reviewer.md
cao install .agentic-sdlc/cao/profiles/pr-reviewer.md --provider codex
cao profile show sdlc_pr_reviewer
```

Keep the `--provider` argument and frontmatter aligned. CAO installation chooses the explicit flag before frontmatter; installation alone does not modify the workflow's explicit `step()` provider. See [CAO CLI installation options](https://awslabs.github.io/cli-agent-orchestrator/docs/reference/cli-commands/#cao-install).

Rebuild/reinstall affected workflow bundles after runtime changes; the shared runtime is embedded into all three. Use [the build/install guide](../build-and-install.md) and respect source review's no-overwrite upgrade procedure. Finish active runs before replacing shared definitions or hooks.

Check source and bundled tests, then run a disposable live fixture proving: the selected profile uses the new provider; other profiles retain theirs; the answer file and JSON contract work; forbidden writes/commands are denied; and repair, timeout, cleanup and replay behave correctly. A successful `cao launch --provider codex` only validates a direct launch, not this workflow path. No profile/provider switch was performed as part of this documentation update.
