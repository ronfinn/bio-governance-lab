"""Command-line entry point for bio-governance-lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from bio_governance import __version__
from bio_governance.catalog import (
    DEFAULT_CONTRACT_DIR,
    CatalogError,
    OpenMetadataClient,
    OpenMetadataConfig,
    PublishedCatalog,
    fully_qualified_name,
    publish_study,
    study_identifiers,
)
from bio_governance.contracts import (
    ContractError,
    ContractValidationResult,
    DatasetError,
    load_contract,
    validate_dataset,
)
from bio_governance.governance import (
    GovernanceCheckStatus,
    GovernanceError,
    GovernanceReport,
    evaluate_governance,
)
from bio_governance.lineage import (
    LineageError,
    emit_curation_lineage,
)
from bio_governance.quality import (
    QualityCheckStatus,
    QualityReport,
    StudyError,
    evaluate_study,
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
DEFAULT_RESULTS_ROOT = Path("results")

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

dq_app = typer.Typer(
    name="dq",
    help="Evaluate the data quality of a generated study.",
    no_args_is_help=True,
)
app.add_typer(dq_app, name="dq")

lineage_app = typer.Typer(
    name="lineage",
    help="Emit OpenLineage provenance evidence for a curation run.",
    no_args_is_help=True,
)
app.add_typer(lineage_app, name="lineage")

governance_app = typer.Typer(
    name="governance",
    help="Decide whether a study's evidence makes it ready to use.",
    no_args_is_help=True,
)
app.add_typer(governance_app, name="governance")

catalog_app = typer.Typer(
    name="catalog",
    help="Publish governed assets to a metadata catalogue.",
    no_args_is_help=True,
)
app.add_typer(catalog_app, name="catalog")

mcp_app = typer.Typer(
    name="mcp",
    help="Serve the governance evidence over the Model Context Protocol.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")

openmetadata_app = typer.Typer(
    name="openmetadata",
    help="Publish to a local OpenMetadata instance.",
    no_args_is_help=True,
)
catalog_app.add_typer(openmetadata_app, name="openmetadata")


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
    typer.echo("Study-level data-quality evidence: 'bio-gov dq run'.")
    typer.echo("OpenLineage provenance events for a curation run: 'bio-gov lineage emit'.")
    typer.echo("Deterministic governance decisions: 'bio-gov governance evaluate'.")
    typer.echo("Publication to a local OpenMetadata: 'bio-gov catalog openmetadata publish'.")
    typer.echo("Read-only MCP access to that evidence: 'bio-gov mcp serve'.")


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
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Also write the structured result to this path."),
    ] = None,
) -> None:
    """Check a CSV dataset against a YAML data contract.

    Exits 0 when the dataset satisfies the contract, 1 when it does not, and 2
    when the contract or the dataset could not be read. Files a contract
    references, such as compounds.csv, are resolved beside the dataset.

    --json-out writes the ContractValidationResult itself, which is the evidence
    'bio-gov governance evaluate' later reads; the printed report is for people.
    """
    try:
        definition = load_contract(contract)
        result = validate_dataset(definition, dataset)
    except (ContractError, DatasetError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    if json_out is not None:
        _write_json(result, json_out)

    for line in _format_result(result):
        typer.echo(line)
    if json_out is not None:
        typer.echo(f"\nResult: {json_out}")
    if not result.passed:
        raise typer.Exit(FAIL_EXIT_CODE)


def _write_json(document: BaseModel, path: Path) -> None:
    """Write a report as JSON evidence, before any exit status is decided.

    Every layer writes its evidence the same way and always writes it: a failing
    run is exactly the one whose evidence somebody will want to read, and the
    governance layer downstream has nothing to evaluate if a gate withholds its
    result on the way out.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        typer.echo(f"error: cannot write {path}: {exc.strerror or exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc


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


@dq_app.command("run")
def dq_run(
    study: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Generated study directory, e.g. data/raw/BIO-001.",
        ),
    ],
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Also write the structured report to this path."),
    ] = None,
) -> None:
    """Evaluate a generated study for data quality.

    Contract validation asks whether each file conforms to its declared
    structure; this asks whether the study as a whole is consistent and usable.
    Exits 0 when the study passes or only warns, 1 when a check fails, and 2
    when the study could not be read.
    """
    try:
        report = evaluate_study(study)
    except StudyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    if json_out is not None:
        _write_json(report, json_out)

    for line in _format_report(report):
        typer.echo(line)
    if json_out is not None:
        typer.echo(f"\nReport: {json_out}")
    if report.failed:
        raise typer.Exit(FAIL_EXIT_CODE)


