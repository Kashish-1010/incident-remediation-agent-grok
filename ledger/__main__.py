# `python -m ledger` enters here and returns the CLI exit code.
from ledger.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
