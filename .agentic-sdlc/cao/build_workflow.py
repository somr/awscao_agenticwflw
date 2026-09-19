#!/usr/bin/env python3
"""Build a deterministic standalone CAO workflow from the maintained modules.

This deliberately supports a small, explicit module contract: relative named
imports, unique top-level definitions, and no module-namespace aliases. It does
not execute source or resolve third-party imports. Imported SDLC source becomes
ordinary, visible Python in the artifact so CAO can lint and freeze all of it.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path

WORKFLOWS = {'dev_plan': 'planning', 'deliver': 'delivery', 'source_review': 'source_review'}
PACKAGE = Path(__file__).resolve().parent / 'sdlc_workflows'


class BuildError(ValueError):
    pass


def _bindings(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [name.id for target in node.targets for name in ast.walk(target) if isinstance(name, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    if isinstance(node, ast.Import):
        return [alias.asname or alias.name.split('.')[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [alias.asname or alias.name for alias in node.names]
    return []


def build_source(workflow: str, package: Path = PACKAGE) -> str:
    """Flatten our static dependency graph, refusing ambiguous global bindings."""
    if workflow not in WORKFLOWS:
        raise BuildError(f'Unknown workflow: {workflow}')
    modules: dict[str, tuple[str, ast.Module]] = {}
    order: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise BuildError(f'Circular module dependency: {name}')
        if name in modules:
            return
        if not name.isidentifier():
            raise BuildError(f'Invalid local module name: {name}')
        path = package / f'{name}.py'
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(path))
        visiting.add(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {'__file__', '__package__'}:
                raise BuildError(f'{name}: module-relative paths cannot survive snapshot relocation')
            if isinstance(node, ast.ImportFrom) and node.level:
                if node not in tree.body or node.level != 1 or not node.module or '.' in node.module:
                    raise BuildError(f'{name}: local imports must be top-level, single-level module imports')
                if any(a.name == '*' or a.asname for a in node.names):
                    raise BuildError(f'{name}: wildcard and aliased local imports cannot be bundled')
                visit(node.module)
                dependency_tree = modules[node.module][1]
                exports = {binding for item in dependency_tree.body for binding in _bindings(item)}
                for alias in node.names:
                    if alias.name not in exports:
                        raise BuildError(f'{name}: {node.module} does not export {alias.name}')
            elif isinstance(node, ast.Import) and any(a.name.startswith('sdlc_workflows') for a in node.names):
                raise BuildError(f'{name}: use relative named imports for local dependencies')
            elif isinstance(node, ast.ImportFrom) and (node.module or '').startswith('sdlc_workflows'):
                raise BuildError(f'{name}: use relative named imports for local dependencies')
        visiting.remove(name)
        modules[name] = (source, tree)
        order.append(name)

    initializer = package / '__init__.py'
    if initializer.exists():
        initializer_tree = ast.parse(initializer.read_text(encoding='utf-8'))
        if any(not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)) for n in initializer_tree.body):
            raise BuildError('Package __init__.py must contain only documentation, not executable code')
        visit('__init__')
    visit(WORKFLOWS[workflow])
    bindings: dict[str, tuple[str, str]] = {}
    sections = []
    for name in order:
        source, tree = modules[name]
        remove = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.level or node.module == '__future__'):
                if node.module == '__future__' and any(a.name != 'annotations' for a in node.names):
                    raise BuildError(f'{name}: unsupported future import')
                remove.append((node.lineno, node.end_lineno))
                continue
            # Repeating the exact same external import is safe. Definitions and
            # constants must have one owner, even if their text happens to match.
            for binding in _bindings(node):
                if binding == 'SDLC_BUNDLE_MANIFEST':
                    raise BuildError(f'{name}: reserved bundle manifest name')
                signature_node = node
                if isinstance(node, ast.ImportFrom):
                    alias = next(a for a in node.names if (a.asname or a.name) == binding)
                    signature_node = ast.ImportFrom(module=node.module, names=[alias], level=node.level)
                elif isinstance(node, ast.Import):
                    alias = next(a for a in node.names if (a.asname or a.name.split('.')[0]) == binding)
                    signature_node = ast.Import(names=[alias])
                signature = ast.dump(signature_node, include_attributes=False)
                if binding in bindings:
                    owner, previous = bindings[binding]
                    if not isinstance(node, (ast.Import, ast.ImportFrom)) or signature != previous:
                        raise BuildError(f'Conflicting binding {binding}: {owner} and {name}')
                else:
                    bindings[binding] = (name, signature)
        lines = source.splitlines(keepends=True)
        for start, end in sorted(remove, reverse=True):
            del lines[start - 1:end]
        sections.append(f'# --- sdlc_workflows/{name}.py ---\n' + ''.join(lines).strip() + '\n')
    manifest = {name + '.py': hashlib.sha256(modules[name][0].encode()).hexdigest() for name in order}
    result = ('"""Generated CAO bundle. Edit sdlc_workflows/, then rebuild; do not edit this file."""\n'
              'from __future__ import annotations\n\n'
              'SDLC_BUNDLE_MANIFEST = ' + repr(manifest) + '\n\n' + '\n\n'.join(sections))
    compile(result, f'{workflow}.py', 'exec')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', choices=WORKFLOWS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = build_source(args.workflow)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(source, encoding='utf-8')
    print(json.dumps({'workflow': args.workflow, 'output': str(args.output),
                      'sha256': hashlib.sha256(source.encode()).hexdigest()}))


if __name__ == '__main__':
    main()
