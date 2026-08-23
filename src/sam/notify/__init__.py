"""Channel-agnostic notifications for SAM -- SMTP first, the ledger with it.

The mailer both consumers share: ``sam-admin project --upcoming-expirations
--notify`` and the webapp's XRAS activation Notify button::

    from sam.notify import Message, Notifier, Recipient

    notifier = Notifier(ledger=NotificationLedger(session_factory))
    result = notifier.send(Message(kind='expiration', recipient=Recipient(...),
                                   subject=..., dedup_key=...))

WARNING: fail-closed. ``NOTIFY_ENABLED`` defaults to false everywhere, because
every dev container and CI worker runs an obfuscated copy of production,
obfuscation does not remove the mail relay, and the relay accepts arbitrary
external recipients from the whole UCAR /16. A ``Notifier`` you did not
explicitly enable records ``suppressed`` and mails nobody.

WARNING: imports here are lazy (PEP 562), and must stay that way. Only
:mod:`sam.notify.base` is eager. ``sam/__init__.py`` exports
:class:`~sam.notify.models.NotificationLog`, so importing any submodule runs
this file -- eager imports would put jinja2, three transports and ``sam.fmt``
into the import graph of every ORM consumer. ``sam.fmt`` in turn imports the
top-level ``config``, which under ``python3 ./src/webapp/run.py`` is shadowed
by ``webapp/config.py``, and startup dies with a partially-initialized-module
``ImportError``. The rule: **the ORM must be importable without the mailer.**

Design and measurements: ``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md``.
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

#: Public name -> the submodule that defines it. Everything reachable from
#: ``sam.notify`` that is not already imported above lives here; ``__getattr__``
#: imports the module on first access and caches the attribute in globals, so
#: the cost is paid once and only by code that actually asks.
_LAZY_EXPORTS = {
    'to_recipients':              'sam.notify.audience',
    'NotifyConfig':               'sam.notify.config',
    'NOTIFICATION_KINDS':         'sam.notify.kinds',
    'NotificationKind':           'sam.notify.kinds',
    'get_kind':                   'sam.notify.kinds',
    'DEDUP_CHUNK':                'sam.notify.ledger',
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
    'DEDUP_CHUNK',
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
    'to_recipients',
    'transport_names',
]
