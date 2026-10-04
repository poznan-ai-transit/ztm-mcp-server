from logging_config import configure_logging
from server import create_server, run_server


def main() -> None:
    configure_logging()
    run_server(create_server())


if __name__ == "__main__":
    main()
