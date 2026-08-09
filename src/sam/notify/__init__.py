"""Channel-agnostic notifications for SAM — SMTP first, the ledger with it.

``sam.notify`` is the mailer both consumers share: ``sam-admin project
--upcoming-expirations --notify`` and the webapp's XRAS activation Notify
button. It replaces ``cli.notifications.EmailNotificationService``, which was
a working mailer trapped behind a ``rich`` progress bar, a CLI ``Context``, a
hardcoded Bcc, and no record of anything it sent.

Typical use::

    from sam.notify import Message, Notifier, Recipient

    notifier = Notifier(ledger=NotificationLedger(session_factory))
    result = notifier.send(Message(
        kind='expiration',
        recipient=Recipient('pi@example.edu', name='A PI', role='lead'),
        subject='NSF NCAR Project SCSG0001 Expiration Notice',
        context={...},
        facility='UNIV',
        entity=('project', project.project_id),
        projcode='SCSG0001',
        dedup_key='expiration:SCSG0001:2026-09-30:pi@example.edu',
        requested_by='benkirk',
    ))

⚠️ **It is fail-closed.** ``NOTIFY_ENABLED`` defaults to ``false`` everywhere,
because every dev container and CI worker runs against an obfuscated copy of
production, obfuscation does not remove the mail relay, and the relay accepts
arbitrary external recipients from the whole UCAR ``/16``. A ``Notifier`` you
did not explicitly enable records ``suppressed`` and mails nobody.

Imports are **lazy** (PEP 562)
------------------------------
Only :mod:`sam.notify.base` is imported eagerly. Everything else — the
renderer, the transports, the service — loads on first attribute access.

This is not micro-optimisation. ``sam/__init__.py`` exports
:class:`~sam.notify.models.NotificationLog`, and importing *any* submodule
runs this file first. Eager imports here therefore put ``jinja2``, three
transports and ``sam.fmt`` into the import graph of **every** consumer of the
ORM — the CLI, every test, every script — and ``sam.fmt`` in turn imports the
top-level ``config`` module. Under ``python3 ./src/webapp/run.py`` that lands
``sam.fmt`` first in the chain, where ``config`` is shadowed by
``webapp/config.py`` (``sys.path[0]`` is the script's own directory) and
startup dies with a confusing partially-initialised-module ``ImportError``.

The rule this encodes: **the ORM must be importable without the mailer.**

Design, measurements and rationale: ``docs/plans/NOTIFICATION_FRAMEWORK.md``.
"""

from sam.notify.base import (
    Channel,
    DeliveryResult,
    Message,
    NOTIFICATION_STATUSES,
    NotifyError,
    Recipient,
    RenderedMessage,
    Transport,
    TransportError,
)

#: Public name → the submodule that defines it. Everything reachable from
#: ``sam.notify`` that is not already imported above lives here; ``__getattr__``
#: imports the module on first access and caches the attribute in globals, so
#: the cost is paid once and only by code that actually asks.
_LAZY_EXPORTS = {
    'NotifyConfig':               'sam.notify.config',
    'NOTIFICATION_KINDS':         'sam.notify.kinds',
    'NotificationKind':           'sam.notify.kinds',
    'get_kind':                   'sam.notify.kinds',
    'LedgerError':                'sam.notify.ledger',
    'NotificationLedger':         'sam.notify.ledger',
    'SUPPRESSING_STATUSES':       'sam.notify.ledger',
    'NotificationLog':            'sam.notify.models',
    'TRANSPORTS':                 'sam.notify.registry',
    'build_transport':            'sam.notify.registry',
    'transport_names':            'sam.notify.registry',
    'DEFAULT_FACILITY_TEMPLATE':  'sam.notify.render',
    'TEMPLATE_DIR':               'sam.notify.render',
    'TemplateError':              'sam.notify.render',
    'TemplateRenderer':           'sam.notify.render',
    'REDIRECT_BANNER':            'sam.notify.service',
    'Notifier':                   'sam.notify.service',
    'ConsoleTransport':           'sam.notify.transports',
    'NullTransport':              'sam.notify.transports',
    'ORIGINAL_TO_HEADER':         'sam.notify.transports',
    'SmtpTransport':              'sam.notify.transports',
}


def __getattr__(name):
    """PEP 562 lazy attribute access — see the module docstring."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    import importlib
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value          # cache; __getattr__ runs once per name
    return value


def __dir__():
    """So tab-completion and `dir()` still show the full surface."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    'Channel',
    'ConsoleTransport',
    'DEFAULT_FACILITY_TEMPLATE',
    'DeliveryResult',
    'LedgerError',
    'Message',
    'NOTIFICATION_KINDS',
    'NOTIFICATION_STATUSES',
    'NotificationKind',
    'NotificationLedger',
    'NotificationLog',
    'Notifier',
    'NotifyConfig',
    'NotifyError',
    'NullTransport',
    'ORIGINAL_TO_HEADER',
    'REDIRECT_BANNER',
    'Recipient',
    'RenderedMessage',
    'SUPPRESSING_STATUSES',
    'SmtpTransport',
    'TEMPLATE_DIR',
    'TRANSPORTS',
    'TemplateError',
    'TemplateRenderer',
    'Transport',
    'TransportError',
    'build_transport',
    'get_kind',
    'transport_names',
]
