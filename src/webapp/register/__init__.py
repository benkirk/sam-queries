"""The HPC account-registration blueprints: ``blueprint`` (the creation form,
ACCOUNT_REGISTRATION_ENABLED), ``events`` and ``invite`` (the event pages and
the invitation link, ACCOUNT_INVITATIONS_ENABLED), and ``allocation_request``
(a visitor's New at ``/request``, always mounted, served by XRAS_SUBMIT_ANONYMOUS_ENABLED).
Mounted in ``webapp.run``."""
