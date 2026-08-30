"""Command-line entry point for bio-governance-lab."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bio_governance import __version__
from bio_governance.contracts import (
    ContractError,
    ContractValidationResult,
    DatasetError,
    load_contract,
    validate_dataset,
)
from bio_governance.synthetic import (
    DEFAULT_COMPOUND_COUNT,
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_SEED,
    DEFAULT_STUDY_ID,
    Injection,
    generate_study,
)

DEFAULT_OUTPUT_ROOT = Path("data/raw")

#: Exit status for a dataset that breaks its contract, kept distinct from the
#: status used when the contract or dataset could not be read at all.
FAIL_EXIT_CODE = 1
ERROR_EXIT_CODE = 2

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

contract_app = typer.Typer(
    name="contract",
    help="Validate a dataset against a YAML data contract.",
    no_args_is_help=True,
)
app.add_typer(contract_app, name="contract")


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
    typer.echo("Domain models: Asset, AssetIdentifier, Ownership, Provenance, contracts.")
    typer.echo("Deterministic synthetic study generation: 'bio-gov demo generate'.")
    typer.echo("YAML data contracts over the generated CSVs: 'bio-gov contract validate'.")


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


@contract_app.command("validate")
def contract_validate(
    contract: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Contract definition, e.g. contracts/samples.v1.yaml.",
        ),
    ],
    dataset: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="CSV file to check, e.g. data/raw/BIO-001/samples.csv.",
        ),
    ],
) -> None:
    """Check a CSV dataset against a YAML data contract.

    Exits 0 when the dataset satisfies the contract, 1 when it does not, and 2
    when the contract or the dataset could not be read. Files a contract
    references, such as compounds.csv, are resolved beside the dataset.
    """
    try:
        definition = load_contract(contract)
        result = validate_dataset(definition, dataset)
    except (ContractError, DatasetError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    for line in _format_result(result):
        typer.echo(line)
    if not result.passed:
        raise typer.Exit(FAIL_EXIT_CODE)


def _format_result(result: ContractValidationResult) -> list[str]:
    """Render a validation result as the lines of the CLI report."""
    lines = [
        f"Contract: {result.label}",
        f"Dataset: {result.dataset}",
        "",
        "PASS" if result.passed else "FAIL",
        f"Rows checked: {result.rows_checked}",
    ]
    if result.passed:
        return lines

    count = len(result.violations)
    lines += ["", f"{count} violation{'' if count == 1 else 's'}", ""]

    rows = [
        (
            f"row {violation.row}" if violation.row is not None else "file",
            violation.column or "-",
            violation.rule.value,
            violation.message,
        )
        for violation in result.violations
    ]
    widths = [max(len(row[index]) for row in rows) for index in range(3)]
    lines += [
        "  ".join(cell.ljust(width) for cell, width in zip(row[:3], widths, strict=True))
        + f"  {row[3]}"
        for row in rows
    ]
    return lines


if __name__ == "__main__":  # pragma: no cover
    app()
