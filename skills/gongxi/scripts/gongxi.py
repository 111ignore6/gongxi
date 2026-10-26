"""Portable skill entry point: locate the project and forward ordinary CLI calls."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys


def is_project(path):
    return (path / 'backend/app.py').is_file() and (path / 'scripts/agent_client.py').is_file() and (path / 'launch.py').is_file()


def project_root():
    configured = os.environ.get('GONGXI_ROOT')
    if configured:
        root = Path(configured).expanduser().resolve()
        if not is_project(root):
            raise ValueError('GONGXI_ROOT does not identify a Common Ground project.')
        return root
    for parent in Path(__file__).resolve().parents:
        if is_project(parent):
            return parent
    location = Path(__file__).resolve().parents[1] / 'references/local.json'
    if location.is_file():
        root = Path(json.loads(location.read_text(encoding='utf-8'))['project_root']).expanduser().resolve()
        if is_project(root):
            return root
    raise ValueError('Project not found. Set GONGXI_ROOT to the Common Ground project directory.')


def python_for(root):
    candidate = root / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    return str(candidate) if candidate.is_file() else sys.executable


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Common Ground portable skill CLI')
    parser.add_argument('mode', choices=('info', 'service', 'client', 'backup'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = project_root()
    if args.mode == 'info':
        print(json.dumps({'project_root': str(root), 'skill_dir': str(Path(__file__).resolve().parents[1]), 'python': python_for(root)}, ensure_ascii=False))
        return 0
    script = {'service': 'runtime.py', 'client': 'agent_client.py', 'backup': 'backup.py'}[args.mode]
    target = root / 'scripts' / script
    if not target.is_file():
        raise ValueError(f'This project does not provide scripts/{script}. Update the project before using this command.')
    return subprocess.call([python_for(root), str(target), *args.arguments], cwd=root)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
