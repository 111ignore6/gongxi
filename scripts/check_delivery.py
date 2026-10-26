"""Read-only check of local delivery links and the running participation guide."""
from pathlib import Path
import re
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    count = 0
    for source in [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md')]:
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', source.read_text(encoding='utf-8')):
            if target.startswith(('http:', 'https:', '#')):
                continue
            path = (source.parent / target.split('#')[0]).resolve()
            if not path.exists():
                raise RuntimeError(f'{source}: missing {target}')
            count += 1
    with urlopen('http://127.0.0.1:4284/participate.md', timeout=5) as response:
        guide = response.read().decode('utf-8')
    if guide != (ROOT / 'participate.md').read_text(encoding='utf-8'):
        raise RuntimeError('The live participation guide differs from the saved source.')
    print(f'PASS: {count} local artifact links; live guide matches saved source.')


if __name__ == '__main__':
    main()
