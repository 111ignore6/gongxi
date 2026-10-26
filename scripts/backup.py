#!/usr/bin/env python3
"""Private, complete SQLite backups for a 共席 data directory."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile


DB_NAME = 'gongxi.sqlite3'
DB_MEMBER = 'database.sqlite3'
MANIFEST_MEMBER = 'manifest.json'
FORMAT = 'gongxi-sqlite-backup-v1'
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
REQUIRED_TABLES = {
    'workspace', 'members', 'invites', 'entities', 'events',
    'idempotency', 'directions', 'acknowledgements',
}
REQUIRED_COLUMNS = {
    'workspace': {'id', 'data'},
    'members': {'id', 'token_hash', 'data'},
    'invites': {'code_hash', 'kind', 'expires_at', 'used_at'},
    'entities': {'id', 'kind', 'data'},
    'events': {'id', 'type', 'entity_id', 'actor_id', 'created_at'},
    'idempotency': {'member_id', 'key', 'body', 'message_id'},
    'directions': {'revision', 'data'},
    'acknowledgements': {'member_id', 'revision', 'data'},
}
ROOT = Path(__file__).resolve().parents[1]


class BackupError(Exception):
    pass


def private_file(path):
    path = Path(path)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'])
        match = re.search(rb'S-1-[0-9-]+', identity)
        if not match:
            raise BackupError('无法读取当前 Windows 用户 SID。')
        account = '*' + match.group().decode('ascii')
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', account + ':(F)'], capture_output=True)
        if result.returncode:
            raise BackupError('无法限制备份文件 ACL。')
    else:
        os.chmod(path, 0o600)


def private_directory(path):
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'])
        match = re.search(rb'S-1-[0-9-]+', identity)
        if not match:
            raise BackupError('无法读取当前 Windows 用户 SID。')
        account = '*' + match.group().decode('ascii')
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', account + ':(OI)(CI)(F)'], capture_output=True)
        if result.returncode:
            raise BackupError('无法限制备份暂存目录 ACL。')
    else:
        os.chmod(path, 0o700)


def manifest_for(db_path):
    with closing(sqlite3.connect(db_path)) as db:
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise BackupError('SQLite integrity_check 未通过。')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not REQUIRED_TABLES.issubset(tables):
            raise BackupError('数据库 schema 不完整，无法备份或恢复。')
        columns = {}
        for name in tables:
            quoted = name.replace('"', '""')
            columns[name] = [row[1] for row in db.execute('PRAGMA table_info("' + quoted + '")')]
        for table, required in REQUIRED_COLUMNS.items():
            missing = sorted(required - set(columns[table]))
            if missing:
                raise BackupError(f'数据库 schema 缺少必需列：{table}.{", ".join(missing)}。')
        counts = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        schema = {
            'user_version': db.execute('PRAGMA user_version').fetchone()[0],
            'tables': tables,
            'columns': columns,
        }
    digest = sha256_file(db_path)
    return {
        'format': FORMAT,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'schema': schema,
        'table_counts': counts,
        'database_sha256': digest,
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archive(path, extraction_target=None):
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != 2 or set(names) != {DB_MEMBER, MANIFEST_MEMBER} or len(set(names)) != 2:
                raise BackupError('备份必须只包含数据库和 manifest 两个固定文件。')
            db_info = archive.getinfo(DB_MEMBER)
            manifest_info = archive.getinfo(MANIFEST_MEMBER)
            if db_info.file_size <= 0:
                raise BackupError('备份文件大小无效。')
            if db_info.file_size > MAX_DATABASE_BYTES:
                raise BackupError(f'备份数据库超出大小上限（{MAX_DATABASE_BYTES} 字节）。')
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise BackupError(f'备份 manifest 超出大小上限（{MAX_MANIFEST_BYTES} 字节）。')
            manifest_raw = read_member_limited(archive, MANIFEST_MEMBER, MAX_MANIFEST_BYTES)
            if len(manifest_raw) != manifest_info.file_size:
                raise BackupError('manifest 声明大小与实际解压大小不一致。')
            manifest = json.loads(manifest_raw.decode('utf-8'))
            if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
                    or not isinstance(manifest.get('created_at'), str)):
                raise BackupError('备份格式不受支持。')
            if extraction_target is None:
                with tempfile.TemporaryDirectory(prefix='gongxi-verify-') as temp:
                    private_directory(temp)
                    db_path = Path(temp) / DB_NAME
                    with archive.open(DB_MEMBER) as source, open(db_path, 'wb') as target:
                        copied = copy_member_limited(source, target, MAX_DATABASE_BYTES)
                    if copied != db_info.file_size:
                        raise BackupError('数据库声明大小与实际解压大小不一致。')
                    verify_archive_database(db_path, manifest)
            else:
                with archive.open(DB_MEMBER) as source, open(extraction_target, 'wb') as target:
                    copied = copy_member_limited(source, target, MAX_DATABASE_BYTES)
                if copied != db_info.file_size:
                    raise BackupError('数据库声明大小与实际解压大小不一致。')
                verify_archive_database(extraction_target, manifest)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, KeyError, RuntimeError) as exc:
        raise BackupError('备份文件损坏或无法读取。') from exc
    return manifest


def copy_member_limited(source, target, maximum):
    total = 0
    while True:
        chunk = source.read(min(1024 * 1024, maximum - total + 1))
        if not chunk:
            return total
        total += len(chunk)
        if total > maximum:
            raise BackupError(f'备份成员解压后超出大小上限（{maximum} 字节）。')
        target.write(chunk)


def read_member_limited(archive, name, maximum):
    parts = bytearray()
    with archive.open(name) as source:
        while True:
            chunk = source.read(min(64 * 1024, maximum - len(parts) + 1))
            if not chunk:
                return bytes(parts)
            parts.extend(chunk)
            if len(parts) > maximum:
                raise BackupError(f'备份 manifest 解压后超出大小上限（{maximum} 字节）。')


def verify_archive_database(db_path, manifest):
    if sha256_file(db_path) != manifest.get('database_sha256'):
        raise BackupError('数据库哈希与 manifest 不一致。')
    verify_manifest(db_path, manifest)


def verify_manifest(db_path, manifest):
    actual = manifest_for(db_path)
    recorded_schema = manifest.get('schema')
    if not isinstance(recorded_schema, dict):
        raise BackupError('备份 manifest 中的 schema 无效。')
    if 'columns' in recorded_schema:
        schema_matches = actual['schema'] == recorded_schema
    else:
        # Older v1 backups recorded only these two schema fields. The actual
        # database still passes manifest_for's required-table/column checks.
        schema_matches = (
            set(recorded_schema) == {'tables', 'user_version'}
            and all(actual['schema'][key] == recorded_schema[key] for key in ('tables', 'user_version'))
        )
    if not schema_matches or actual['table_counts'] != manifest.get('table_counts'):
        raise BackupError('备份 manifest 中的 schema 或表记录数不匹配。')


def create(data_dir, output):
    source = Path(data_dir).expanduser().resolve() / DB_NAME
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise BackupError('数据目录中没有 gongxi.sqlite3。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BackupError('备份输出文件已存在；请选择新路径。')
    work_dir = Path(tempfile.mkdtemp(prefix='.gongxi-staging-', dir=destination.parent))
    private_directory(work_dir)
    staged_db = work_dir / DB_NAME
    zip_fd, zip_name = tempfile.mkstemp(prefix='.gongxi-backup-', suffix='.tmp', dir=destination.parent)
    os.close(zip_fd)
    staged_zip = Path(zip_name)
    try:
        # Apply the output ACL before any database content is written.
        private_file(staged_zip)
        src = sqlite3.connect(source, timeout=30)
        dst = sqlite3.connect(staged_db)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        manifest = manifest_for(staged_db)
        with zipfile.ZipFile(staged_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(staged_db, DB_MEMBER)
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        # Validate the completed archive before publishing its final name.
        validate_archive(staged_zip)
        if destination.exists():
            raise BackupError('备份输出文件已存在；请选择新路径。')
        os.replace(staged_zip, destination)
        return {'backup_file': str(destination), 'created_at': manifest['created_at'], 'table_counts': manifest['table_counts']}
    finally:
        staged_zip.unlink(missing_ok=True)
        if not work_dir.is_symlink():
            try:
                resolved_work = work_dir.resolve()
                resolved_parent = destination.parent.resolve()
                if (resolved_work.parent == resolved_parent
                        and work_dir.name.startswith('.gongxi-staging-')):
                    shutil.rmtree(resolved_work, ignore_errors=True)
            except OSError:
                pass


def verify(path):
    manifest = validate_archive(Path(path).expanduser())
    return {'valid': True, 'created_at': manifest['created_at'], 'schema': manifest['schema'], 'table_counts': manifest['table_counts']}


def restore(path, data_dir):
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise BackupError('恢复目标必须是空目录或尚不存在的目录。')
    manifest = validate_archive(Path(path).expanduser())
    destination.mkdir(parents=True, exist_ok=True)
    db_path = destination / DB_NAME
    if db_path.exists():
        raise BackupError('恢复目标已有数据库，拒绝覆盖。')
    fd, temp_name = tempfile.mkstemp(prefix='.gongxi-restore-', dir=destination)
    os.close(fd)
    staged = Path(temp_name)
    try:
        # The extracted database contains the complete collaboration history.
        # Apply a restrictive ACL before the first archive byte is written.
        private_file(staged)
        validate_archive(Path(path).expanduser(), staged)
        if db_path.exists():
            raise BackupError('恢复目标已有数据库，拒绝覆盖。')
        # Windows rename is atomic and refuses an existing destination;
        # POSIX rename may overwrite, so use a same-directory hard link there.
        # Both paths fail closed if a file appears after the existence check.
        if os.name == 'nt':
            os.rename(staged, db_path)
        else:
            os.link(staged, db_path)
            staged.unlink()
    finally:
        staged.unlink(missing_ok=True)
    return {'restored_database': str(db_path), 'created_at': manifest['created_at'], 'table_counts': manifest['table_counts']}


def parser():
    root = argparse.ArgumentParser(description='共席完整 SQLite 数据库备份；包含身份 hash 和全部历史，不包含明文凭证。')
    commands = root.add_subparsers(dest='command', required=True)
    create_parser = commands.add_parser('create', help='使用 SQLite backup API 在线创建私有完整备份')
    create_parser.add_argument('--data-dir', default=str(ROOT / 'data'))
    create_parser.add_argument('--output', required=True)
    verify_parser = commands.add_parser('verify', help='校验备份哈希、schema、表记录数和 SQLite integrity_check')
    verify_parser.add_argument('backup')
    restore_parser = commands.add_parser('restore', help='恢复到明确的空目录')
    restore_parser.add_argument('backup')
    restore_parser.add_argument('--data-dir', required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'create':
        result = create(args.data_dir, args.output)
    elif args.command == 'verify':
        result = verify(args.backup)
    else:
        result = restore(args.backup, args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BackupError, OSError, sqlite3.Error, ValueError) as error:
        print('错误：' + str(error), file=__import__('sys').stderr)
        raise SystemExit(1)
