"""Desktop entry point: verify the GTK runtime, then run the application."""
import sys

from .deps import check


def main(argv=None):
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
