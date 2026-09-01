"""Exception types shared by the CLI, the orchestration layer and the backends.

These live outside ``session.py`` so that backends can raise them without
importing ``session``, which imports the backends in turn.
"""


class WorkmanError(Exception):
    """Anything the user can act on. The CLI prints these without a traceback."""


class UnsupportedDesktopError(WorkmanError):
    """No backend could talk to the running compositor."""
