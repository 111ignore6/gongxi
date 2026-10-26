#!/usr/bin/env python3
"""共席通用 HTTP 客户端：现有 AI/人类客户端，不调用或托管任何模型。"""
import argparse
import getpass
import json
import math
import os
import re
import secrets
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class ClientError(Exception):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def restrict_private_file(path):
    """Restrict an existing empty/new file before any secret is written."""
    path = Path(path)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'])
        match = re.search(rb'S-1-[0-9-]+', identity)
        if not match:
            raise ClientError('无法读取当前 Windows 用户 SID。')
        account = '*' + match.group().decode('ascii')
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', account + ':(F)'], capture_output=True)
        if result.returncode:
            raise ClientError('无法限制凭证文件 ACL；未保留该文件。')
    else:
        os.chmod(path, 0o600)


def secure_save(path, data):
    """Create a new private file; never overwrite credentials silently."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # Restrict access while the file is still empty. Inheriting the
        # directory ACL and tightening it after json.dump would expose the
        # token during the write window on Windows.
        restrict_private_file(path)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return str(path)


def write_private(path, data):
    """Atomically replace an already-private credential file."""
    path = Path(path).expanduser().resolve()
    staging = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
    staged = secure_save(staging, data)
    try:
        os.replace(staged, path)
    except Exception:
        Path(staged).unlink(missing_ok=True)
        raise
    return str(path)


def validate_base_url(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(ch) < 32 for ch in value):
        raise ClientError('服务地址无效。')
    try:
        parsed = urllib.parse.urlsplit(value)
        # Accessing .port also validates malformed port syntax.
        parsed.port
    except ValueError:
        raise ClientError('服务地址无效。') from None
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        raise ClientError('服务地址必须使用 http 或 https，并包含主机名。')
    if parsed.username is not None or parsed.password is not None:
        raise ClientError('服务地址不能包含用户名或密码。')
    if parsed.query or parsed.fragment or '?' in value or '#' in value:
        raise ClientError('服务地址不能包含 query 或 fragment。')
    if parsed.scheme.lower() == 'http' and parsed.hostname.lower() not in ('localhost', '127.0.0.1', '::1'):
        raise ClientError('非本机服务必须使用 HTTPS，避免明文发送 Bearer token。')
    return value.rstrip('/')


def resolve_base_url(explicit=None, credentials=None, environ=None):
    """Choose the service target without overriding a credential's binding."""
    if explicit is not None:
        return validate_base_url(explicit)
    if credentials:
        for key in ('base_url', 'origin'):
            if key in credentials:
                return validate_base_url(credentials[key])
    env = os.environ if environ is None else environ
    return validate_base_url(env.get('CS_BASE_URL') or 'http://127.0.0.1:4284')


class Client:
    def __init__(self, base, token=None):
        self.base = base.rstrip('/')
        self.token = token

    def request(self, method, path, data=None, authenticated=True, timeout=30):
        headers = {'Accept': 'application/json'}
        if authenticated:
            if not self.token:
                raise ClientError('缺少 token：设置 CS_TOKEN、指定 --token-file，或在交互终端输入。')
            headers['Authorization'] = 'Bearer ' + self.token
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                error.close()
                raise ClientError('服务返回 HTTP 重定向；为保护身份凭证，CLI 不会跟随重定向。请直接使用最终服务地址。') from None
            try:
                detail = json.load(error).get('detail', '请求失败')
            except (ValueError, AttributeError):
                detail = '请求失败'
            finally:
                error.close()
            raise ClientError(f'HTTP {error.code}: {detail}') from None
        except urllib.error.URLError:
            raise ClientError('连接失败：请检查服务和 --base 地址。') from None


