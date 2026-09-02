"""Publishing one study's governed outputs into a local OpenMetadata.

This is the last layer of the chain the earlier milestones built. The generator
wrote the study, the contracts said whether each file conformed, the quality
checks said whether the study hung together, the pipeline gated curation on
both, and the OpenLineage events recorded that the run happened. None of that
was discoverable by anyone who did not already know the repository existed.
Publication is what makes it so.

What is published is decided by :mod:`bio_governance.catalog.mapping` and sent
by :mod:`bio_governance.catalog.client`. This module does the one thing neither
of those can: it looks at the directories, refuses to catalogue anything that is
not actually there, and then puts the two together.

The order matters. The service must exist before its containers, and the
containers must exist before an edge between them, because OpenMetadata's
lineage API works in entity IDs.

The four public helpers below — which study this is, which files it consists of,
which run produced them and which contracts describe them — are shared with
:mod:`bio_governance.catalog.datahub_publish`. Which files exist is a fact about
the study, not about a catalogue, and two catalogues that answered it separately
would eventually answer it differently.
"""

from __future__ import annotations

import json
from pathlib import Path

from bio_governance.catalog.client import OpenMetadataClient
from bio_governance.catalog.mapping import (
    SERVICE_DESCRIPTION,
    SERVICE_DISPLAY_NAME,
    SERVICE_NAME,
    SERVICE_TYPE,
    lineage_edges,
    prepare_assets,
)
from bio_governance.catalog.models import CatalogError, PublishedCatalog
from bio_governance.contracts import ContractError, DataContract, load_contract
from bio_governance.lineage import CURATED_STAGE, DATASET_FILES, QUALITY_DATASET, RAW_STAGE
from bio_governance.models import AssetIdentifier

#: Where the pipeline's outputs sit under a results directory.
CURATED_SUBDIR = "curated"
QUALITY_REPORT = Path("quality") / "dq-report.json"
LINEAGE_EVENTS = Path("lineage") / "openlineage.jsonl"

#: Where the shipped contracts live, and which dataset each one describes. Only
#: the two tabular datasets have contracts; the expression matrix is wide and
#: generated, and hundreds of catalogue columns would be noise.
DEFAULT_CONTRACT_DIR = Path("contracts")
CONTRACT_FILES = (("samples", "samples.v1.yaml"), ("compounds", "compounds.v1.yaml"))


def publish_study(
    client: OpenMetadataClient,
    raw_dir: Path,
    results_dir: Path,
    *,
    contract_dir: Path | None = None,
) -> PublishedCatalog:
    """Publish a study's seven governed assets and their lineage.

    ``raw_dir`` is the generated study, ``results_dir`` the pipeline output that
    holds ``curated/``, ``quality/dq-report.json`` and ``lineage/``. Every file
    the catalogue will claim is checked first: a catalogue entry for a file that
    was never written is worse than no entry at all.

    Re-running against the same directories updates the same entities. Both the
    container and the lineage routes are create-or-update, so a second run
    leaves seven containers and six edges, not fourteen and twelve.
    """
    study_id = study_id_from(raw_dir)
    sizes = asset_sizes(study_id, raw_dir, results_dir)
    run_id = lineage_run_id(results_dir / LINEAGE_EVENTS)

    assets = prepare_assets(
        study_id,
        sizes=sizes,
        contracts=load_contracts(contract_dir or DEFAULT_CONTRACT_DIR),
    )
    edges = lineage_edges(study_id)

    service = client.upsert_storage_service(
        name=SERVICE_NAME,
        service_type=SERVICE_TYPE,
        display_name=SERVICE_DISPLAY_NAME,
        description=SERVICE_DESCRIPTION,
    )
    ids = {asset.identifier: client.upsert_container(asset, service=service) for asset in assets}
    for edge in edges:
        client.add_lineage(
            from_id=ids[edge.from_identifier],
            to_id=ids[edge.to_identifier],
        )

    return PublishedCatalog(
        study_id=study_id,
        service=service,
        assets=assets,
        edges=edges,
        lineage_run_id=run_id,
    )


def study_id_from(raw_dir: Path) -> str:
    """Take the study identifier from the raw directory's name, as lineage does.

    Public because the DataHub publication reads the same evidence from the
    same directories. Which files a study consists of, and whether they are
    there, is a fact about the study rather than about either catalogue.
    """
    if not raw_dir.is_dir():
        raise CatalogError(f"raw study directory not found: {raw_dir}")
    study_id = raw_dir.resolve().name
    try:
        AssetIdentifier.parse(f"bio://{study_id}/{RAW_STAGE}/samples")
    except ValueError as exc:
        raise CatalogError(f"{raw_dir} is not named for a study: {exc}") from exc
    return study_id


def asset_sizes(study_id: str, raw_dir: Path, results_dir: Path) -> dict[str, int]:
    """The byte size of every file the catalogue is about to claim exists.

    Doubles as the existence check: a missing file raises here, before a single
    request is sent, so a failed publication leaves nothing half-catalogued.
    """
    curated_dir = results_dir / CURATED_SUBDIR
    if not curated_dir.is_dir():
        raise CatalogError(f"curated directory not found: {curated_dir}")

    sizes = {
        f"bio://{study_id}/{stage}/{name}": _size(directory / file_name, f"{stage} {name}")
        for stage, directory in ((RAW_STAGE, raw_dir), (CURATED_STAGE, curated_dir))
        for name, file_name in DATASET_FILES
    }
    report = results_dir / QUALITY_REPORT
    sizes[f"bio://{study_id}/{QUALITY_DATASET}"] = _size(report, "quality report")
    return sizes


def _size(path: Path, label: str) -> int:
    if not path.is_file():
        raise CatalogError(f"{label} is missing {path}")
    return path.stat().st_size


def lineage_run_id(events: Path) -> str | None:
    """The OpenLineage run ID behind these outputs, when the evidence is there.

    The events file is provenance the catalogue *reports*, not provenance it
    interprets: its inputs and outputs are deliberately not turned into edges,
    because a run's full input-output cross product says far more than this
    project can explain. The run ID is printed so a reader can find the JSONL
    that corresponds to what the catalogue now holds.
    """
    if not events.is_file():
        return None
    for line in events.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return None
        run_id = event.get("run", {}).get("runId") if isinstance(event, dict) else None
        return run_id if isinstance(run_id, str) else None
    return None


def load_contracts(contract_dir: Path) -> dict[str, DataContract]:
    """Load the shipped contracts whose columns become container data models.

    A contract that is not there is not an error. The catalogue's job is to
    describe what exists, and a container without a data model is still an
    honest entry; refusing to publish a study because a YAML file moved would
    be the catalogue holding the pipeline hostage.
    """
    contracts: dict[str, DataContract] = {}
    for dataset, file_name in CONTRACT_FILES:
        path = contract_dir / file_name
        if not path.is_file():
            continue
        try:
            contracts[dataset] = load_contract(path)
        except ContractError as exc:
            raise CatalogError(f"cannot read contract {path}: {exc}") from exc
    return contracts
