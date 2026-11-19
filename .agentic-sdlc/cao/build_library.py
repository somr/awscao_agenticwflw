#!/usr/bin/env python3
"""Build the frozen workflow library and the small CAO entry script of each workflow.

The library is the sdlc_workflows package as it is (every .py module and every prompts/<workflow>/*.md file), plus
manifest.json listing each file's sha256. Its id is derived from that manifest, so the same tree always gives the
same id. An entry script carries the workflow's INPUTS literally (CAO reads them without running the script), the
library's absolute path and the manifest; before importing anything it checks that the library holds exactly the
listed files with those hashes, so a run (or a resumed run) never executes different code than was installed.
See agentic-sdlc-docs/build-and-install.md.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import inspect
import json
import pprint
from pathlib import Path

WORKFLOWS = {'dev_plan': 'planning', 'deliver': 'delivery', 'source_review': 'source_review',
             'source_remediate': 'source_remediation', 'source_followup': 'source_followup'}
PACKAGE = Path(__file__).resolve().parent / 'sdlc_workflows'
PACKAGE_NAME = 'sdlc_workflows'
MANIFEST_FILE = 'manifest.json'
ID_LENGTH = 16


class BuildError(ValueError):
    pass


def library_files(package: Path = PACKAGE) -> dict[str, bytes]:
    """Every file the library ships, by path relative to the package, in sorted order."""
    files: dict[str, bytes] = {}
    for path in sorted(package.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(package).as_posix()
        if path.suffix == '.py' or (relative.startswith('prompts/') and path.suffix == '.md'):
            files[relative] = path.read_bytes()
        elif path.suffix not in ('.pyc',):
            raise BuildError(f'{relative}: only .py modules and prompts/**/*.md belong in the library')
    if '__init__.py' not in files:
        raise BuildError('the package has no __init__.py')
    return files


def manifest_bytes(manifest: dict[str, str]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, indent=1) + '\n').encode()


def build_library(package: Path = PACKAGE) -> tuple[dict[str, bytes], dict[str, str], str]:
    """(files, manifest, library id) for the package."""
    files = library_files(package)
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    library_id = hashlib.sha256(manifest_bytes(manifest)).hexdigest()[:ID_LENGTH]
    return files, manifest, library_id


def verify_library(library, manifest):
    """Stop unless the library holds exactly the manifest's files with their hashes (copied into every entry)."""
    library = Path(library)
    package = library / 'sdlc_workflows'
    problem = None
    if not package.is_dir():
        problem = 'it is missing'
    else:
        found = {path.relative_to(package).as_posix() for path in package.rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts}
        if found != set(manifest):
            extra, missing = sorted(found - set(manifest)), sorted(set(manifest) - found)
            problem = f'files differ (extra: {extra}, missing: {missing})'
        else:
            for name in sorted(manifest):
                if hashlib.sha256((package / name).read_bytes()).hexdigest() != manifest[name]:
                    problem = f'{name} changed'
                    break
        recorded = library / 'manifest.json'
        if problem is None and (not recorded.is_file()
                                or json.loads(recorded.read_text(encoding='utf-8')) != manifest):
            problem = 'manifest.json differs'
    if problem is not None:
        raise SystemExit(f'Workflow library {library} does not match this installed workflow: {problem}. '
                         'Reinstall the workflow; a run started from the old library cannot resume.')


def _inputs_literal(package: Path, module: str) -> object:
    tree = ast.parse((package / f'{module}.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'INPUTS' for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError as exc:
                raise BuildError(f'{module}.INPUTS must be a literal (CAO reads it without running the script)') from exc
    raise BuildError(f'{module} defines no INPUTS')


def build_entry(workflow: str, library: Path, manifest: dict[str, str], package: Path = PACKAGE) -> str:
    """The CAO entry script of one workflow, running it from the library at its absolute path."""
    if workflow not in WORKFLOWS:
        raise BuildError(f'Unknown workflow: {workflow}')
    module = WORKFLOWS[workflow]
    inputs = _inputs_literal(package, module)
    source = (
        f'"""Generated CAO entry for the {workflow} workflow; do not edit. Change .agentic-sdlc/cao/sdlc_workflows/ '
        'and reinstall.\n\n'
        'The code runs from the frozen library below. Before importing it, this script checks that the library holds\n'
        'exactly the files it was installed with, so a run or a resumed run never executes different code.\n"""\n'
        'from __future__ import annotations\n'
        'import hashlib\n'
        'import json\n'
        'import sys\n'
        'from pathlib import Path\n\n'
        f'INPUTS = {pprint.pformat(inputs, indent=1, width=110, sort_dicts=False)}\n'
        f'LIBRARY = {str(library)!r}\n'
        f'MANIFEST = {pprint.pformat(manifest, indent=1, width=110)}\n\n\n'
        f'{inspect.getsource(verify_library)}\n\n'
        'verify_library(LIBRARY, MANIFEST)\n'
        'sys.dont_write_bytecode = True\n'
        'sys.path.insert(0, LIBRARY)\n'
        f'from {PACKAGE_NAME}.{module} import main  # noqa: E402\n\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )
    compile(source, f'{workflow}.py', 'exec')
    return source


def write_library(files: dict[str, bytes], manifest: dict[str, str], destination: Path) -> None:
    """Write a library into an empty folder and make it read-only."""
    package = destination / PACKAGE_NAME
    for name, content in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (destination / MANIFEST_FILE).write_bytes(manifest_bytes(manifest))
    for path in sorted(destination.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    destination.chmod(0o555)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', choices=WORKFLOWS)
    parser.add_argument('--library', type=Path, required=True, help='absolute library folder the entry points to')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    _, manifest, library_id = build_library()
    source = build_entry(args.workflow, args.library, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(source, encoding='utf-8')
    print(json.dumps({'workflow': args.workflow, 'library_id': library_id, 'output': str(args.output),
                      'sha256': hashlib.sha256(source.encode()).hexdigest()}))


if __name__ == '__main__':
    main()