def _format_report(report: QualityReport) -> list[str]:
    """Render a quality report as the lines of the CLI output.

    A passing check prints its name alone. The message is what a reader needs
    only when something went wrong, and the full detail is in the JSON.
    """
    lines = [
        f"Study: {report.study_id}",
        f"Data quality: {report.overall_status.value.upper()}",
        "",
    ]
    width = max(len(check.check_id.value) for check in report.checks)
    for check in report.checks:
        label = f"{check.status.value.upper():<4}  {check.check_id.value}"
        if check.status is QualityCheckStatus.PASS:
            lines.append(label)
        else:
            lines.append(f"{label.ljust(width + 6)}  {check.message}")
    return lines


@lineage_app.command("emit")
def lineage_emit(
    raw: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Raw study directory, e.g. data/raw/BIO-001.",
        ),
    ],
    curated: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Curated output directory, e.g. results/BIO-001/curated.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="JSONL file the events are written to."),
    ],
    quality_report: Annotated[
        Path | None,
        typer.Option("--quality-report", help="dq-report.json, recorded as a further output."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Reuse an existing run identity instead of minting one."),
    ] = None,
) -> None:
    """Emit the OpenLineage events describing one governed curation run.

    Writes a START and a COMPLETE event, sharing one run ID, as the two lines of
    a local JSONL file. The raw files are the run's inputs and the curated files
    its outputs, so the evidence says which raw study a curated directory came
    from. Exits 0 on success and 2 when a required file is missing or the events
    could not be written.
    """
    try:
        emitted = emit_curation_lineage(
            raw,
            curated,
            output,
            quality_report=quality_report,
            run_id=run_id,
        )
    except LineageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    typer.echo(f"Study: {emitted.study_id}")
    typer.echo(f"Run ID: {emitted.run_id}")
    for identifier in emitted.inputs:
        typer.echo(f"  in   {identifier}")
    for identifier in emitted.outputs:
        typer.echo(f"  out  {identifier}")
    typer.echo(f"\nLineage: {emitted.output}")


@governance_app.command("evaluate")
def governance_evaluate(
    results: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Pipeline results directory, e.g. results/BIO-001.",
        ),
    ],
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Also write the structured report to this path."),
    ] = None,
) -> None:
    """Decide whether a study's evidence makes it ready to use.

    Reads the contract results, the quality report, the curated outputs and the
    OpenLineage events the pipeline left behind, and derives one decision from
    them: READY, REVIEW or BLOCKED. Nothing here consults a clock, a network or
    a model — the same evidence always produces the same verdict.

    Exits 0 for READY, 1 for REVIEW or BLOCKED, and 2 only when the results
    directory itself cannot be read as a study's evidence. Evidence that is
    missing or incoherent is a governance failure, so it is reported as a FAIL
    check and a BLOCKED decision rather than as an error.
    """
    try:
        report = evaluate_governance(results)
    except GovernanceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    if json_out is not None:
        _write_json(report, json_out)

    for line in _format_governance(report):
        typer.echo(line)
    if json_out is not None:
        typer.echo(f"\nReport: {json_out}")
    if not report.ready:
        raise typer.Exit(FAIL_EXIT_CODE)


def _format_governance(report: GovernanceReport) -> list[str]:
    """Render a governance report as the lines of the CLI output.

    A passing check prints its name alone, as in the quality report: the message
    is what a reader needs only when the check has something to say.
    """
    lines = [
        f"Study: {report.study_id}",
        f"Decision: {report.decision.value.upper()}",
        "",
    ]
    width = max(len(check.check_id.value) for check in report.checks)
    for check in report.checks:
        label = f"{check.status.value.upper():<4}  {check.check_id.value}"
        if check.status is GovernanceCheckStatus.PASS:
            lines.append(label)
        else:
            lines.append(f"{label.ljust(width + 6)}  {check.message}")
    return lines


@openmetadata_app.command("health")
def catalog_health() -> None:
    """Report whether the configured OpenMetadata instance is reachable.

    Deliberately answerable without a token: while a token is being obtained,
    "is the server up?" is the question worth asking. The token's presence is
    reported, never its value. Exits 0 when the server answered and 2 when it
    did not.
    """
    config = OpenMetadataConfig.from_env()
    typer.echo(f"Host: {config.host}")
    typer.echo(f"Token: {config.token_hint}")

    try:
        with OpenMetadataClient(config) as client:
            version = client.version()
    except CatalogError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    typer.echo(f"OpenMetadata: {version}")


@openmetadata_app.command("publish")
def catalog_publish(
    raw: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Raw study directory, e.g. data/raw/BIO-001.",
        ),
    ],
    results: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Pipeline results directory, e.g. results/BIO-001.",
        ),
    ],
    contracts: Annotated[
        Path,
        typer.Option("--contracts", help="Directory the shipped contracts are read from."),
    ] = DEFAULT_CONTRACT_DIR,
) -> None:
    """Publish a study's governed assets and their lineage to OpenMetadata.

    Upserts one CustomStorage service, the study's seven containers, and the
    six lineage edges this project can explain. Running it twice updates the
    same entities rather than creating a second set. Exits 0 on success and 2
    when the catalogue could not be reached or a claimed file is missing.
    """
    config = OpenMetadataConfig.from_env()
    try:
        with OpenMetadataClient(config) as client:
            version = client.version()
            published = publish_study(client, raw, results, contract_dir=contracts)
    except CatalogError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    typer.echo(f"OpenMetadata: {version} at {config.host}")
    for line in _format_catalog(published):
        typer.echo(line)


