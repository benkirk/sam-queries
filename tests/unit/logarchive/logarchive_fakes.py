"""Fakes for the log-archiver tests: a scripted KubeClient and a stop that never sleeps."""

from logarchive.kube import ContainerRef, PodRef


def stamp(second: int, frac: str = '') -> str:
    """A UTC timestamp `second` seconds into 2026-11-26T15:00, with an optional fraction."""
    return f'2026-11-26T15:{second // 60:02d}:{second % 60:02d}{"." + frac if frac else ""}Z'


def pod(name: str, *, started: bool = True, finished: bool = False, container: str = 'app') -> PodRef:
    return PodRef(name, f'uid-{name}', 'Running', (ContainerRef(container, started, finished),))


class FakeStop:
    """Stands in for threading.Event; `wait` returns at once and records the delay."""

    def __init__(self):
        self.waits, self.flag = [], False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds=None):
        self.waits.append(seconds)
        return self.flag


class FakeClient:
    """`streams`: one script per stream_log call; an exception in a script is raised in place.
    `listings`: one pod list (or exception) per list_pods call; the last repeats."""

    def __init__(self, streams=(), listings=((),)):
        self.streams, self.listings = list(streams), list(listings)
        self.stream_calls, self.list_calls = [], []

    def list_pods(self, selector):
        self.list_calls.append(selector)
        result = self.listings[min(len(self.list_calls), len(self.listings)) - 1]
        if isinstance(result, Exception):
            raise result
        return list(result)

    def stream_log(self, pod_name, container, since_time=None):
        self.stream_calls.append((pod_name, container, since_time))
        for item in self.streams.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item
