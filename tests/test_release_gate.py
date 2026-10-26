"""Real Git-index publication checks, including index/worktree divergence."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_gate', ROOT / 'scripts/release_check.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-release-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)

    def stage(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
        self.git('add', '--', name)
        return path

    def test_only_public_source_passes(self):
        self.stage('README.md', '# Example project\nNo local records.\n')
        count, issues = gate.inspect_index(self.root)
        self.assertEqual(count, 1)
        self.assertEqual(issues, [])

    def test_private_path_and_known_credential_are_blocked(self):
        credential = ('private-' + 'A' * 40).encode()
        self.stage('data/owner.credentials.json', '{"token":"' + credential.decode() + '"}')
        _, issues = gate.inspect_index(self.root, {credential})
        reasons = [reason for _, reason in issues]
        self.assertIn('private artifact path', reasons)
        self.assertIn('matches a local private credential', reasons)

    def test_index_is_checked_even_after_worktree_is_cleaned(self):
        path = self.stage('example.txt', 'gh' + 'p_' + 'A' * 40)
        path.write_text('Safe working copy', encoding='utf-8')
        _, issues = gate.inspect_index(self.root)
        self.assertIn(('example.txt', 'github-token'), issues)


if __name__ == '__main__':
    unittest.main()
