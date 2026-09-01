from importlib.metadata import version, PackageNotFoundError

try:
    # Report the version of the actually-installed package, so `workman
    # --version` reflects what the user has on their system.
    __version__ = version("workman")
except PackageNotFoundError:
    # Running from a source checkout that was never installed. Deliberately not
    # a version number: nothing in the release process updates this line, so any
    # number written here silently rots (it read 0.1.2 through three releases).
    __version__ = "0+source"
