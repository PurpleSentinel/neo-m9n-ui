"""Desktop entry point: verify the GTK runtime, then run the application.

The only command-line option is --version (for packaging checks); everything else is in the window.
"""
import sys

from .. import __version__
from .deps import check


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv[1:]
    if "--version" in args:
        print(f"m9n {__version__}")
        return 0
    error = check()
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    from .app import Application
    app = Application()
    # GApplication must not parse our arguments; only the program name is passed.
    status = app.run(sys.argv[:1] if argv is None else argv[:1])
    return app.exit_status or status


if __name__ == "__main__":
    raise SystemExit(main())
