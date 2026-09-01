"""The compositor abstraction.

Backends own *windows*: enumerating them, placing them, closing them. They do
not own *apps*. Resolving an executable from a pid, relaunching Flatpaks,
reopening a browser's tabs and deciding what to reuse rather than launch are
identical on every compositor and stay in ``session.py``.

Keeping the seam here means a new backend implements roughly five short
methods and inherits the rest of Workman's behaviour for free.
"""

from abc import ABC, abstractmethod


class Backend(ABC):
    #: Recorded in the session file and checked when restoring one.
    name = ""

    @staticmethod
    @abstractmethod
    def is_available():
        """Cheap probe: can this backend talk to the compositor right now?

        This tests capability, not identity. ``XDG_CURRENT_DESKTOP`` is empty
        whenever a compositor is started from a TTY rather than a display
        manager, so it can't be trusted to name what's running.
        """

    @abstractmethod
    def capture(self):
        """Return a backend-shaped payload describing the current session.

        The payload must carry a ``windows`` list whose entries each have an
        app key and a ``pid``. ``session.py`` annotates those entries with
        ``exe``/``flatpak``/``urls`` and relaunches them, without needing to
        understand anything else the payload contains.
        """

    @abstractmethod
    def list_windows(self):
        """Return the windows currently on screen.

        Entries have the same shape as ``capture()["windows"]`` entries, so
        the same ``app_key`` applies to both.
        """

    @abstractmethod
    def app_key(self, window):
        """The key windows are grouped by when matching saved against running."""

    @abstractmethod
    def close_window(self, window):
        """Gracefully close one window. True if it worked."""

    @abstractmethod
    def place(self, payload, dry_run=False):
        """Apply a captured payload's layout to what is now on screen."""
