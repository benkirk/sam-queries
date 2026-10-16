"""The latch: one failure pauses reads for the backoff, then they resume."""
from sam.integration.directory import outage


def test_the_latch(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(outage.time, 'monotonic', lambda: clock[0])
    outage.reset()
    assert outage.is_down() is False
    outage.mark_down(60)
    assert outage.is_down() is True
    clock[0] += 59
    assert outage.is_down() is True
    clock[0] += 2
    assert outage.is_down() is False
    outage.mark_down()
    outage.reset()
    assert outage.is_down() is False
