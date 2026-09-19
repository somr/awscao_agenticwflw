#!/usr/bin/env python3
"""Build, validate and atomically install one SDLC workflow into CAO.

Existing workflow names and source-review profile refusal rules are preserved.
No active server or worker is restarted. Temporary candidates and locks are
unique to the workflow being installed.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from build_workflow import WORKFLOWS, build_source

REGISTERED_NAMES = {'dev_plan': 'sdlc_dev_plan', 'deliver': 'sdlc_deliver', 'source_review': 'source_review'}
SOURCE_ROLES = ('mapper', 'correctness', 'security', 'validator', 'feedback')


def cao(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(['cao', *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(f"cao {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result


def install(workflow: str, repository_root: Path, workflow_dir: Path, *, validate_only: bool = False) -> Path:
    name = REGISTERED_NAMES[workflow]
    source = build_source(workflow, repository_root / '.agentic-sdlc/cao/sdlc_workflows')
    cao('workflow', 'list')
    workflow_dir.mkdir(parents=True, exist_ok=True)
    target = workflow_dir / f'{name}.py'
    lock = workflow_dir / f'.{name}.install.lock'
    # Exclusive creation serializes this installer without interfering with
    # unrelated workflows. A crash leaves a lock for the operator to inspect.
    with lock.open('x'):
        pass
    staged = None
    try:
        if not validate_only:
            for suffix in ('.yaml', '.yml'):
                if (workflow_dir / (name + suffix)).exists():
                    raise FileExistsError(f'Workflow name collides with {name}{suffix}')
            if workflow == 'source_review' and target.exists():
                raise FileExistsError('Refusing to overwrite installed source_review; see the upgrade guide')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix=name + '_candidate_',
                                         suffix='.py', dir=workflow_dir, delete=False) as stream:
            staged = Path(stream.name)
            stream.write(source)
        cao('workflow', 'validate', str(staged))
        if validate_only:
            return target
        if workflow == 'source_review':
            profiles = repository_root / '.agentic-sdlc/cao/profiles'
            for role in SOURCE_ROLES:
                cao('profile', 'validate', str(profiles / f'source-{role}.md'))
            for role in SOURCE_ROLES:
                if cao('profile', 'show', f'sdlc_source_{role}', check=False).returncode == 0:
                    raise FileExistsError(f'Refusing to replace profile sdlc_source_{role}')
            for role in SOURCE_ROLES:
                cao('install', str(profiles / f'source-{role}.md'))
            # Exclusive target creation retains source-review's no-overwrite rule.
            os.link(staged, target)
        else:
            if target.exists():
                shutil.copy2(target, str(target) + '.bak')
            os.replace(staged, target)
        cao('workflow', 'validate', str(target))
        return target
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', choices=WORKFLOWS)
    parser.add_argument('--repository-root', type=Path, default=Path.cwd())
    parser.add_argument('--workflow-dir', type=Path,
                        default=Path(os.environ.get('CAO_WORKFLOW_DIR', str(Path.home() / '.aws/cli-agent-orchestrator/workflows'))))
    parser.add_argument('--validate-only', action='store_true', help='Validate a temporary bundle without installing workflows/profiles')
    args = parser.parse_args()
    target = install(args.workflow, args.repository_root.resolve(), args.workflow_dir.resolve(), validate_only=args.validate_only)
    print(f"{'Validated bundle for' if args.validate_only else 'Installed'} {target}")


if __name__ == '__main__':
    main()
