import sys

if len(sys.argv) > 1:
    # Transitional: any arguments select the CLI until it is removed (#14).
    from .cli import main
else:
    from .gui.application import main

raise SystemExit(main())
