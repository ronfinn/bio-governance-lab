"""Command-line entry point for bio-governance-lab."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bio_governance import __version__
from bio_governance.synthetic import (
    DEFAULT_COMPOUND_COUNT,
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_SEED,
    DEFAULT_STUDY_ID,
    Injection,
    generate_study,
)

DEFAULT_OUTPUT_ROOT = Path("data/raw")

app = typer.Typer(
    name="bio-gov",
    help="Governance-as-code tooling for synthetic life-sciences data.",
    no_args_is_help=True,
)

demo_app = typer.Typer(
    name="demo",
    help="Generate the synthetic demonstration study.",
    no_args_is_help=True,
)
app.add_typer(demo_app, name="demo")


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
    typer.echo("Plus deterministic synthetic study generation: 'bio-gov demo generate'.")


@demo_app.command("generate")
def demo_generate(
    study: Annotated[
        str,
        typer.Option("--study", help="Study identifier, e.g. BIO-001."),
    ] = DEFAULT_STUDY_ID,
    samples: Annotated[
        int,
        typer.Option("--samples", min=1, help="Number of samples to generate."),
    ] = DEFAULT_SAMPLE_COUNT,
    compounds: Annotated[
        int,
        typer.Option("--compounds", min=1, help="Number of test compounds."),
    ] = DEFAULT_COMPOUND_COUNT,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Seed controlling every generated value."),
    ] = DEFAULT_SEED,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Directory the study folder is written into."),
    ] = DEFAULT_OUTPUT_ROOT,
    inject_missing_sample_id: Annotated[
        bool,
        typer.Option("--inject-missing-sample-id", help="Blank one sample's identifier."),
    ] = False,
    inject_invalid_dose: Annotated[
        bool,
        typer.Option("--inject-invalid-dose", help="Give one sample a negative dose."),
    ] = False,
    inject_duplicate_sample: Annotated[
        bool,
        typer.Option("--inject-duplicate-sample", help="Repeat one sample row verbatim."),
    ] = False,
    inject_unknown_compound: Annotated[
        bool,
        typer.Option("--inject-unknown-compound", help="Reference a compound that does not exist."),
    ] = False,
) -> None:
    """Generate a deterministic synthetic study under the output directory.

    The same study, sample count, compound count and seed always produce
    identical files. The --inject-* options deliberately write malformed data
    for later milestones to detect; nothing here validates it.
    """
    injections = tuple(
        injection
        for injection, requested in (
            (Injection.MISSING_SAMPLE_ID, inject_missing_sample_id),
            (Injection.INVALID_DOSE, inject_invalid_dose),
            (Injection.DUPLICATE_SAMPLE, inject_duplicate_sample),
            (Injection.UNKNOWN_COMPOUND, inject_unknown_compound),
        )
        if requested
    )

    try:
        generated = generate_study(
            output,
            study_id=study,
            samples=samples,
            compounds=compounds,
            seed=seed,
            injections=injections,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"Generated synthetic study {generated.study.study_id} in {generated.directory}")
    for path in generated.files:
        typer.echo(f"  {path}")
    if injections:
        typer.echo("Injected defects: " + ", ".join(injection.value for injection in injections))


if __name__ == "__main__":  # pragma: no cover
    app()
