"""Install the same portable skill into any chosen skill directory."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'skills/gongxi'
FILES = ('SKILL.md', 'scripts/gongxi.py')


def is_link_or_junction(path):
    path = Path(path)
    is_junction = getattr(path, 'is_junction', None)
    return path.is_symlink() or bool(is_junction and is_junction())


def install(target):
    target = target.expanduser().absolute()
    manifest = target / 'references/local.json'

    # Check the ownership record as well as copied files: write_text follows a
    # symlink at references/local.json and could otherwise overwrite a file
    # outside the selected skill directory.
    for candidate in (manifest, *manifest.parents):
        if is_link_or_junction(candidate):
            raise ValueError('A destination traverses a symbolic link; nothing should be installed here.')
    if target.exists():
        if not manifest.is_file():
            raise ValueError('Existing directory is not a managed Common Ground skill; nothing overwritten.')
        old = json.loads(manifest.read_text(encoding='utf-8'))
        if old.get('managed_by') != 'gongxi-skill-installer':
            raise ValueError('Existing skill ownership is unknown; nothing overwritten.')
        hashes = old.get('hashes')
        if not isinstance(hashes, dict) or set(hashes) != set(FILES):
            raise ValueError('Managed skill metadata is incomplete; nothing overwritten.')
        for relative, expected in hashes.items():
            if not isinstance(expected, str) or len(expected) != 64 or any(ch not in '0123456789abcdef' for ch in expected):
                raise ValueError('Managed skill metadata contains an invalid file hash; nothing overwritten.')
            path = target / relative
            if is_link_or_junction(path) or (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError(f'Locally edited skill file {relative}; choose another target to preserve your edits.')
    hashes = {}
    for relative in FILES:
        destination = target / relative
        if any(is_link_or_junction(parent) for parent in (destination, *destination.parents)):
            raise ValueError('A destination traverses a symbolic link; nothing should be installed here.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, destination)
        hashes[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'managed_by': 'gongxi-skill-installer', 'project_root': str(ROOT), 'hashes': hashes}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description='Install a harness-neutral Common Ground skill')
    parser.add_argument('--target', type=Path, default=Path.home() / '.agents/skills/gongxi', help='Exact skill folder; default ~/.agents/skills/gongxi')
    args = parser.parse_args()
    try:
        installed = install(args.target)
        print(json.dumps({'installed': str(installed), 'entry': str(installed / 'SKILL.md'), 'project_root': str(ROOT)}, ensure_ascii=True))
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
