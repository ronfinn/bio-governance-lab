"""Smoke tests for the bio-gov command-line interface."""

from typer.testing import CliRunner

from bio_governance import __version__
from bio_governance.cli import app

runner = CliRunner()


def test_help_lists_the_command_name() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "bio-gov" in result.output


def test_version_reports_the_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "Usage" in result.output


def test_info_command_runs() -> None:
    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert __version__ in result.output
