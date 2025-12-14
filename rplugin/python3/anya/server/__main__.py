"""Entry point for running the Anya daemon server.

Usage:
    python -m anya.server
    python -m anya.server --foreground
    python -m anya.server --debug
"""

from .main import main

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Anya Daemon Server")
    parser.add_argument(
        "-f", "--foreground", action="store_true", help="Run in foreground"
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    main(foreground=args.foreground, debug=args.debug)
