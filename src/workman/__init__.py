from importlib.metadata import version, PackageNotFoundError

try:
    # Report the version of the actually-installed package, so `workman
    # --version` reflects what the user has on their system.
    __version__ = version("workman")
except PackageNotFoundError:
    # Running from a source checkout that was never installed.
    __version__ = "0.1.2"
