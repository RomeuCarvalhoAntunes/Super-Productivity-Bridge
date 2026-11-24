"""Start the local web application."""
import argparse

from bridge.server import BridgeServer


def main():
    parser = argparse.ArgumentParser(description="Run Super Productivity Bridge on loopback.")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port (default: 8765)")
    args = parser.parse_args()
    try:
        server = BridgeServer(args.port)
    except (OSError, OverflowError) as error:
        parser.exit(1, f"Could not start the local web server: {error}\n")
    with server:
        print(f"Super Productivity Bridge is available at {server.origin}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
