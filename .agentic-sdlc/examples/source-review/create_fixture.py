#!/usr/bin/env python3
"""Create a disposable, local two-commit review fixture; never touches a PR."""
import argparse
import json
from pathlib import Path
import subprocess


def create(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('config', 'user.name', 'Source Review Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (root / 'pagination.py').write_text('''def _first_items(items, limit):
    """Return up to limit items, preserving order. Limit is non-negative."""
    return items[:limit]
''')
    (root / 'accounts.py').write_text('''def read_account(actor, account):
    """Return an account only to its owner."""
    if actor.id != account.owner_id:
        raise PermissionError("Not your account")
    return account.data
''')
    (root / 'test_pagination.py').write_text("""from pagination import _first_items


def test_short_list():
    assert _first_items([1], 3) == [1]
""")
    git('add', '.')
    git('commit', '-qm', 'Correct baseline')
    base = git('rev-parse', 'HEAD')
    (root / 'pagination.py').write_text('''def _first_items(items, limit):
    """Return up to limit items, preserving order. Limit is non-negative."""
    return items[:limit + 1]
''')
    (root / 'accounts.py').write_text('''def read_account(actor, account):
    """Return an account only to its owner."""
    return account.data
''')
    git('add', '.')
    git('commit', '-qm', 'Introduce bounded bug and protected-boundary bug')
    return {'source_repository': str(root.resolve()), 'base_sha': base, 'head_sha': git('rev-parse', 'HEAD')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(json.dumps(create(parser.parse_args().destination), indent=2))
