import importlib.util
import json
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/t3-hosts/scripts/t3_hosts.py'


def load_command():
    spec = importlib.util.spec_from_file_location('t3_hosts', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class T3HostsTests(unittest.TestCase):
    def test_truncated_remote_response_preserves_healthy_host_measurements(self):
        command = load_command()
        resources = {'cpuUtilization': 0.5, 'cpuCount': 8, 'availableMemoryBytes': 20,
                     'totalMemoryBytes': 100, 'sampledAt': 12345}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                self.wfile.write(b'20\r\n{"partial":true}\r\n')
                self.wfile.flush()
                self.close_connection = True

        with tempfile.TemporaryDirectory() as folder, ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = 'http://127.0.0.1:{}'.format(server.server_port)

            class Gateway:
                def describe(self, address):
                    return {'environmentId': 'local', 'label': 'laptop'}

                def linked_environments(self):
                    return [{'environmentId': 'broken', 'label': 'archive',
                             'endpoint': {'httpBaseUrl': origin}}]

                def read_resources(self, address, environment_id, local=False):
                    return resources if local else command.request_json(address, 2)

            home = Path(folder)
            (home / 'userdata').mkdir()
            (home / 'userdata/server-runtime.json').write_text(json.dumps({'origin': origin}))
            try:
                report = command.collect_report(home, gateway=Gateway())
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(report['hosts'][0]['resources'], resources)
        self.assertEqual(report['hosts'][1]['status'], 'unreachable')
        self.assertFalse(report['complete'])
        self.assertEqual(report['errors'][0]['label'], 'archive')

    def test_connect_reuses_saved_account_and_requests_read_only_access(self):
        command = load_command()
        resources = {'cpuUtilization': 0.2, 'cpuCount': 24, 'availableMemoryBytes': 20,
                     'totalMemoryBytes': 100, 'sampledAt': 12345}
        requests = []

        def http(url, timeout, payload=None, headers=None, form=False):
            requests.append((url, payload, headers or {}))
            if url.endswith('/v1/client?_is_native=true'):
                return {'response': {'last_active_session_id': 'chosen', 'sessions': [
                    {'id': 'other', 'status': 'active'}, {'id': 'chosen', 'status': 'active'}]}}
            if '/sessions/chosen/tokens/' in url:
                return {'jwt': 'relay-jwt'}
            if url == 'https://relay.t3.codes/v1/environments':
                return {'environments': [{'environmentId': 'remote', 'label': 'workstation',
                                         'endpoint': {'httpBaseUrl': 'https://remote.example'}}]}
            if url.endswith('/.well-known/t3/environment'):
                return {'environmentId': 'remote', 'orchestrationProtocolVersion': 2}
            if url.endswith('/v1/client/dpop-token') or url.endswith('/oauth/token'):
                return {'access_token': 'proof-bound-token', 'token_type': 'DPoP'}
            if url.endswith('/v1/environments/remote/connect'):
                return {'environmentId': 'remote', 'credential': 'bootstrap',
                        'endpoint': {'httpBaseUrl': 'https://remote.example'}}
            if url.endswith('/api/auth/websocket-ticket'):
                return {'ticket': 'ticket'}
            self.fail('Unexpected URL: ' + url)

        def crypto_process(arguments, **options):
            request = json.loads(options['input'])
            result = ({'pem': 'private-key', 'jwk': {'kty': 'EC'}, 'thumbprint': 'thumbprint'}
                      if request['action'] == 'generate' else {'signature': 'signature'})
            return subprocess.CompletedProcess(arguments, 0, json.dumps(result), '')

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'userdata').mkdir()
            (home / 'userdata/clerk-tokens.json').write_text(json.dumps({'__clerk_client_jwt': 'raw:saved-client'}))
            client = command.T3Client(home, 5)
            with patch.object(command, 'request_json', side_effect=http), \
                    patch.object(command.shutil, 'which', return_value='/fake/node'), \
                    patch.object(command.subprocess, 'run', side_effect=crypto_process), \
                    patch.object(command, 'resource_rpc', return_value=resources):
                linked = client.linked_environments()
                result = client.read_resources(linked[0]['endpoint']['httpBaseUrl'], 'remote')
        self.assertEqual(result, resources)
        self.assertEqual(requests[0][2]['Authorization'], 'Bearer saved-client')
        host_exchange = next(payload for url, payload, headers in requests if url.endswith('/oauth/token'))
        self.assertEqual(host_exchange['scope'], 'orchestration:read')
        self.assertEqual(linked[0]['label'], 'workstation')

    def test_cli_reads_existing_protocol_two_server_and_noops_without_t3(self):
        resources = {'cpuUtilization': 0.5, 'cpuCount': 12, 'availableMemoryBytes': 2147483648,
                     'totalMemoryBytes': 17179869184, 'sampledAt': 12345}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if self.path != '/api/auth/websocket-ticket' or self.headers.get('Authorization') != 'Bearer fake-token':
                    self.send_error(403)
                    return
                body = b'{"ticket":"one-use-ticket"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/.well-known/t3/environment':
                    body = json.dumps({'environmentId': 'local-id', 'label': 'test-laptop',
                                       'serverVersion': 'test-nightly', 'orchestrationProtocolVersion': 2}).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != '/ws?wsTicket=one-use-ticket&orchestrationProtocol=2':
                    self.send_error(426)
                    return
                accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] +
                    '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept)
                self.end_headers()
                first, second = self.rfile.read(2)
                length = second & 127
                if length == 126:
                    length = struct.unpack('!H', self.rfile.read(2))[0]
                mask = self.rfile.read(4)
                body = self.rfile.read(length)
                request = json.loads(bytes(c ^ mask[i % 4] for i, c in enumerate(body)))
                value = resources if request.get('tag') == 'server.getHostResources' else None
                reply = json.dumps({'_tag': 'Exit', 'requestId': request['id'],
                                    'exit': {'_tag': 'Success', 'value': value}}).encode()
                frame = bytes([0x81, 126]) + struct.pack('!H', len(reply)) + reply
                self.wfile.write(frame)
                self.wfile.flush()

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'bin').mkdir()
            cli = home / 'bin/t3'
            cli.write_text('#!' + sys.executable + '\n' + '''import json,sys
if sys.argv[1:] == ['--version']:
    print('test-nightly')
elif sys.argv[1:3] == ['auth', 'session'] and 'issue' in sys.argv:
    print(json.dumps({'token': 'fake-token', 'sessionId': 'fake-session'}))
elif 'revoke' in sys.argv:
    pass
else:
    sys.exit(2)
''')
            cli.chmod(0o755)
            (home / 'userdata').mkdir()
            environment = {**os.environ, 'T3CODE_HOME': str(home)}
            absent = subprocess.run([sys.executable, str(SCRIPT), '--local', '--json'],
                                    env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual((absent.returncode, absent.stdout, absent.stderr), (0, '', ''))
            with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    origin = 'http://127.0.0.1:{}'.format(server.server_port)
                    (home / 'userdata/server-runtime.json').write_text(json.dumps({'origin': origin}))
                    live = subprocess.run([sys.executable, str(SCRIPT), '--local', '--json'],
                                          env=environment, capture_output=True, text=True, timeout=10)
                finally:
                    server.shutdown()
                    worker.join()
            self.assertEqual(live.returncode, 0, live.stderr)
            result = json.loads(live.stdout)
            self.assertEqual(result['hosts'], [{'environmentId': 'local-id', 'label': 'test-laptop',
                                               'status': 'online', 'resources': resources}])
            self.assertTrue(result['complete'])

    def test_discovers_hosts_once_and_reports_partial_results_without_hiding_failures(self):
        command = load_command()
        resources = {'cpuUtilization': 0.25, 'cpuCount': 8, 'availableMemoryBytes': 1073741824,
                     'totalMemoryBytes': 8589934592, 'sampledAt': 1000}

        class Server:
            def describe(self, origin):
                return {'environmentId': 'local-id', 'label': 'laptop', 'serverVersion': 'nightly',
                        'orchestrationProtocolVersion': 2}

            def linked_environments(self):
                return [{'environmentId': 'local-id', 'label': 'laptop',
                         'endpoint': {'httpBaseUrl': 'https://local.t3.example'}},
                        {'environmentId': 'remote-id', 'label': 'workstation',
                         'endpoint': {'httpBaseUrl': 'https://remote.t3.example'}},
                        {'environmentId': 'offline-id', 'label': 'archive',
                         'endpoint': {'httpBaseUrl': 'https://offline.t3.example'}}]

            def read_resources(self, origin, environment_id, local=False):
                if environment_id == 'offline-id':
                    raise command.HostError('unreachable', 'Host did not answer.')
                return resources

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            gateway = Server()
            self.assertIsNone(command.collect_report(home, gateway=gateway))
            (home / 'userdata').mkdir()
            (home / 'userdata/server-runtime.json').write_text(json.dumps({'origin': 'http://127.0.0.1:3773'}))
            report = command.collect_report(home, gateway=gateway)
        self.assertFalse(report['complete'])
        self.assertEqual([(h['label'], h['status']) for h in report['hosts']],
                         [('laptop', 'online'), ('archive', 'unreachable'), ('workstation', 'online')])
        self.assertEqual(report['hosts'][2]['resources']['cpuCount'], 8)
        self.assertEqual(report['errors'], [{'label': 'archive', 'code': 'unreachable',
                                            'message': 'Host did not answer.'}])

    def test_snapshot_distinguishes_unreachable_hosts_from_idle_hosts(self):
        command = load_command()
        report = {
            'hosts': [
                {'environmentId': 'one', 'label': 'laptop', 'status': 'online',
                 'resources': {'cpuUtilization': 0, 'cpuCount': 16,
                               'availableMemoryBytes': 20 * 1024 ** 3,
                               'totalMemoryBytes': 48 * 1024 ** 3, 'sampledAt': 1000}},
                {'environmentId': 'two', 'label': 'archive', 'status': 'unreachable',
                 'resources': None},
            ],
            'errors': [], 'complete': False,
        }
        rendered = command.format_table(report)
        self.assertIn('CPU', rendered)
        self.assertIn('RAM available', rendered)
        self.assertRegex(rendered, r'laptop\s+online\s+0%\s+16\s+20\.0 / 48 GiB')
        self.assertRegex(rendered, r'archive\s+unreachable\s+--\s+--\s+--')
        self.assertIn('1 of 2 hosts reachable.', rendered)


if __name__ == '__main__':
    unittest.main()
