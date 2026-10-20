#!/usr/bin/env python3

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import hashlib
import hmac
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


class Snapshot(TypedDict):
    sampledAt: int
    cpuUtilization: float | None
    cpuCount: int
    availableMemoryBytes: int
    totalMemoryBytes: int


class Host(TypedDict):
    environmentId: str
    label: str
    status: str
    resources: Snapshot | None


class Report(TypedDict):
    hosts: list[Host]
    errors: list[dict[str, str]]
    complete: bool


class HostError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def request_json(url: str, timeout: float, payload=None, headers=None, form=False):
    supplied = {'User-Agent': 'astack-t3-hosts/1.0', **(headers or {})}
    body = None
    if payload is not None:
        body = urlencode(payload).encode() if form else json.dumps(payload).encode()
        supplied['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    request = Request(url, data=body, headers=supplied)
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        code = 'unauthorized' if error.code in (401, 403) else 'unreachable'
        raise HostError(code, f'T3 returned HTTP {error.code}.') from None
    except (OSError, URLError, ValueError, HTTPException):
        raise HostError('unreachable', 'Host did not answer.') from None
    try:
        if len(raw) > 1024 * 1024:
            raise ValueError()
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise HostError('invalid-response', 'T3 returned invalid JSON.') from None


def resource_rpc(origin: str, ticket: str, timeout: float, protocol: int):
    address = urlsplit(origin)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    request_id = str(uuid.uuid4())
    deadline = time.monotonic() + timeout
    connection = None
    reader = None
    try:
        connection = socket.create_connection((address.hostname, address.port or
                                               (443 if address.scheme == 'https' else 80)), timeout)
        if address.scheme == 'https':
            connection = ssl.create_default_context().wrap_socket(connection, server_hostname=address.hostname)
        reader = connection.makefile('rb')
        path = '/ws?' + urlencode({'wsTicket': ticket, 'orchestrationProtocol': protocol})
        handshake = (f'GET {path} HTTP/1.1\r\nHost: {address.netloc}\r\n'
                     'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                     f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        connection.sendall(handshake.encode('ascii'))
        status = reader.readline(4096)
        headers = {}
        for _ in range(100):
            line = reader.readline(4096)
            if line == b'\r\n':
                break
            if not line or b':' not in line:
                raise HostError('invalid-response', 'Invalid WebSocket handshake.')
            name, value = line.decode('ascii').split(':', 1)
            headers[name.lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if (len(status.split()) < 2 or status.split()[1] != b'101'
                or not hmac.compare_digest(headers.get('sec-websocket-accept', ''), expected)):
            raise HostError('unsupported', 'Host refused the authenticated WebSocket.')

        def send(payload: bytes, opcode=1):
            mask = secrets.token_bytes(4)
            length = len(payload)
            frame = bytes([0x80 | opcode, 0x80 | length]) if length < 126 else (
                bytes([0x80 | opcode, 0x80 | 126]) + struct.pack('!H', length))
            connection.sendall(frame + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

        def read(length):
            if time.monotonic() >= deadline:
                raise HostError('unreachable', 'Host resource query timed out.')
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            data = reader.read(length)
            if len(data) != length:
                raise HostError('unreachable', 'Host closed its resource connection.')
            return data

        send(json.dumps({'_tag': 'Request', 'id': request_id, 'tag': 'server.getHostResources',
                         'payload': {}, 'headers': []}).encode())
        fragments = bytearray()
        while True:
            first, second = read(2)
            opcode, length = first & 15, second & 127
            if length == 126:
                length = struct.unpack('!H', read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', read(8))[0]
            if length > 1024 * 1024 or len(fragments) + length > 1024 * 1024 or second & 128:
                raise HostError('invalid-response', 'Invalid WebSocket resource frame.')
            data = read(length)
            if opcode == 8:
                raise HostError('unreachable', 'Host closed its resource connection.')
            if opcode == 9:
                send(data, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise HostError('invalid-response', 'Unexpected WebSocket frame.')
            fragments.extend(data)
            if not first & 128:
                continue
            messages = json.loads(fragments.decode())
            fragments.clear()
            for frame in messages if isinstance(messages, list) else [messages]:
                if not isinstance(frame, dict):
                    raise HostError('invalid-response', 'Invalid resource RPC response.')
                if frame.get('_tag') == 'Ping':
                    send(b'{"_tag":"Pong"}')
                if frame.get('_tag') in ('Defect', 'ClientProtocolError'):
                    raise HostError('unsupported', 'Host does not support this resource query.')
                if frame.get('_tag') == 'Exit' and frame.get('requestId') == request_id:
                    exit_value = frame.get('exit', {})
                    if not isinstance(exit_value, dict) or exit_value.get('_tag') != 'Success':
                        raise HostError('unsupported', 'Host refused the read-only resource query.')
                    return exit_value.get('value')
    except (OSError, ValueError, UnicodeError):
        raise HostError('unreachable', 'Host resource connection failed.') from None
    finally:
        if reader:
            reader.close()
        if connection:
            connection.close()


def run_process(arguments, *, home: Path, timeout: float, payload=None, environment=None):
    try:
        result = subprocess.run(arguments, input=payload, capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, 'T3CODE_HOME': str(home),
                                                      **(environment or {})})
        if result.returncode:
            raise HostError('authentication', 'T3 authentication command failed.')
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise HostError('authentication', 'T3 authentication command failed or timed out.') from None


CRYPTO_PROGRAM = r'''
const fs = require('node:fs');
const c = require('node:crypto');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
let output;
if (p.action === 'decrypt') {
    const encrypted = Buffer.from(p.value, 'base64');
    if (encrypted.subarray(0, 3).toString() !== 'v10') throw Error('Unsupported storage');
    const key = c.pbkdf2Sync(p.password, 'saltysalt', 1003, 16, 'sha1');
    const decipher = c.createDecipheriv('aes-128-cbc', key, Buffer.alloc(16, 32));
    output = {value: Buffer.concat([decipher.update(encrypted.subarray(3)), decipher.final()]).toString('utf8')};
    key.fill(0);
} else if (p.action === 'generate') {
    const pair = c.generateKeyPairSync('ec', {namedCurve: 'P-256'});
    const jwk = pair.publicKey.export({format: 'jwk'});
    const canonical = JSON.stringify({crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y});
    output = {pem: pair.privateKey.export({type: 'pkcs8', format: 'pem'}), jwk,
              thumbprint: c.createHash('sha256').update(canonical).digest('base64url')};
} else if (p.action === 'sign') {
    output = {signature: c.sign('sha256', Buffer.from(p.value),
              {key: p.pem, dsaEncoding: 'ieee-p1363'}).toString('base64url')};
} else throw Error('Unknown crypto operation');
process.stdout.write(JSON.stringify(output));
'''


class Crypto:
    def __init__(self, home: Path, timeout: float):
        self.home, self.timeout = home, timeout
        self.environment = {}
        node = shutil.which('node')
        if node:
            self.command = node
            return
        if sys.platform == 'darwin':
            for root in (Path('/Applications'), Path.home() / 'Applications'):
                for name in ('T3 Code (Nightly)', 'T3 Code'):
                    candidate = root / (name + '.app') / 'Contents/MacOS' / name
                    if candidate.is_file():
                        self.command = str(candidate)
                        self.environment = {'ELECTRON_RUN_AS_NODE': '1'}
                        return
        raise HostError('unsupported', 'T3 Connect authentication needs Node.js or the macOS T3 desktop app.')

    def call(self, action, **values):
        raw = run_process([self.command, '-e', CRYPTO_PROGRAM], home=self.home,
                          timeout=max(5, self.timeout), environment=self.environment,
                          payload=json.dumps({'action': action, **values}))
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise HostError('authentication', 'T3 authentication crypto failed.') from None


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


class T3Client:
    def __init__(self, home: Path, timeout: float):
        self.home, self.timeout = home, timeout

    def describe(self, origin):
        return request_json(origin + '/.well-known/t3/environment', self.timeout)

    def local_cli(self, version):
        candidates = [self.home / 'runtime/versions' / version / 't3', self.home / 'bin/t3']
        found = shutil.which('t3')
        if found:
            candidates.append(Path(found))
        for candidate in candidates:
            if candidate.is_file():
                try:
                    actual = run_process([str(candidate), '--version'], home=self.home, timeout=self.timeout).strip()
                    if actual.removeprefix('t3 ').removeprefix('v') == version:
                        return str(candidate)
                except HostError:
                    continue
        raise HostError('unsupported', 'No matching T3 CLI was found. Open or update T3 Code.')

    def read_resources(self, origin, environment_id, local=False):
        descriptor = self.describe(origin)
        if not isinstance(descriptor, dict) or descriptor.get('environmentId') != environment_id:
            raise HostError('invalid-response', 'Host identity differs from the T3 Connect record.')
        protocol = descriptor.get('orchestrationProtocolVersion', 1)
        if type(protocol) is not int or protocol not in (1, 2):
            raise HostError('unsupported', 'Host uses an unsupported T3 WebSocket protocol.')
        if not local:
            return self.remote_resources(origin, environment_id, protocol)
        version = descriptor.get('serverVersion', '')
        if not isinstance(version, str) or not version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-' for c in version):
            raise HostError('unsupported', 'Host did not return a usable T3 version.')
        cli = self.local_cli(version)
        raw = run_process([cli, 'auth', 'session', 'issue', '--scope', 'orchestration:read',
                           '--ttl', '1m', '--label', 'astack t3-hosts', '--json'],
                          home=self.home, timeout=self.timeout)
        try:
            session = json.loads(raw)
            token, session_id = session['token'], session['sessionId']
            if not isinstance(token, str) or not isinstance(session_id, str) or not token or not session_id:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HostError('authentication', 'T3 returned an invalid read session.') from None
        try:
            ticket = request_json(origin + '/api/auth/websocket-ticket', self.timeout, {},
                                  {'Authorization': 'Bearer ' + token})
            return self.query_ticket(origin, ticket, protocol)
        finally:
            run_process([cli, 'auth', 'session', 'revoke', session_id], home=self.home, timeout=self.timeout)

    def query_ticket(self, origin, response, protocol):
        if not isinstance(response, dict) or not isinstance(response.get('ticket'), str) or not response['ticket']:
            raise HostError('invalid-response', 'T3 did not return a WebSocket ticket.')
        return resource_rpc(origin, response['ticket'], self.timeout, protocol)

    def saved_client_token(self):
        try:
            stored = json.loads((self.home / 'userdata/clerk-tokens.json').read_text())['__clerk_client_jwt']
            if not isinstance(stored, str):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise HostError('signed-out', 'Sign in to T3 Connect in the running desktop app to discover other hosts.') from None
        if stored.startswith('raw:') and len(stored) > 4:
            return stored[4:]
        if not stored.startswith('enc:'):
            raise HostError('unsupported', 'The saved T3 Connect sign-in format is unsupported.')
        if sys.platform != 'darwin':
            raise HostError('unsupported', 'Encrypted T3 desktop sign-in is currently supported only on macOS.')
        crypto = Crypto(self.home, self.timeout)
        for service in ('t3code Safe Storage', 'T3 Code (Nightly) Safe Storage', 'T3 Code Safe Storage'):
            try:
                password = run_process(['/usr/bin/security', 'find-generic-password', '-s', service, '-w'],
                                       home=self.home, timeout=30).rstrip('\r\n')
                value = crypto.call('decrypt', value=stored[4:], password=password).get('value')
                if isinstance(value, str) and value:
                    return value
            except HostError:
                continue
        raise HostError('keychain', 'Cannot unlock the saved T3 sign-in. Authorize T3 Safe Storage access from your own Terminal, then retry. See the t3-hosts Keychain recovery instructions.')

    def linked_environments(self):
        token = self.saved_client_token()
        headers = {'Authorization': 'Bearer ' + token}
        client = request_json('https://clerk.t3.codes/v1/client?_is_native=true', self.timeout, headers=headers)
        try:
            client = client['response']
            sessions = [s for s in client['sessions'] if isinstance(s, dict) and s.get('status') == 'active']
            active = next((s for s in sessions if s.get('id') == client.get('last_active_session_id')),
                          sessions[0] if len(sessions) == 1 else None)
            if active is None or not isinstance(active.get('id'), str):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise HostError('signed-out', 'Choose an active T3 Connect account in the desktop app.') from None
        url = 'https://clerk.t3.codes/v1/client/sessions/' + quote(active['id'], safe='') + '/tokens/t3-relay?_is_native=true'
        response = request_json(url, self.timeout, {}, headers)
        if not isinstance(response, dict) or not isinstance(response.get('jwt'), str) or not response['jwt']:
            raise HostError('authentication', 'T3 sign-in did not return a relay credential.')
        self.relay_token = response['jwt']
        response = request_json('https://relay.t3.codes/v1/environments', self.timeout,
                                headers={'Authorization': 'Bearer ' + self.relay_token})
        if not isinstance(response, dict) or not isinstance(response.get('environments'), list):
            raise HostError('invalid-response', 'T3 Connect did not return a host list.')
        self.crypto = Crypto(self.home, self.timeout)
        self.proof_key = self.crypto.call('generate')
        return response['environments']

    def proof(self, method, url, access_token=None):
        header = {'typ': 'dpop+jwt', 'alg': 'ES256', 'jwk': self.proof_key['jwk']}
        claims = {'jti': str(uuid.uuid4()), 'htm': method, 'htu': url.split('?', 1)[0], 'iat': int(time.time())}
        if access_token:
            claims['ath'] = base64url(hashlib.sha256(access_token.encode()).digest())
        content = '.'.join(base64url(json.dumps(v, separators=(',', ':')).encode()) for v in (header, claims))
        signature = self.crypto.call('sign', value=content, pem=self.proof_key['pem'])['signature']
        return content + '.' + signature

    def remote_resources(self, origin, environment_id, protocol):
        relay = 'https://relay.t3.codes'
        exchange = {'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
                    'subject_token': self.relay_token, 'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt',
                    'requested_token_type': 'urn:ietf:params:oauth:token-type:access_token',
                    'resource': relay, 'scope': 'environment:connect', 'client_id': 't3-web'}
        url = relay + '/v1/client/dpop-token'
        response = request_json(url, self.timeout, exchange, {'DPoP': self.proof('POST', url)}, form=True)
        relay_access = self.access_token(response)
        url = relay + '/v1/environments/' + quote(environment_id, safe='') + '/connect'
        response = request_json(url, self.timeout, {'clientProofKeyThumbprint': self.proof_key['thumbprint']},
                                {'Authorization': 'DPoP ' + relay_access,
                                 'DPoP': self.proof('POST', url, relay_access)})
        if (not isinstance(response, dict) or response.get('environmentId') != environment_id
                or not isinstance(response.get('endpoint'), dict)
                or origin_url(response['endpoint'].get('httpBaseUrl')) != origin
                or not isinstance(response.get('credential'), str)):
            raise HostError('invalid-response', 'T3 Connect returned a different host or endpoint.')
        url = origin + '/oauth/token'
        exchange = {'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
                    'subject_token': response['credential'],
                    'subject_token_type': 'urn:t3:params:oauth:token-type:environment-bootstrap',
                    'requested_token_type': 'urn:ietf:params:oauth:token-type:access_token',
                    'scope': 'orchestration:read', 'client_label': 'astack t3-hosts', 'client_device_type': 'bot'}
        response = request_json(url, self.timeout, exchange, {'DPoP': self.proof('POST', url)}, form=True)
        access = self.access_token(response)
        url = origin + '/api/auth/websocket-ticket'
        ticket = request_json(url, self.timeout, {}, {'Authorization': 'DPoP ' + access,
                                                     'DPoP': self.proof('POST', url, access)})
        return self.query_ticket(origin, ticket, protocol)

    @staticmethod
    def access_token(response):
        if (not isinstance(response, dict) or response.get('token_type') != 'DPoP'
                or not isinstance(response.get('access_token'), str) or not response['access_token']):
            raise HostError('authentication', 'T3 did not grant a host access token.')
        return response['access_token']


def origin_url(value: object, local: bool = False) -> str:
    if not isinstance(value, str):
        raise HostError('invalid-response', 'T3 returned an invalid host address.')
    try:
        url = urlsplit(value)
        valid_port = url.port is None or 0 < url.port < 65536
    except ValueError:
        raise HostError('invalid-response', 'T3 returned an invalid host address.') from None
    loopback = url.hostname in ('localhost', '127.0.0.1', '::1')
    if (not valid_port or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or url.scheme not in ('http', 'https')
            or (local and not loopback) or (url.scheme == 'http' and not loopback)):
        raise HostError('invalid-response', 'T3 host addresses must use HTTPS or local loopback.')
    return value.rstrip('/')


def validate_resources(value: object) -> Snapshot:
    if not isinstance(value, dict):
        raise HostError('unsupported', 'Host did not return a resource snapshot.')
    numbers = ('sampledAt', 'cpuCount', 'availableMemoryBytes', 'totalMemoryBytes')
    if any(type(value.get(k)) is not int or value[k] < 0 for k in numbers):
        raise HostError('invalid-response', 'Host returned invalid resource measurements.')
    cpu = value.get('cpuUtilization')
    if ('cpuUtilization' not in value or (cpu is not None and
            (type(cpu) not in (int, float) or not math.isfinite(cpu) or not 0 <= cpu <= 1))
            or value['availableMemoryBytes'] > value['totalMemoryBytes']):
        raise HostError('invalid-response', 'Host returned invalid resource measurements.')
    return {key: value[key] for key in (*numbers, 'cpuUtilization')}


def collect_report(home: Path, timeout: float = 5, local_only: bool = False,
                   gateway=None) -> Report | None:
    try:
        runtime = json.loads((home / 'userdata/server-runtime.json').read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise HostError('configuration', 'Cannot read T3 runtime metadata.') from None
    if not isinstance(runtime, dict):
        raise HostError('configuration', 'T3 runtime metadata is invalid.')
    origin = origin_url(runtime.get('origin'), local=True)
    gateway = gateway or T3Client(home, timeout)
    try:
        descriptor = gateway.describe(origin)
    except HostError as error:
        if error.code == 'unreachable':
            return None
        raise
    if (not isinstance(descriptor, dict) or not isinstance(descriptor.get('environmentId'), str)
            or not descriptor['environmentId']):
        raise HostError('invalid-response', 'The local server did not identify itself as T3.')
    local_id = descriptor['environmentId']
    label = descriptor.get('label') or 'Local host'
    if not isinstance(label, str):
        label = 'Local host'
    report: Report = {'hosts': [], 'errors': [], 'complete': True}

    def read_host(entry, local=False):
        environment_id, name, address = entry
        host: Host = {'environmentId': environment_id, 'label': display_label(name),
                      'status': 'online', 'resources': None}
        try:
            host['resources'] = validate_resources(gateway.read_resources(address, environment_id, local))
            return host, None
        except HostError as error:
            host['status'] = 'unreachable' if error.code == 'unreachable' else 'unavailable'
            return host, {'label': host['label'], 'code': error.code, 'message': str(error)}

    local_host, local_error = read_host((local_id, label, origin), True)
    report['hosts'].append(local_host)
    if local_error:
        report['errors'].append(local_error)
    if not local_only:
        try:
            linked = gateway.linked_environments()
            if not isinstance(linked, list):
                raise HostError('invalid-response', 'T3 Connect did not return a host list.')
            seen, entries = {local_id}, []
            for item in linked:
                if not isinstance(item, dict) or not isinstance(item.get('environmentId'), str):
                    raise HostError('invalid-response', 'T3 Connect returned an invalid host.')
                identity = item['environmentId']
                if identity in seen:
                    continue
                name, endpoint = item.get('label'), item.get('endpoint')
                if not identity or not isinstance(name, str) or not isinstance(endpoint, dict):
                    raise HostError('invalid-response', 'T3 Connect returned an invalid host.')
                entries.append((identity, name, origin_url(endpoint.get('httpBaseUrl'))))
                seen.add(identity)
            entries.sort(key=lambda item: (item[1].casefold(), item[0]))
            with ThreadPoolExecutor(max_workers=4) as pool:
                for host, error in pool.map(read_host, entries):
                    report['hosts'].append(host)
                    if error:
                        report['errors'].append(error)
        except HostError as error:
            report['errors'].append({'label': 'T3 Connect', 'code': error.code, 'message': str(error)})
    report['complete'] = not report['errors']
    return report


def display_label(label: str) -> str:
    return ''.join(c if c.isprintable() else ' ' for c in label).strip() or 'Unnamed host'


def format_table(report: Report) -> str:
    rows = [('Host', 'Status', 'CPU', 'Cores', 'RAM available')]
    for host in report['hosts']:
        resource = host['resources']
        cpu, cores, ram = '--', '--', '--'
        if resource is not None:
            usage = resource['cpuUtilization']
            cpu = '--' if usage is None else f'{usage:.0%}'
            cores = str(resource['cpuCount'])
            ram = '{:.1f} / {:.0f} GiB'.format(
                resource['availableMemoryBytes'] / 1024 ** 3,
                resource['totalMemoryBytes'] / 1024 ** 3,
            )
        rows.append((display_label(host['label']), host['status'], cpu, cores, ram))
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    lines = ['  '.join(value.ljust(widths[i]) if i < 2 else value.rjust(widths[i])
                       for i, value in enumerate(row)).rstrip() for row in rows]
    online = sum(host['status'] == 'online' for host in report['hosts'])
    lines.extend(('', f"{online} of {len(report['hosts'])} hosts reachable."))
    for error in report['errors']:
        lines.append(f"{display_label(error['label'])}: {error['message']}")
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Show CPU and available RAM on T3 Connect hosts.')
    parser.add_argument('--json', action='store_true', help='emit structured output')
    parser.add_argument('--local', action='store_true', help='query only the local server, without Keychain access')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('T3CODE_HOME', '~/.t3')).expanduser(),
                        help='T3 data directory, default T3CODE_HOME or ~/.t3')
    parser.add_argument('--timeout', type=float, default=5, help='timeout per network request in seconds, default 5')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 60:
        parser.error('--timeout must be greater than zero and at most 60 seconds')
    try:
        report = collect_report(args.home.expanduser(), args.timeout, args.local)
        if report is None:
            return 0
        print(json.dumps(report, allow_nan=False) if args.json else format_table(report),
              end='\n' if args.json else '')
        return 0 if report['complete'] else 1
    except HostError as error:
        if args.json:
            print(json.dumps({'complete': False, 'hosts': [], 'errors': [
                {'label': 'T3', 'code': error.code, 'message': str(error)}]}))
        else:
            print('t3-hosts: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
