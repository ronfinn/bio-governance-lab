"""Deciding what a study looks like in OpenMetadata's storage model.

Our governed outputs are generated *files*, not tables in a database, so they
are published as OpenMetadata **containers** under a single **storage service**
of type ``CustomStorage``. Labelling them MySQL or Snowflake would put a false
statement in the catalogue, and ``CustomStorage`` is the vocabulary's own answer
for a store OpenMetadata has no connector for.

Two identity schemes meet here and neither replaces the other. ``bio://`` is
this project's identity for a governed asset: stable, readable, and the same
string the OpenLineage events use. An OpenMetadata FQN is the catalogue's own
address for an entity, and it is built from a service name and an entity name
that has to satisfy OpenMetadata's naming rules. So the ``bio://`` URI is
*derived down* into an entity name, and then carried unchanged in the
container's ``fullPath``, where it stays visible and searchable::

    bio://BIO-001/raw/samples
      entity name  BIO-001_raw_samples
      FQN          bio_governance_lab.BIO-001_raw_samples
      fullPath     bio://BIO-001/raw/samples

The seven assets are the same seven the lineage layer already names, imported
rather than restated so the catalogue cannot drift from the provenance.

A study's validated governance declaration is projected too, and only half of it
fits. Its **classification** has an exact home: OpenMetadata's own word for a
controlled, mutually exclusive tag vocabulary is a *Classification*, so the
project's four values become the four tags of one ``bio_governance_classification``
and each container carries the declared one::

    classification: internal
      tag  bio_governance_classification.internal

Its **ownership** does not. An OpenMetadata owner is a reference, by server-
assigned UUID, to a User or Team the server already holds; the declaration
names people, and creating or looking up accounts for them would be user
provisioning. So ownership is deliberately not sent, and stays canonical in the
declaration. ``docs/governance-metadata.md`` records why the alternatives — a
sentence in the description, a custom property — were not taken either.

Nothing in this module performs IO or speaks HTTP.
"""

from __future__ import annotations

from bio_governance.catalog.models import (
    CatalogAsset,
    CatalogColumn,
    FileFormat,
    LineageEdge,
)
from bio_governance.contracts import ColumnType, DataContract
from bio_governance.governance import GovernanceMetadata
from bio_governance.lineage import (
    CURATED_STAGE,
    DATASET_FILES,
    QUALITY_DATASET,
    RAW_STAGE,
)
from bio_governance.models import AssetIdentifier, Classification

#: The one storage service every governed asset is published under.
SERVICE_NAME = "bio_governance_lab"
SERVICE_TYPE = "CustomStorage"
SERVICE_DISPLAY_NAME = "Bio Governance Lab"
SERVICE_DESCRIPTION = (
    "Governed synthetic life-sciences assets produced by bio-governance-lab. "
    "The files are local pipeline output, so they are catalogued as containers "
    "of a custom storage service rather than as tables of a database."
)

#: The OpenMetadata Classification the project's vocabulary is published as.
#: Mutually exclusive, because an asset has one sensitivity, not several; that
#: is OpenMetadata's own distinction between *classifying* and *categorising*.
CLASSIFICATION_NAME = "bio_governance_classification"
CLASSIFICATION_DISPLAY_NAME = "Bio Governance Classification"
CLASSIFICATION_DESCRIPTION = (
    "The sensitivity vocabulary of bio-governance-lab: public, internal, confidential "
    "and restricted. Each asset carries the one its study's governance declaration "
    "states. The declaration, committed in bio-governance-lab, is the canonical record."
)

#: What each dataset holds, in one sentence a steward can read in the catalogue.
_SUBJECTS = {
    "samples": "one row per treated well: treatment, dose, tissue and replicate",
    "compounds": "the study's registry of test articles",
    "expression": "the gene-expression matrix, one row per sample",
}

#: Contract types are a closed vocabulary of three, and so is this mapping.
#: It exists to satisfy OpenMetadata's column model, not to describe chemistry.
_DATA_TYPES = {
    ColumnType.STRING: "STRING",
    ColumnType.INTEGER: "INT",
    ColumnType.NUMBER: "DOUBLE",
}


def entity_name(identifier: AssetIdentifier) -> str:
    """The OpenMetadata entity name for a ``bio://`` identifier.

    Deterministic and one-way: the domain and path segments joined with
    underscores, because OpenMetadata entity names may not carry a scheme or
    slashes. ``bio://BIO-001/raw/samples`` becomes ``BIO-001_raw_samples``.
    """
    return "_".join((identifier.domain, *identifier.path))


def fully_qualified_name(identifier: AssetIdentifier) -> str:
    """The catalogue's address for the container: ``<service>.<entity name>``."""
    return f"{SERVICE_NAME}.{entity_name(identifier)}"


def classification_tag_fqn(classification: Classification) -> str:
    """The fully qualified name of the tag for one classification value.

    The tag name is the vocabulary's own value, unchanged: ``internal`` is
    ``bio_governance_classification.internal``, not a translation of it.
    """
    return f"{CLASSIFICATION_NAME}.{classification.value}"