@openmetadata_app.command("get")
def catalog_get(
    study: Annotated[
        str,
        typer.Argument(help="Study identifier whose published assets to retrieve, e.g. BIO-001."),
    ],
) -> None:
    """Read a study's published assets back out of OpenMetadata.

    This is the verification half of publication: it asks the catalogue what it
    holds rather than trusting what was sent. Each container is fetched by its
    fully qualified name and reported with the bio:// identifier it carries in
    ``fullPath``, and the raw samples container's lineage is fetched too, so the
    edges can be confirmed through the API rather than by looking at the UI.
    Exits 0 when every expected asset came back and 2 when one did not.
    """
    config = OpenMetadataConfig.from_env()
    identifiers = study_identifiers(study)

    try:
        with OpenMetadataClient(config) as client:
            containers = [
                (identifier, client.get_container(fully_qualified_name(identifier)))
                for identifier in identifiers
            ]
            downstream = _downstream_names(client.get_lineage(fully_qualified_name(identifiers[0])))
    except CatalogError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ERROR_EXIT_CODE) from exc

    typer.echo(f"Study: {study}")
    typer.echo(f"Assets: {len(containers)}")
    typer.echo("")
    width = max(len(str(container.get("fullyQualifiedName", ""))) for _, container in containers)
    for identifier, container in containers:
        fqn = str(container.get("fullyQualifiedName", ""))
        typer.echo(f"  {fqn.ljust(width)}  {container.get('fullPath', '(no fullPath)')}")
        if container.get("fullPath") != identifier.uri:
            typer.echo(f"    warning: fullPath is not {identifier.uri}")

    typer.echo(f"\nDownstream of {identifiers[0]}: {len(downstream)}")
    for name in downstream:
        typer.echo(f"  {name}")


def _format_catalog(published: PublishedCatalog) -> list[str]:
    """Render a publication as the lines of the CLI summary."""
    lines = [
        f"Service: {published.service}",
        f"Study: {published.study_id}",
        "",
        f"{len(published.assets)} assets",
    ]
    width = max(len(asset.name) for asset in published.assets)
    lines += [
        f"  {asset.name.ljust(width)}  {asset.file_format.value:<4}  {asset.identifier}"
        for asset in published.assets
    ]
    lines += ["", f"{len(published.edges)} lineage edges"]
    lines += [f"  {edge}" for edge in published.edges]
    if published.lineage_run_id is not None:
        lines += ["", f"OpenLineage run: {published.lineage_run_id}"]
    return lines


def _downstream_names(graph: dict[str, Any]) -> list[str]:
    """The entity names one hop downstream, as OpenMetadata's lineage graph gives them.

    The graph reports edges as entity IDs and the entities themselves in a
    separate list, so the two have to be joined to say anything readable.
    """
    nodes = {
        str(node.get("id")): str(
            node.get("fullyQualifiedName") or node.get("name") or node.get("id")
        )
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
    }
    entity = graph.get("entity")
    root = str(entity.get("id")) if isinstance(entity, dict) else None
    return sorted(
        nodes.get(str(edge.get("toEntity")), str(edge.get("toEntity")))
        for edge in graph.get("downstreamEdges", [])
        if isinstance(edge, dict) and (root is None or str(edge.get("fromEntity")) == root)
    )


@mcp_app.command("serve")
def mcp_serve(
    results_root: Annotated[
        Path,
        typer.Option(
            "--results-root",
            exists=True,
            file_okay=False,
            help="Directory the governed studies are read from.",
        ),
    ] = DEFAULT_RESULTS_ROOT,
) -> None:
    """Serve the governance evidence to an MCP client over stdio.

    Runs until the client disconnects, speaking the Model Context Protocol on
    stdin and stdout. Six read-only tools and two resources expose the studies
    under --results-root: the governance decision, the evidence behind it, and
    which checks stand between a study and READY.

    Nothing served here can be written to. The decision is computed by
    'bio-gov governance evaluate' from files on disk, and an MCP client can read
    and explain it but has no tool that recomputes, overrides or approves it.
    """
    # Imported here rather than at module scope: the MCP SDK costs about a
    # second to import, and every other bio-gov command — including the six the
    # pipeline shells out to on every run — would pay it for nothing.
    from bio_governance.mcp import build_server

    typer.echo(f"bio-gov MCP server on stdio, serving {results_root}", err=True)
    build_server(results_root).run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    app()
