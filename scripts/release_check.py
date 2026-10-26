"""Inspect the exact Git index before publication; never print secret values."""
import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

PRIVATE_PATHS = (
    'data/*', '.venv/*', '.venv-*/*', '.release/*', '_local/*',
    'design/screenshots/*', 'prototype/*', 'preview.cmd', 'docs/VALIDATION.md',
    'docs/OPTIMIZATION-VALIDATION.md', 'tests/last-run.txt',
    '*/references/local.json', '*.credentials.json', '*.sqlite*', '*.db',
    '*.db-wal', '*.db-shm', '*.log', '*.zip', '*.pem', '*.key', '*.pfx', '*.p12',
)
SECRET_PATTERNS = (
    ('private-key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ('github-token', re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b')),
    ('provider-key', re.compile(rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{25,}\b')),
)
LOCAL_PATH = re.compile(r'(?i)\b[A-Z]:[\\/](?:Users|aiuser)[\\/]')


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if result.returncode:
        raise ValueError('Git index could not be read. Initialize and stage the public files first.')
    return result.stdout


def collect_private_values(directory):
    """Opt-in comparison with local credential values; no data leaves the machine."""
    values = set()
    if not directory:
        return values
    for path in directory.glob('*.credentials.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            for key in ('token', 'code', 'client_token', 'management_token'):
                value = data.get(key)
                if isinstance(value, str) and len(value) >= 24:
                    values.add(value.encode('utf-8'))
    return values


def inspect_index(repo, private_values=()):
    entries = git(repo, 'ls-files', '--stage', '-z').split(b'\0')
    findings = []
    count = 0
    for entry in filter(None, entries):
        metadata, path_bytes = entry.split(b'\t', 1)
        mode, _, stage = metadata.decode('ascii').split()
        path = path_bytes.decode('utf-8')
        count += 1
        name = PurePosixPath(path).name
        if (mode in ('120000', '160000') or stage != '0'):
            findings.append((path, 'symlink, submodule or unmerged index entry'))
            continue
        if (any(fnmatch.fnmatchcase(path, pattern) for pattern in PRIVATE_PATHS)
                or (name.startswith('.env') and name != '.env.example')
                or name in ('id_rsa', 'id_ed25519', 'known_hosts')):
            findings.append((path, 'private artifact path'))
        blob = git(repo, 'show', ':' + path)
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(blob):
                findings.append((path, label))
        if any(value in blob for value in private_values):
            findings.append((path, 'matches a local private credential'))
        if path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            if not path.startswith('docs/images/'):
                findings.append((path, 'image is outside the reviewed public demo folder'))
            continue
        try:
            text = blob.decode('utf-8')
        except UnicodeDecodeError:
            findings.append((path, 'unreviewed binary file'))
            continue
        if LOCAL_PATH.search(text.replace('\\\\', '\\')):
            findings.append((path, 'personal absolute machine path'))
    if not count:
        findings.append(('<index>', 'no staged/indexed files to inspect'))
    return count, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--private-dir', type=Path, help='Opt-in local credential comparison; values are never printed')
    args = parser.parse_args()
    try:
        count, findings = inspect_index(args.repo, collect_private_values(args.private_dir))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if findings:
        for path, reason in findings:
            print(f'BLOCKED: {path}: {reason}')
        return 1
    print(f'PASS: {count} exact indexed files; no blocked artifacts, known token patterns or personal machine paths.')
    print('This heuristic check supplements review; it is not proof that arbitrary content is safe to publish.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
