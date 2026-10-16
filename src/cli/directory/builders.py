"""Dict shapes for the directory commands. No Rich, no I/O."""


def build_report(report) -> dict:
    return {'kind': 'directory_report', **report.as_dict()}
