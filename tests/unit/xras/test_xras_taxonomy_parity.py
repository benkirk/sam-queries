"""The XRAS service -> kind -> subject -> notify taxonomy, pinned so its four
layers cannot drift.

Adding an action outcome touches four places by design, each with a distinct
job: dispatch selects the *service*, the activation map gives that service a
notify *kind*, the subject map gives the kind a *line*, ``notify/kinds``
*registers* it, and the task decides whether it *auto-sends*. Nothing couples
them but this gate — change one and forget another and a notice silently loses
its subject, its registration, or its auto-send with every test still green.
"""

from sam.notify.kinds import NOTIFICATION_KINDS
from sam.queries.xras_activation import XRAS_SERVICE_KINDS
from sam.queries.xras_notices import XRAS_KIND_SUBJECTS
from sam.xras.dispatch import SERVICES
from scheduling.tasks.xras_notices import AUTO_NOTICES

_KINDS = set(XRAS_SERVICE_KINDS.values())
_AUTO = {n.service for n in AUTO_NOTICES}


def test_every_notifiable_service_is_a_real_dispatch_service():
    assert set(XRAS_SERVICE_KINDS) <= set(SERVICES), \
        set(XRAS_SERVICE_KINDS) - set(SERVICES)


def test_every_kind_has_a_subject_and_every_subject_a_kind():
    assert set(XRAS_KIND_SUBJECTS) == _KINDS


def test_every_kind_is_registered_under_the_xras_family():
    for kind in _KINDS:
        assert kind in NOTIFICATION_KINDS, f'{kind} has no NOTIFICATION_KINDS entry'
        assert NOTIFICATION_KINDS[kind].family == 'xras'


def test_every_auto_sent_service_has_a_kind():
    assert _AUTO <= set(XRAS_SERVICE_KINDS), _AUTO - set(XRAS_SERVICE_KINDS)


def test_add_is_never_auto_sent():
    """A New is two writes — active=True AND the notice — so "is now active" must
    not fire before an operator activates. A policy, not an accident of the tuple."""
    assert 'add' not in _AUTO


def test_transfer_notifies_nowhere():
    """Transfer parks as manual by design: no service->kind entry, no auto-send."""
    assert 'transfer' not in XRAS_SERVICE_KINDS
    assert 'transfer' not in _AUTO
