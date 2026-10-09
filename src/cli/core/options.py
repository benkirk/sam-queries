"""Click option decorators shared by `sam-search` and `sam-admin` subcommands."""

import click

from cli.core.context import Context
from cli.core.utils import configure_logging


def verbose_option(help: str = 'Show detailed information'):
    """A subcommand's ``-v/--verbose``: does what the group's ``-v`` does, logging included."""
    def callback(click_ctx, param, value):
        if value:
            click_ctx.ensure_object(Context).verbose = True
            configure_logging(True)
        return value
    return click.option('--verbose', '-v', is_flag=True, help=help, callback=callback)


def provisioning_option(f):
    """``--provisioning/--no-provisioning``: override the host-provisioning cross-check."""
    def callback(click_ctx, param, value):
        if value is not None:
            click_ctx.ensure_object(Context).check_provisioning = value
    return click.option('--provisioning/--no-provisioning', default=None, expose_value=False,
                        callback=callback,
                        help='Cross-check host provisioning (auto-on on a provisioned host)')(f)