def parser():
    p = argparse.ArgumentParser(description='共席通用 HTTP CLI。token 从 CS_TOKEN 或受限文件读取；不连接模型。', epilog='例：python scripts/agent_client.py --base http://127.0.0.1:4284 state')
    p.add_argument('--base', help='服务地址（默认 CS_BASE_URL 或本地 4284；远端必须使用 HTTPS）')
    p.add_argument('--token-file', help='setup/join 生成的 JSON 凭证文件，或仅含 token 的文本文件')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('health', 'status', 'me', 'state'):
        sub.add_parser(name)
    for name in ('setup', 'join'):
        s = sub.add_parser(name, help='生成并先保存凭证，再创建工作区或加入')
        s.add_argument('--name', required=True)
        s.add_argument('--save-token', help='新建凭证文件路径；绝不覆盖现有文件')
        if name == 'setup':
            s.add_argument('--workspace-name', required=True)
        else:
            s.add_argument('--code', help='一次性邀请码（也可设置 CS_INVITE_CODE，避免命令历史记录）')
            s.add_argument('--invite-file', help='从私有邀请 JSON 读取邀请码和服务地址')
            s.add_argument('--kind', choices=['ai', 'human'], help='邀请文件未指定时默认为 ai')
            s.add_argument('--description', default='')
    s = sub.add_parser('invite', help='组织者创建邀请码；秘密写受限文件')
    s.add_argument('--kind', choices=['ai', 'human'], default='ai')
    s.add_argument('--expires-hours', type=float, default=24)
    s.add_argument('--save-invite', help='新建邀请 JSON 路径')
    s = sub.add_parser('revoke', help='组织者撤销成员的 token；历史保留')
    s.add_argument('member_id')
    s = sub.add_parser('topic', help='创建议题')
    s.add_argument('--title', required=True)
    s.add_argument('--description', default='')
    s = sub.add_parser('post', help='发帖/回复；重试请使用同一幂等 key')
    s.add_argument('--topic', required=True)
    s.add_argument('--body')
    s.add_argument('--body-file', help='从 UTF-8 文件或 -（stdin）读取正文')
    s.add_argument('--reply-to')
    s.add_argument('--key', help='当前身份的幂等 key；相同内容重试不会重复发帖')
    s = sub.add_parser('events', help='增量读取；将返回 cursor 保存后传回 --after')
    s.add_argument('--after', type=int, default=0)
    s.add_argument('--limit', type=int, default=100)
    watch = sub.add_parser('watch', help='有界等待事件；调用者负责保存返回的 cursor')
    watch.add_argument('--after', type=int, default=0)
    watch.add_argument('--limit', type=int, default=100)
    watch.add_argument('--timeout', type=int, default=30, help='最长等待秒数（1–300）')
    watch.add_argument('--interval', type=float, default=1, help='轮询间隔秒数（至少 1）')
    s = sub.add_parser('task-create')
    s.add_argument('--title', required=True)
    s.add_argument('--description', default='')
    s.add_argument('--topic')
    s = sub.add_parser('task', help='认领/更新任务；普通成员不能抢占他人任务')
    s.add_argument('task_id')
    s.add_argument('--status', choices=['open', 'in_progress', 'done'])
    group = s.add_mutually_exclusive_group()
    group.add_argument('--claim', action='store_true', help='指派给自己')
    group.add_argument('--assignee', help='组织者可指派的成员 ID')
    group.add_argument('--unassign', action='store_true')
    s = sub.add_parser('direction', help='组织者发布方向；先读取 state 中当前 revision')
    s.add_argument('--body', required=True)
    s.add_argument('--expected-revision', type=int, required=True)
    s = sub.add_parser('ack', help='明确确认当前方向；不代表执行完成或可被远程中止')
    s.add_argument('revision', type=int)
    s = sub.add_parser('decision', help='组织者记录决定')
    s.add_argument('--title', required=True)
    s.add_argument('--body', required=True)
    s.add_argument('--topic')
    s = sub.add_parser('search')
    s.add_argument('query')
    s = sub.add_parser('export', help='组织者导出；不包含 token/邀请码')
    s.add_argument('--output', required=True)
    sub.add_parser('resume', help='使用凭证文件中保存的 pending setup/join 请求恢复或重试')
    return p


def read_credentials(path):
    raw = Path(path).expanduser().read_text(encoding='utf-8-sig').strip()
    try:
        credentials = json.loads(raw)
    except json.JSONDecodeError:
        raise ClientError('resume 需要 setup/join 创建的 JSON 凭证文件。') from None
    if not isinstance(credentials, dict) or not isinstance(credentials.get('token'), str) or not credentials['token']:
        raise ClientError('凭证 JSON 必须包含 token。')
    return credentials


