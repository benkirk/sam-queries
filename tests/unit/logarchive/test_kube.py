"""`logarchive.kube`: request building, token re-read, and the pod reduction. No network."""

import pytest
import requests

from logarchive.kube import ContainerRef, KubeClient, KubeError, PodGone, StreamBroken, pod_ref


class Response:
    def __init__(self, status=200, payload=None, lines=(), fail=None):
        self.status_code, self.payload, self.lines, self.fail = status, payload, lines, fail
        self.text, self.closed = 'refused', False

    def json(self):
        return self.payload

    def iter_lines(self, delimiter=None):
        assert delimiter == b'\n'
        yield from self.lines
        if self.fail:
            raise self.fail

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def token(tmp_path):
    path = tmp_path / 'token'
    path.write_text('first\n')
    return path


def client(token, *responses):
    c = KubeClient('https://kube:443/', 'sam-queries-dev', token_path=token, verify='/ca.crt')
    c.session = Session(*responses)   # patched on the instance: the outbound guard is never reached
    return c


def test_list_pods_builds_the_namespaced_url_with_the_selector_and_bearer_token(token):
    c = client(token, Response(payload={'items': []}))
    assert c.list_pods('app=samuel-dev') == []
    url, kwargs = c.session.calls[0]
    assert url == 'https://kube:443/api/v1/namespaces/sam-queries-dev/pods'
    assert kwargs['params'] == {'labelSelector': 'app=samuel-dev'}
    assert kwargs['headers'] == {'Authorization': 'Bearer first'}
    assert kwargs['verify'] == '/ca.crt' and kwargs['stream'] is False


def test_stream_log_follows_with_timestamps_and_resumes_from_since_time(token):
    raw = [b'2026-11-26T15:00:01.5Z hello world', b'', b'2026-11-26T15:00:02Z ', b'2026-11-26T15:00:03Z caf\xc3\xa9 \xff']
    c = client(token, Response(lines=raw), Response(lines=[]))
    assert list(c.stream_log('samuel-dev-abc', 'samuel-dev')) == [
        ('2026-11-26T15:00:01.5Z', 'hello world'), ('2026-11-26T15:00:02Z', ''),
        ('2026-11-26T15:00:03Z', 'café �')]
    url, kwargs = c.session.calls[0]
    assert url.endswith('/namespaces/sam-queries-dev/pods/samuel-dev-abc/log')
    assert kwargs['params'] == {'follow': 'true', 'timestamps': 'true', 'container': 'samuel-dev'}
    assert kwargs['stream'] is True and kwargs['timeout'] == (10, 300)
    list(c.stream_log('samuel-dev-abc', 'samuel-dev', '2026-11-26T15:00:03Z'))
    assert c.session.calls[1][1]['params']['sinceTime'] == '2026-11-26T15:00:03Z'


def test_a_401_re_reads_the_rotated_token_once(token):
    c = client(token, Response(status=401), Response(payload={'items': []}))
    token.write_text('rotated\n')
    c.list_pods('app=x')
    assert [k['headers']['Authorization'] for _, k in c.session.calls] == ['Bearer first', 'Bearer rotated']


def test_a_second_401_is_an_error_not_a_loop(token):
    c = client(token, Response(status=401), Response(status=401))
    with pytest.raises(KubeError):
        c.list_pods('app=x')
    assert len(c.session.calls) == 2


def test_a_404_is_pod_gone_and_other_refusals_are_plain_errors(token):
    c = client(token, Response(status=404), Response(status=400), requests.ConnectionError('down'))
    with pytest.raises(PodGone):
        list(c.stream_log('gone', 'c'))
    for _ in range(2):
        with pytest.raises(KubeError) as info:
            list(c.stream_log('creating', 'c'))
        assert not isinstance(info.value, (PodGone, StreamBroken))


def test_a_read_timeout_or_drop_mid_stream_is_stream_broken_and_closes_the_response(token):
    resp = Response(lines=[b'2026-11-26T15:00:01Z a'], fail=requests.ConnectionError('Read timed out.'))
    c = client(token, resp)
    got = []
    with pytest.raises(StreamBroken):
        for item in c.stream_log('p', 'c'):
            got.append(item)
    assert got == [('2026-11-26T15:00:01Z', 'a')] and resp.closed


def test_from_cluster_reads_the_mounted_service_account(tmp_path, monkeypatch):
    (tmp_path / 'token').write_text('tok')
    (tmp_path / 'namespace').write_text('sam-queries\n')
    monkeypatch.setenv('KUBERNETES_SERVICE_HOST', '10.96.0.1')
    monkeypatch.setenv('KUBERNETES_SERVICE_PORT', '6443')
    c = KubeClient.from_cluster(tmp_path)
    assert (c.base_url, c.namespace, c.verify) == ('https://10.96.0.1:6443', 'sam-queries', str(tmp_path / 'ca.crt'))
    monkeypatch.setenv('KUBERNETES_SERVICE_HOST', 'fd00::1')
    assert KubeClient.from_cluster(tmp_path).base_url == 'https://[fd00::1]:6443'


def item(phase='Running', policy='Always', statuses=(), init_statuses=(), init=()):
    return {'metadata': {'name': 'p', 'uid': 'u'},
            'spec': {'restartPolicy': policy, 'containers': [{'name': 'app'}], 'initContainers': list(init)},
            'status': {'phase': phase, 'containerStatuses': list(statuses),
                       'initContainerStatuses': list(init_statuses)}}


def status(name='app', restarts=0, **state):
    return {'name': name, 'restartCount': restarts, 'state': state}


@pytest.mark.parametrize('pod, expected', [
    (item(phase='Pending'), ContainerRef('app', False, False)),
    (item(phase='Pending', statuses=[status(waiting={'reason': 'ContainerCreating'})]), ContainerRef('app', False, False)),
    (item(statuses=[status(running={})]), ContainerRef('app', True, False)),
    # CrashLoopBackOff: waiting, but the last run's log is served, and it will run again.
    (item(statuses=[status(restarts=3, waiting={'reason': 'CrashLoopBackOff'})]), ContainerRef('app', True, False)),
    (item(statuses=[status(terminated={'exitCode': 137})]), ContainerRef('app', True, False)),
    (item(phase='Succeeded', policy='Never', statuses=[status(terminated={'exitCode': 0})]), ContainerRef('app', True, True)),
    (item(phase='Failed', policy='Never', statuses=[status(terminated={'exitCode': 1})]), ContainerRef('app', True, True)),
    (item(policy='OnFailure', statuses=[status(terminated={'exitCode': 1})]), ContainerRef('app', True, False)),
])
def test_a_container_is_finished_only_when_it_cannot_run_again(pod, expected):
    ref = pod_ref(pod)
    assert (ref.name, ref.uid, ref.phase) == ('p', 'u', pod['status']['phase'])
    assert ref.container('app') == expected and ref.container('nope') is None


def test_init_containers_are_listed_first_and_finish_on_success():
    pod = item(init=[{'name': 'migrate'}, {'name': 'proxy', 'restartPolicy': 'Always'}],
               init_statuses=[status('migrate', terminated={'exitCode': 0}), status('proxy', running={})],
               statuses=[status(running={})])
    assert pod_ref(pod).containers == (ContainerRef('migrate', True, True), ContainerRef('proxy', True, False),
                                       ContainerRef('app', True, False))


def test_an_unreadable_list_is_a_kube_error(token):
    with pytest.raises(KubeError):
        client(token, Response(payload={'items': [{'spec': {}}]})).list_pods('app=x')
