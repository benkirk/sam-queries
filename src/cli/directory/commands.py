"""`sam-admin directory report`: the sweep of every directory record against SAM."""

from cli.core.base import BaseCommand
from cli.core.utils import EXIT_ERROR
from cli.directory import builders, display


class DirectoryReportCommand(BaseCommand):
    """Counts by kind and category with sample keys; exit 0 whatever it finds (a report, not an audit)."""

    def execute(self, *, kind=None, limit: int = 0) -> int:
        from sam.integration.directory import DirectoryUnavailable
        from sam.integration.directory.registry import reader_from_environment
        from sam.queries.directory_sweep import SWEPT_KINDS, sweep
        reader = reader_from_environment()
        if not reader.configured:
            self.ctx.stderr_console.print('No directory is configured (DIRECTORY_URL is empty).',
                                          style='bold red')
            return EXIT_ERROR
        try:
            report = sweep(self.session, reader, kinds=(kind,) if kind else SWEPT_KINDS, limit=limit)
        except DirectoryUnavailable as exc:
            self.ctx.stderr_console.print(
                f'The directory could not be read ({type(exc).__name__}).', style='bold red')
            return EXIT_ERROR
        except Exception as e:  # noqa: BLE001
            return self.handle_exception(e)
        return self.emit(builders.build_report(report), display.display_report)
