"""The archiver against a stand-in Kubernetes API on localhost: real sockets, real threads.

Proves what the scripted fakes cannot: `requests` delivers a chunked follow
stream line by line, a roll's last lines are caught, and a restart resumes.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from logarchive import read, store
from logarchive.follow import Archiver
from logarchive.kube import KubeClient

from logarchive_fakes import stamp


class Cluster:
    """Pods with their log lines; `emit` wakes every open follow stream."""

    def __init__(self):
        self.changed = threading.Condition()
        self.pods = {}          # name -> {'app', 'lines': [(stamp, text)], 'done': bool}
        self.token = 'tok'
        self.log_requests = []

    def start(self, name, app='web'):
        with self.changed:
            self.pods[name] = {'app': app, 'lines': [], 'done': False}

    def emit(self, name, second, text, frac=''):
        with self.changed:
            self.pods[name]['lines'].append((stamp(second, frac), text))
            self.changed.notify_all()

    def finish(self, name):
        with self.changed:
            self.pods[name]['done'] = True
            self.changed.notify_all()

    def delete(self, name):
        with self.changed:
            del self.pods[name]
            self.changed.notify_all()


def handler_for(cluster):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def _send(self, code, body=b''):
            self.send_response(code)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            parts = url.path.split('/')[5:]          # after /api/v1/namespaces/<ns>/
            if self.headers.get('Authorization') != f'Bearer {cluster.token}':
                return self._send(401)
            if parts == ['pods']:
                app = query['labelSelector'].split('=', 1)[1]
                with cluster.changed:
                    items = [self._item(n, p) for n, p in cluster.pods.items() if p['app'] == app]
                return self._send(200, json.dumps({'items': items}).encode())
            name = parts[1]
            cluster.log_requests.append((name, query.get('sinceTime')))
            pod = cluster.pods.get(name)
            if pod is None:
                return self._send(404)
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            since = store.parse_ts(query['sinceTime']) if 'sinceTime' in query else (0, 0)
            sent = 0
            try:
                while True:
                    with cluster.changed:   # an open stream outlives the pod's deletion
                        lines = list(pod['lines'][sent:])
                        done = pod['done'] or name not in cluster.pods
                        if not lines and not done:
                            cluster.changed.wait(0.05)
                            continue
                    for ts, text in lines:
                        sent += 1
                        if store.parse_ts(ts) >= since:
                            data = f'{ts} {text}\n'.encode()
                            self.wfile.write(b'%x\r\n%s\r\n' % (len(data), data))
                    self.wfile.flush()
                    if done and not lines:
                        self.wfile.write(b'0\r\n\r\n')
                        return
            except OSError:
                pass

        @staticmethod
        def _item(name, pod):
            state = {'terminated': {'exitCode': 0}} if pod['done'] else {'running': {}}
            return {'metadata': {'name': name, 'uid': name},
                    'spec': {'restartPolicy': 'Never', 'containers': [{'name': 'app'}]},
                    'status': {'phase': 'Succeeded' if pod['done'] else 'Running',
                               'containerStatuses': [{'name': 'app', 'restartCount': 0, 'state': state}]}}

    return Handler


@pytest.fixture
def cluster(tmp_path):
    cluster = Cluster()
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(cluster))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = tmp_path / 'token'
    token.write_text('tok')
    cluster.url, cluster.token_path = f'http://127.0.0.1:{server.server_address[1]}', token
    yield cluster
    server.shutdown()
    server.server_close()


def run_archiver(cluster, root):
    client = KubeClient(cluster.url, 'ns', token_path=cluster.token_path)
    archiver = Archiver(client, root, ['web'], 90, poll_seconds=0.05)
    thread = threading.Thread(target=archiver.run, daemon=True)
    thread.start()
    return archiver, thread


def until(predicate, what, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail(f'timed out waiting for {what}')


def archived(root):
    return [line for _, line in read.iter_lines(root, ['web'])]


def test_a_roll_and_an_archiver_restart_lose_and_repeat_nothing(cluster, tmp_path):
    root = tmp_path / 'archive'
    cluster.start('web-1')
    cluster.emit('web-1', 1, 'one')
    archiver, thread = run_archiver(cluster, root)
    until(lambda: archived(root) == ['one'], 'the first line')

    cluster.emit('web-1', 2, 'two\rstill two', frac='5')      # over the open stream; \r is not a line end
    until(lambda: archived(root) == ['one', 'two\rstill two'], 'a followed line')

    archiver.stop.set()                                       # the archiver goes down ...
    thread.join(5)
    cluster.emit('web-1', 2, 'three', frac='5')               # ... and misses a line sharing the cursor's stamp
    cluster.emit('web-1', 3, 'four')
    archiver, thread = run_archiver(cluster, root)
    until(lambda: archived(root)[2:] == ['three', 'four'], 'the resume')
    assert ('web-1', stamp(2)) in cluster.log_requests        # resumed from the floored cursor

    cluster.start('web-2')                                    # a roll: the old pod's last words,
    cluster.emit('web-2', 4, 'new pod up')
    cluster.emit('web-1', 5, 'shutting down')
    cluster.finish('web-1')                                   # then it terminates and is deleted
    cluster.delete('web-1')
    old = store.log_path(root, 'web', 'web-1', 'app')
    until(lambda: store.is_finished(old), 'the old pod to be finalized')
    until(lambda: 'new pod up' in archived(root), 'the new pod')
    assert archived(root)[2:] == ['three', 'four', 'new pod up', 'shutting down'] and len(archived(root)) == 6

    cluster.token = 'rotated'                                 # the projected token rotates
    cluster.token_path.write_text('rotated')
    cluster.emit('web-2', 6, 'after rotation')
    cluster.finish('web-2')
    until(lambda: store.is_finished(store.log_path(root, 'web', 'web-2', 'app')), 'the new pod to finish')
    assert archived(root)[-1] == 'after rotation'
    assert store.heartbeat_age(root) < 5
    archiver.stop.set()
    thread.join(5)
