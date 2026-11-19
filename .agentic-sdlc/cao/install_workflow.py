#!/usr/bin/env python3
"""Build, validate and atomically install one SDLC workflow into CAO.

The workflow code goes into a frozen, content-addressed library (sdlc-lib/<id>/ beside CAO's workflows folder),
written once and never changed; CAO gets a small entry script that checks and imports it (build_library.py).
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

from build_library import WORKFLOWS, build_entry, build_library, verify_library, write_library

REGISTERED_NAMES = {'dev_plan': 'sdlc_dev_plan', 'deliver': 'sdlc_deliver', 'source_review': 'source_review',
                    'source_remediate': 'source_remediate', 'source_followup': 'source_followup'}
SOURCE_ROLES = ('mapper', 'correctness', 'security', 'standards', 'validator', 'feedback')
FOLLOWUP_ROLES = ('followup-judge', 'followup-changes')


def cao(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(['cao', *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(f"cao {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result


def _make_writable(root: Path) -> None:
    for path in [root, *root.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def ensure_library(files: dict[str, bytes], manifest: dict[str, str], library_id: str, library_root: Path) -> Path:
    """The library folder for this id: written once (staged, then renamed into place), otherwise verified and reused."""
    target = library_root / library_id
    if not target.exists():
        library_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f'.{library_id}-', dir=library_root))
        try:
            write_library(files, manifest, staging)
            try:
                os.rename(staging, target)
            except OSError:
                if not target.exists():
                    raise
        finally:
            if staging.exists():
                _make_writable(staging)
                shutil.rmtree(staging)
    try:
        verify_library(target, manifest)
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from None
    return target


def install(workflow: str, repository_root: Path, workflow_dir: Path, *, validate_only: bool = False,
            library_root: Path | None = None) -> Path:
    name = REGISTERED_NAMES[workflow]
    package = repository_root / '.agentic-sdlc/cao/sdlc_workflows'
    files, manifest, library_id = build_library(package)
    library_root = library_root or workflow_dir.parent / 'sdlc-lib'
    library = library_root / library_id
    source = build_entry(workflow, library, manifest, package)
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
        # The library must exist before an entry that points to it does; it is never replaced.
        ensure_library(files, manifest, library_id, library_root)
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
            if workflow == 'source_remediate':
                # Shared remediator upgrades are explicit; do not replace a profile another workflow uses.
                for profile in ('sdlc_remediator', 'sdlc_source_fix_reviewer'):
                    if cao('profile', 'show', profile, check=False).returncode:
                        raise FileNotFoundError(f'Install required profile first: {profile}')
            if workflow == 'source_followup':
                # The follow-up owns its profiles, so an install replaces them with the repository's copy.
                if cao('profile', 'show', 'sdlc_source_standards', check=False).returncode:
                    raise FileNotFoundError('Install source_review first: the follow-up uses its sdlc_source_standards profile')
                profiles = repository_root / '.agentic-sdlc/cao/profiles'
                for role in FOLLOWUP_ROLES:
                    cao('profile', 'validate', str(profiles / f'source-{role}.md'))
                for role in FOLLOWUP_ROLES:
                    cao('install', str(profiles / f'source-{role}.md'))
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
    parser.add_argument('--library-root', type=Path, default=None,
                        help='where libraries live (default: sdlc-lib beside the workflow folder)')
    parser.add_argument('--validate-only', action='store_true',
                        help='Validate a temporary entry without installing the library, workflows or profiles')
    args = parser.parse_args()
    target = install(args.workflow, args.repository_root.resolve(), args.workflow_dir.resolve(),
                     validate_only=args.validate_only,
                     library_root=args.library_root.resolve() if args.library_root else None)
    print(f"{'Validated entry for' if args.validate_only else 'Installed'} {target}")


if __name__ == '__main__':
    main()
