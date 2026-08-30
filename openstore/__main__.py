"""Package entry point: python -m openstore <command>

Available commands:
    reconcile   Run the reconciliation sweeper (PRODUCTION_READINESS §1.5)
    replay      Replay a stored webhook event (PRODUCTION_READINESS §1.4)
"""

from __future__ import annotations

import sys


COMMANDS = {
    "reconcile": "openstore.reconcile",
    "replay": "openstore.replay",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python -m openstore <command> [args]")
        print()
        print("Commands:")
        print("  reconcile   Run the reconciliation sweeper")
        print("  replay      Replay a stored webhook event")
        sys.exit(2)

    command = sys.argv[1]
    # Shift argv so the subcommand's argparse sees its own args
    sys.argv = [sys.argv[0] + " " + command] + sys.argv[2:]

    import importlib
    mod = importlib.import_module(COMMANDS[command])
    mod.main()


if __name__ == "__main__":
    main()
