"""Application entry point."""

from server import create_server, run_server


def main() -> None:
    run_server(create_server())


if __name__ == "__main__":
    main()
