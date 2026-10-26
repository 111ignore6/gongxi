"""Focused release security regressions using isolated files and loopback HTTP."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

from scripts import agent_client, backup, install_skill, runtime


class ReleaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-release-security-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def serve(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(3)

        self.addCleanup(stop)
        return server

    def test_credential_acl_is_applied_before_secret_bytes_are_written(self):
        target = self.root / 'private' / 'credential.json'
        original = agent_client.restrict_private_file
        observed = []

        def inspect_empty_then_restrict(path):
            observed.append(Path(path).stat().st_size)
            original(path)

        with mock.patch.object(agent_client, 'restrict_private_file', side_effect=inspect_empty_then_restrict):
            agent_client.secure_save(target, {'token': 'test-only-secret-sentinel'})

        self.assertEqual(observed, [0])
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_client_does_not_forward_bearer_token_across_redirect(self):
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        receiver = self.serve(Receiver)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{receiver.server_port}/capture')
                self.end_headers()

            def log_message(self, *_args):
                pass

        redirect = self.serve(Redirect)

        client = agent_client.Client(f'http://127.0.0.1:{redirect.server_port}', 'test-only-secret-sentinel')
        with self.assertRaises(agent_client.ClientError):
            client.request('GET', '/api/state')
        self.assertEqual(received, [])

    def test_runtime_management_stop_does_not_follow_redirect(self):
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.headers.get('X-Gongxi-Management-Token'))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        receiver = self.serve(Receiver)

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header('Location', f'http://127.0.0.1:{receiver.server_port}/capture')
                self.end_headers()

            def log_message(self, *_args):
                pass

        redirect = self.serve(Redirect)

        self.assertFalse(runtime.request_managed_stop(
            redirect.server_port,
            {'instance_id': 'test-instance'},
            'test-project', 'test-data', 'test-only-management-sentinel',
        ))
        self.assertEqual(received, [])

    def test_restore_restricts_staging_file_before_archive_extraction(self):
        archive = self.root / 'placeholder.zip'
        archive.write_bytes(b'test')
        target = self.root / 'new-data'
        observed = []

        def fake_validate(_archive, extraction_target=None):
            if extraction_target is not None:
                observed.append(Path(extraction_target).stat().st_size)
                raise backup.BackupError('test stopped after ACL ordering check')
            return {'created_at': 'test-only', 'table_counts': {}}

        original = backup.private_file

        def inspect_then_restrict(path):
            observed.append(Path(path).stat().st_size)
            original(path)

        with mock.patch.object(backup, 'private_file', side_effect=inspect_then_restrict), \
                mock.patch.object(backup, 'validate_archive', side_effect=fake_validate):
            with self.assertRaises(backup.BackupError):
                backup.restore(archive, target)
        self.assertEqual(observed, [0, 0])
        self.assertEqual(list(target.iterdir()), [])

    def test_restore_does_not_overwrite_database_planted_after_initial_check(self):
        archive = self.root / 'placeholder.zip'
        archive.write_bytes(b'test')
        target = self.root / 'race-data'
        db_path = target / backup.DB_NAME
        publish = os.rename if os.name == 'nt' else os.link

        def fake_validate(_archive, extraction_target=None):
            if extraction_target is not None:
                Path(extraction_target).write_bytes(b'restored-test-database')
            return {'created_at': 'test-only', 'table_counts': {}}

        def plant_then_publish(source, destination):
            Path(destination).write_bytes(b'user-file-to-preserve')
            return publish(source, destination)

        with mock.patch.object(backup, 'validate_archive', side_effect=fake_validate), \
                mock.patch.object(backup.os, 'rename' if os.name == 'nt' else 'link', side_effect=plant_then_publish):
            with self.assertRaises(FileExistsError):
                backup.restore(archive, target)
        self.assertEqual(db_path.read_bytes(), b'user-file-to-preserve')

    def test_backup_verification_restricts_temp_directory_before_extraction(self):
        archive_path = self.root / 'invalid-database.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(backup.DB_MEMBER, b'not a SQLite database')
            archive.writestr(backup.MANIFEST_MEMBER, json.dumps({
                'format': backup.FORMAT,
                'created_at': 'test-only',
            }))
        observed = []
        original = backup.private_directory

        def inspect_empty_then_restrict(path):
            observed.append(list(Path(path).iterdir()))
            original(path)

        with mock.patch.object(backup, 'private_directory', side_effect=inspect_empty_then_restrict):
            with self.assertRaises(Exception):
                backup.validate_archive(archive_path)
        self.assertEqual(observed, [[]])

    def test_installer_rejects_manifest_symlink_without_touching_target(self):
        target = self.root / 'skill'
        (target / 'references').mkdir(parents=True)
        outside = self.root / 'outside.json'
        original = '{"keep":"this file"}\n'
        outside.write_text(original, encoding='utf-8')
        manifest = target / 'references' / 'local.json'
        try:
            manifest.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'symbolic links unavailable: {error}')

        with self.assertRaises(ValueError):
            install_skill.install(target)
        self.assertEqual(outside.read_text(encoding='utf-8'), original)

    def test_installer_rejects_incomplete_hash_manifest_before_replacing_edits(self):
        target = self.root / 'skill'
        install_skill.install(target)
        entry = target / 'SKILL.md'
        entry.write_text('local user edit', encoding='utf-8')
        manifest = target / 'references' / 'local.json'
        metadata = json.loads(manifest.read_text(encoding='utf-8'))
        metadata['hashes'].pop('scripts/gongxi.py')
        manifest.write_text(json.dumps(metadata), encoding='utf-8')

        with self.assertRaisesRegex(ValueError, 'incomplete'):
            install_skill.install(target)
        self.assertEqual(entry.read_text(encoding='utf-8'), 'local user edit')

    def test_backup_rejects_declared_zip_expansion_over_limit(self):
        archive_path = self.root / 'oversized.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(backup.DB_MEMBER, b'x' * 128)
            archive.writestr(backup.MANIFEST_MEMBER, '{}')

        with mock.patch.object(backup, 'MAX_DATABASE_BYTES', 64):
            with self.assertRaisesRegex(backup.BackupError, '大小上限'):
                backup.validate_archive(archive_path)


if __name__ == '__main__':
    unittest.main()
