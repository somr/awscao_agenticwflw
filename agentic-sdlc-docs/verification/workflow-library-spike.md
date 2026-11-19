# Frozen workflow library: spike (2026-11-18)

Result: CAO 2.5.4 accepts and runs a small entry script that checks a content-addressed library's hashes, puts it
on `sys.path` and imports the workflow from it; a prompt read from a template file inside the library reaches the
agent; and a changed library file stops the run before any step. Step 0 of
[the workflow library plan](../plans/workflow-library-and-prompt-files.md) is done.

## Method

- Library: package `sdlc_spike` (`__init__.py`, `flow.py` calling `step()` with a relative import of
  `prompts_helper.py`, which fills `prompts/shared/ask.md` with `string.Template`), written to
  `~/.aws/cli-agent-orchestrator/sdlc-lib-spike/<id>/`, `<id>` = first 16 hex digits of the sha256 of the manifest
  JSON, files made read-only.
- Entry: `sdlc_tmp_library_spike.py` in CAO's workflows folder, 1,382 characters: `INPUTS` literal, library path,
  manifest; checks the file set and every hash, then `sys.path.insert(0, LIBRARY)` and
  `from sdlc_spike.flow import main`.
- Both removed afterwards. The user approved the entry's plan with `cao workflow approve`.

| Check | Result |
|---|---|
| `cao workflow validate --json` | `status: pass`, `findings: []`; the linter does not see the `step()` call in the library (expected; plan D5 adds a repository test instead) |
| Run `library-spike-1` | completed in about 20 s; the agent answered `{"model": "claude-haiku-5-5", "prompt_from": "library file"}`; `__file__` of the running module was inside the library folder |
| Run `library-spike-tamper-1`, after appending a line to `prompts/shared/ask.md` | failed with no step started: "library … does not match this workflow: prompts/shared/ask.md changed; reinstall"; no new approval was needed because the entry did not change |

Not checked here: a CAO resume of a run started from a library entry (the snapshot is the entry, whose library path
and hashes are fixed, so it should load the same code; to be confirmed in a later live run if a resume happens).
Python could not write `__pycache__` into the read-only library, which is harmless (the check ignores that folder).
