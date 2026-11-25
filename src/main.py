from cli import parse_args
from logging_config import configure_logging
from server import create_server, run_server


def main() -> None:
    options = parse_args()
    configure_logging()
    run_server(create_server(options))


if __name__ == "__main__":
    main()
