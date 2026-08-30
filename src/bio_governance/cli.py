"""Command-line entry point for bio-governance-lab."""

from __future__ import annotations

from typing import Annotated

import typer

from bio_governance import __version__

app = typer.Typer(
    name="bio-gov",
    help="Governance-as-code tooling for synthetic life-sciences data.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bio-gov {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Governance-as-code tooling for synthetic life-sciences data."""


@app.command()
def info() -> None:
    """Show what this installation currently supports."""
    typer.echo(f"bio-gov {__version__}")
    typer.echo("Domain models only: Asset, AssetIdentifier, Ownership, Provenance, contracts.")


if __name__ == "__main__":  # pragma: no cover
    app()
