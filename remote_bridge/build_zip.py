#!/usr/bin/env python3
"""Build the downloadable Windows package from a committed state of this folder.

    python build_zip.py [--out DIR]

Writes Velvet-Remote-Bridge-<version>-<commit>.zip. Refuses to build while
remote_bridge/ has uncommitted changes, so a package always names the exact
commit it contains (an old ZIP can never pass for the reviewed head).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
FILES = ['bridge.py', 'queue_sync.js', 'config.example.json', 'start_bridge.bat', 'run_tests.bat',
         'README_安装说明.md', 'VERSION.txt', 'TEST_RESULTS.md',
         'tests/test_bridge.py', 'tests/queue_sync.test.mjs']


def git(*args: str) -> str:
    return subprocess.run(['git', '-C', str(HERE), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default=str(HERE / 'dist'))
    args = parser.parse_args(argv)
    if git('status', '--porcelain', '--', '.'):
        print('remote_bridge/ has uncommitted changes: commit them first.', file=sys.stderr)
        return 1
    commit = git('rev-parse', '--short=7', 'HEAD')
    version = (HERE / 'VERSION.txt').read_text(encoding='utf-8').split()[3]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f'Velvet-Remote-Bridge-{version}-{commit}.zip'
    root = 'Velvet-Remote-Bridge'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            # The committed bytes, not the working copy (line endings included).
            archive.writestr(f'{root}/{name}', subprocess.run(
                ['git', '-C', str(HERE), 'show', f'HEAD:remote_bridge/{name}'],
                capture_output=True, check=True).stdout)
        archive.writestr(f'{root}/BUILD_INFO.txt',
                         f'Velvet Remote Bridge {version}\ncommit {git("rev-parse", "HEAD")}\n')
    print(target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