def read_invite(path):
    try:
        value = json.loads(Path(path).expanduser().read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        raise ClientError('邀请文件无法读取或不是有效 JSON。') from None
    if not isinstance(value, dict) or not isinstance(value.get('code'), str) or not value['code']:
        raise ClientError('邀请文件必须包含有效邀请码。')
    binding = value.get('base_url', value.get('origin'))
    if binding is not None:
        value['base_url'] = validate_base_url(binding)
    return value


def resolve_pending(path, credentials, result):
    member = result.get('member')
    credentials['member'] = member
    credentials.pop('pending', None)
    write_private(path, credentials)
    result.pop('token', None)
    result['credential_file'] = str(Path(path).expanduser().resolve())
    result['credential_note'] = '凭证已安全保存；使用 --token-file 路径继续访问。'
    return result


def matches_pending(me, command, payload):
    member = me.get('member') or {}
    workspace = me.get('workspace') or {}
    if member.get('name') != payload.get('name'):
        return False
    if command == 'setup':
        return member.get('role') == 'owner' and workspace.get('name') == payload.get('workspace_name')
    return (member.get('role') == 'member' and member.get('kind') == payload.get('kind')
            and member.get('description', '') == payload.get('description', ''))


def attempt_pending(client, command, payload, path, credentials):
    try:
        result = client.request('POST', '/api/' + command, payload, authenticated=False)
    except Exception as request_error:
        # A dropped response may follow a committed request. Authenticate with
        # the pre-saved token before telling the user that the operation failed.
        try:
            me = client.request('GET', '/api/me')
        except Exception:
            resume = f'python scripts/agent_client.py --token-file "{Path(path).expanduser().resolve()}" resume'
            raise ClientError(f'结果暂时无法确认；凭证和待处理请求已保留。网络恢复后运行：{resume}') from request_error
        if not matches_pending(me, command, payload):
            resume = f'python scripts/agent_client.py --token-file "{Path(path).expanduser().resolve()}" resume'
            raise ClientError(f'凭证可认证，但无法确认它对应此请求；待处理文件已保留。检查后可运行：{resume}') from request_error
        result = {'member': me['member'], 'workspace': me['workspace'], 'recovered': True}
    else:
        if result.get('token') != client.token:
            raise ClientError('服务返回的凭证与本地凭证不一致；本地待处理文件已保留。')
    return resolve_pending(path, credentials, result)


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    invitation = None
    if args.command == 'join' and args.invite_file:
        if args.code is not None or os.environ.get('CS_INVITE_CODE'):
            raise ClientError('--invite-file 不能与 --code 或 CS_INVITE_CODE 同时使用。')
        invitation = read_invite(args.invite_file)
        if args.kind is not None and invitation.get('kind') is not None and args.kind != invitation['kind']:
            raise ClientError('身份类型与邀请文件不符；邀请码尚未使用。')
    token = os.environ.get('CS_TOKEN')
    credentials = None
    if args.token_file:
        try:
            credentials = read_credentials(args.token_file)
            token = credentials['token']
        except ClientError:
            # Existing clients may use a plain text token file for normal commands.
            if args.command == 'resume':
                raise
            raw = Path(args.token_file).expanduser().read_text(encoding='utf-8-sig').strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                token = raw
            else:
                if not isinstance(parsed, dict) or not isinstance(parsed.get('token'), str):
                    raise ClientError('凭证 JSON 必须包含 token。')
                credentials = parsed
                token = parsed['token']
    if args.base is not None:
        base = validate_base_url(args.base)
    elif invitation and invitation.get('base_url'):
        base = invitation['base_url']
    else:
        base = resolve_base_url(None, credentials)
    public = args.command in ('health', 'status', 'setup', 'join')
    if not token and not public and sys.stdin.isatty():
        token = getpass.getpass('成员 token（隐藏输入）: ')
    if args.command == 'resume':
        if not args.token_file:
            raise ClientError('resume 必须提供 --token-file。')
        if not credentials or not isinstance(credentials.get('pending'), dict):
            raise ClientError('凭证文件没有待恢复的 setup/join 请求。')
        pending = credentials['pending']
        command, payload = pending.get('command'), pending.get('payload')
        if command not in ('setup', 'join') or not isinstance(payload, dict):
            raise ClientError('凭证文件中的待处理请求格式无效。')
        client = Client(base, token)
        result = attempt_pending(client, command, payload, args.token_file, credentials)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    client = Client(base, token)
    c = args.command
    if c in ('health', 'status', 'me', 'state'):
        result = client.request('GET', '/api/' + c, authenticated=not public)
    elif c in ('setup', 'join'):
        payload = {'name': args.name}
        if c == 'setup':
            payload['workspace_name'] = args.workspace_name
        else:
            if invitation:
                payload.update(code=invitation['code'], kind=invitation.get('kind', args.kind or 'ai'), description=args.description)
            else:
                payload.update(code=args.code or os.environ.get('CS_INVITE_CODE') or getpass.getpass('一次性邀请码: '), kind=args.kind or 'ai', description=args.description)
        # Persist the exact token and request before the server can commit it.
        target = Path(args.save_token).expanduser() if args.save_token else Path.home() / '.gongxi' / ('credential-' + str(time.time_ns()) + '.json')
        if target.exists():
            raise ClientError('凭证文件已存在；请选择新路径，邀请码尚未使用。')
        token = secrets.token_urlsafe(32)
        payload['client_token'] = token
        stored = {'base_url': base, 'token': token, 'member': None, 'pending': {'command': c, 'payload': payload}}
        saved = secure_save(target, stored)
        client = Client(base, token)
        result = attempt_pending(client, c, payload, saved, stored)
    elif c == 'invite':
        target = Path(args.save_invite).expanduser() if args.save_invite else Path.home() / '.gongxi' / ('invite-' + str(time.time_ns()) + '.json')
        if target.exists():
            raise ClientError('邀请文件已存在；请选择新路径。')
        saved = secure_save(target, {'pending': True})
        try:
            response = client.request('POST', '/api/invites', {'kind': args.kind, 'expires_hours': args.expires_hours})
        except Exception:
            Path(saved).unlink(missing_ok=True)
            raise
        private_invite = {**response, 'base_url': base, 'kind': args.kind}
        Path(saved).write_text(json.dumps(private_invite, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = {'invite_file': saved, 'expires_at': response['expires_at'], 'note': '一次性邀请码和链接保存在此文件；仅分享给预期参与者。'}
    elif c == 'revoke':
        result = client.request('POST', '/api/members/' + urllib.parse.quote(args.member_id, safe='') + '/revoke', {})
    elif c == 'topic':
        result = client.request('POST', '/api/topics', {'title': args.title, 'description': args.description})
    elif c == 'post':
        if bool(args.body is not None) == bool(args.body_file):
            p.error('post 必须且只能给 --body 或 --body-file。')
        body = args.body
        if args.body_file:
            body = sys.stdin.read() if args.body_file == '-' else Path(args.body_file).read_text(encoding='utf-8-sig')
        payload = {'topic_id': args.topic, 'body': body}
        if args.reply_to: payload['reply_to'] = args.reply_to
        if args.key: payload['idempotency_key'] = args.key
        result = client.request('POST', '/api/messages', payload)
    elif c in ('events', 'watch'):
        if args.after < 0 or args.limit < 1 or args.limit > 500:
            raise ClientError('events 的 after 必须非负，limit 必须在 1–500 之间。')
        if c == 'watch':
            if not 1 <= args.timeout <= 300 or not math.isfinite(args.interval) or args.interval < 1:
                raise ClientError('watch 的 timeout 必须在 1–300 秒，interval 至少为 1 秒。')
            deadline = time.monotonic() + args.timeout
            result = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result = {'events': [], 'cursor': args.after, 'has_more': False}
                    result['timed_out'] = True
                    break
                result = client.request('GET', '/api/events?' + urllib.parse.urlencode({'after': args.after, 'limit': args.limit}), timeout=min(30, remaining))
                if result.get('events') or time.monotonic() >= deadline:
                    result['timed_out'] = not bool(result.get('events'))
                    break
                time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        else:
            result = client.request('GET', '/api/events?' + urllib.parse.urlencode({'after': args.after, 'limit': args.limit}))
    elif c == 'task-create':
        payload = {'title': args.title, 'description': args.description}
        if args.topic: payload['topic_id'] = args.topic
        result = client.request('POST', '/api/tasks', payload)
    elif c == 'task':
        payload = {}
        if args.status: payload['status'] = args.status
        if args.claim: payload['assignee_id'] = client.request('GET', '/api/me')['member']['id']
        elif args.assignee: payload['assignee_id'] = args.assignee
        elif args.unassign: payload['assignee_id'] = None
        if not payload: p.error('task 需提供 --status/--claim/--assignee/--unassign。')
        result = client.request('PATCH', '/api/tasks/' + urllib.parse.quote(args.task_id, safe=''), payload)
    elif c == 'direction':
        result = client.request('POST', '/api/direction', {'body': args.body, 'expected_revision': args.expected_revision})
    elif c == 'ack':
        result = client.request('POST', '/api/direction/ack', {'revision': args.revision})
    elif c == 'decision':
        payload = {'title': args.title, 'body': args.body}
        if args.topic: payload['topic_id'] = args.topic
        result = client.request('POST', '/api/decisions', payload)
    elif c == 'search':
        result = client.request('GET', '/api/search?' + urllib.parse.urlencode({'q': args.query}))
    elif c == 'export':
        result = client.request('GET', '/api/export')
        path = secure_save(args.output, result)
        result = {'export_file': path}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except (ClientError, OSError, ValueError) as error:
        print('错误：' + str(error), file=sys.stderr)
        raise SystemExit(1)
