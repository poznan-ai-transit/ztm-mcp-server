"""Startup argument handling without downloading data or opening a transport."""

from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from cli import StartupOptions, parse_args


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], StartupOptions()),
        (["--schedule-url"], StartupOptions(schedule_url="")),
        (["--schedule-url", ""], StartupOptions(schedule_url="")),
        (
            ["--schedule-url", "https://example.test/download?file=custom"],
            StartupOptions(schedule_url="https://example.test/download?file=custom"),
        ),
        (
            ["--schedule-url=http://example.test/feed.zip"],
            StartupOptions(schedule_url="http://example.test/feed.zip"),
        ),
        (
            ["--schedule-file", "my schedules/custom feed.zip"],
            StartupOptions(schedule_file=Path("my schedules/custom feed.zip")),
        ),
    ],
)
def test_main_passes_parsed_options_to_server(monkeypatch, argv, expected):
    configure = Mock()
    create = Mock()
    run = Mock()
    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    monkeypatch.setattr(main, "configure_logging", configure)
    monkeypatch.setattr(main, "create_server", create)
    monkeypatch.setattr(main, "run_server", run)

    main.main()

    configure.assert_called_once_with()
    create.assert_called_once_with(expected)
    run.assert_called_once_with(create.return_value)


def test_help_exits_before_configuring_logging_or_starting_server(monkeypatch, capsys):
    configure = Mock()
    create = Mock()
    monkeypatch.setattr("sys.argv", ["main.py", "--help"])
    monkeypatch.setattr(main, "configure_logging", configure)
    monkeypatch.setattr(main, "create_server", create)

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--schedule-url [URL]" in help_text
    assert "--single-schedule" not in help_text
    assert "Europe/Warsaw" not in help_text
    configure.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "schedule.zip",
        "file:///tmp/schedule.zip",
        "ftp://example.test/schedule.zip",
        "https://",
        "https://[invalid",
    ],
)
def test_rejects_invalid_download_urls(url, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--schedule-url", url])

    assert exc.value.code == 2
    assert "--schedule-url" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--schedule-url", "https://example.test/feed", "--schedule-file", "feed.zip"],
        ["--schedule-url", "--schedule-file", "feed.zip"],
        ["--schedule-file", "feed.zip", "--schedule-url"],
    ],
)
def test_schedule_sources_are_mutually_exclusive(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)

    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_local_source_requires_a_path(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--schedule-file"])

    assert exc.value.code == 2
    assert "expected one argument" in capsys.readouterr().err


def test_rejects_unknown_arguments(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--unknown-option"])

    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
