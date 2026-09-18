"""The anonymous HPC account-registration form, mounted only when
``ACCOUNT_REGISTRATION_ENABLED`` is on (see ``webapp.run``)."""

from .blueprint import bp

__all__ = ['bp']