def classification_tag_description(classification: Classification) -> str:
    """A tag's description. It says where the value comes from, not what it permits.

    The project defines no access rules for its classifications, so the
    catalogue is not given any to display.
    """
    return (
        f"The '{classification.value}' value of bio-governance-lab's Classification "
        "vocabulary, projected from a study's governance declaration."
    )


def study_identifiers(study_id: str) -> tuple[AssetIdentifier, ...]:
    """The seven governed identifiers of a study, raw then curated then quality."""
    staged = tuple(
        AssetIdentifier.parse(f"bio://{study_id}/{stage}/{name}")
        for stage in (RAW_STAGE, CURATED_STAGE)
        for name, _ in DATASET_FILES
    )
    return (*staged, AssetIdentifier.parse(f"bio://{study_id}/{QUALITY_DATASET}"))


def prepare_assets(
    study_id: str,
    *,
    sizes: dict[str, int] | None = None,
    contracts: dict[str, DataContract] | None = None,
    governance: GovernanceMetadata | None = None,
) -> tuple[CatalogAsset, ...]:
    """Describe a study's seven governed assets as OpenMetadata containers.

    ``sizes`` maps a ``bio://`` URI to the byte size of the file behind it, and
    ``contracts`` maps a dataset name such as ``samples`` to the contract whose
    declared columns become the container's data model. ``governance`` is the
    study's validated declaration, whose classification and ownership every
    asset carries. All three are optional: a caller that has not read the files
    still gets the seven assets, because the catalogue's shape is decided by
    the governance model rather than by what happens to be on disk. Publication
    is stricter, and refuses to run without the declaration.
    """
    sizes = sizes or {}
    contracts = contracts or {}
    return tuple(
        _asset(identifier, sizes.get(identifier.uri), contracts, governance)
        for identifier in study_identifiers(study_id)
    )


def lineage_edges(study_id: str) -> tuple[LineageEdge, ...]:
    """The edges this project can explain, and only those.

    Curation copies each raw file to its curated counterpart, so each raw
    container is upstream of exactly one curated container. The quality report
    is the verdict on the whole study, so all three raw containers are upstream
    of it. Nothing is inferred from the OpenLineage events' full input-output
    cross product: an edge is published only where a real dependency can be
    stated in a sentence.
    """
    raw = tuple(f"bio://{study_id}/{RAW_STAGE}/{name}" for name, _ in DATASET_FILES)
    curated = tuple(f"bio://{study_id}/{CURATED_STAGE}/{name}" for name, _ in DATASET_FILES)
    report = f"bio://{study_id}/{QUALITY_DATASET}"

    copies = tuple(
        LineageEdge(from_identifier=source, to_identifier=target)
        for source, target in zip(raw, curated, strict=True)
    )
    evidence = tuple(LineageEdge(from_identifier=source, to_identifier=report) for source in raw)
    return (*copies, *evidence)


def _asset(
    identifier: AssetIdentifier,
    size_bytes: int | None,
    contracts: dict[str, DataContract],
    governance: GovernanceMetadata | None,
) -> CatalogAsset:
    stage, name = identifier.path[0], identifier.path[-1]
    if stage in (RAW_STAGE, CURATED_STAGE):
        subject = _SUBJECTS[name]
        description = (
            f"{'Raw generated' if stage == RAW_STAGE else 'Curated'} {name} of study "
            f"{identifier.domain}: {subject}. "
            + (
                "Written by the synthetic generator and gated by its data contract."
                if stage == RAW_STAGE
                else "Written by the pipeline's CURATE step, which runs only after the "
                "contract and data-quality gates have passed."
            )
        )
        # The curated files are copies, so they carry the same declared columns.
        columns = _columns(contracts.get(name))
        file_format = FileFormat.CSV
    else:
        description = (
            f"Data-quality evidence for study {identifier.domain}: the named checks, "
            "their status and the derived overall verdict that let curation proceed."
        )
        columns = ()
        file_format = FileFormat.JSON

    return CatalogAsset(
        identifier=identifier.uri,
        name=entity_name(identifier),
        display_name=" ".join((identifier.domain, *identifier.path)),
        description=description,
        file_format=file_format,
        columns=columns,
        size_bytes=size_bytes,
        classification=governance.classification if governance else None,
        ownership=governance.ownership if governance else None,
    )


def _columns(contract: DataContract | None) -> tuple[CatalogColumn, ...]:
    """The contract's declared columns, as OpenMetadata's column model.

    The contract is the file's declared structure, so publishing its columns
    puts the agreed shape in the catalogue rather than whatever a header
    happened to say on the day. A dataset with no contract — the wide
    expression matrix — is published without a data model instead of with
    hundreds of generated column entities nobody will read.
    """
    if contract is None:
        return ()
    return tuple(
        CatalogColumn(
            name=column.name,
            data_type=_DATA_TYPES[column.type],
            description=column.description,
        )
        for column in contract.columns
    )
