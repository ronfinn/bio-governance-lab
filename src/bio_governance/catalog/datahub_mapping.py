"""Deciding what a study looks like in DataHub's entity model.

DataHub does not model a store of files the way OpenMetadata does. There is no
storage service and no container: everything with rows or fields is a
**Dataset**, and a Dataset belongs to a **data platform** — the thing that
produced it — and to an **environment** such as ``PROD``. So the same seven
governed assets that OpenMetadata holds as containers of a ``CustomStorage``
service are held here as datasets of a ``bio_governance_lab`` platform. Neither
catalogue is being made to imitate the other; each is used the way it is meant
to be used, which is the whole point of having two.

A DataHub dataset is addressed by a URN built from those three parts::

    urn:li:dataset:(urn:li:dataPlatform:bio_governance_lab,BIO-001.raw.samples,PROD)

The identity rule is the one the OpenMetadata mapping already states, applied
to a second catalogue: ``bio://`` is the project's identity and the URN is
DataHub's. The URI is *derived down* into a dotted dataset name and then
carried back unchanged in the dataset's ``qualifiedName`` and in a
``canonical_asset_id`` custom property::

    bio://BIO-001/raw/samples
      dataset name       BIO-001.raw.samples
      URN                urn:li:dataset:
                           (urn:li:dataPlatform:bio_governance_lab,BIO-001.raw.samples,PROD)
      qualifiedName      bio://BIO-001/raw/samples
      canonical_asset_id bio://BIO-001/raw/samples

The ``bio://`` URI could have been used as the dataset name directly — DataHub
tolerates slashes there, and S3 datasets are named by path — but it was not.
The URN already names the platform, so a name beginning ``bio://`` would say it
twice, and the browse paths DataHub derives by splitting on the platform's
delimiter would come out as ``bio:`` and an empty segment. The dotted form is
the one convention, and the canonical URI is carried as data rather than as an
address.

What is published is the same seven assets and six edges the OpenMetadata
mapping prepares, imported rather than restated: two catalogues that disagreed
about which assets exist would be comparing nothing. Only the identities differ,
and they are computed here.

The study's governance declaration is projected in DataHub's vocabulary, which
is not OpenMetadata's:

* **classification** becomes a **glossary term**. DataHub's tags are, in its own
  documentation, informal labels; a controlled vocabulary for governance is a
  business glossary, and DataHub's own classifier applies glossary terms. The
  four values are terms under one ``bio_governance_classification`` node, and
  each dataset's ``glossaryTerms`` aspect names the declared one.
* **ownership** becomes the ``ownership`` aspect, with the owner as a
  ``BUSINESS_OWNER`` and the steward as a ``DATA_STEWARD`` — DataHub has a
  steward role of its own, where OpenMetadata has only "owners". An owner is a
  URN, and DataHub accepts a URN for a user it has never seen, exactly as it
  accepts an upstream dataset it has never seen, so no account is created.

Nothing in this module performs IO, speaks HTTP, or imports the DataHub SDK.
Keeping the SDK out of it is what lets the CLI read a configuration and derive a
URN without importing a metadata model it is not going to send.
"""

from __future__ import annotations

from bio_governance.catalog.mapping import CLASSIFICATION_NAME, lineage_edges, study_identifiers
from bio_governance.catalog.models import CatalogAsset
from bio_governance.lineage import CURATED_STAGE, QUALITY_DATASET, RAW_STAGE
from bio_governance.models import AssetIdentifier, Classification, Ownership

#: The lineage layer names the quality report ``quality/dq-report``; its first
#: segment is the third lifecycle stage, taken from there rather than restated
#: so a rename upstream cannot leave this module quietly disagreeing.
QUALITY_STAGE = QUALITY_DATASET.split("/")[0]

#: The data platform every governed asset is published under, and its URN.
#: A dedicated platform rather than a borrowed one: these files were produced
#: by this project, and registering them under ``s3`` or ``file`` would say
#: something untrue about where they came from.
PLATFORM_NAME = "bio_governance_lab"
PLATFORM_URN = f"urn:li:dataPlatform:{PLATFORM_NAME}"
PLATFORM_DISPLAY_NAME = "Bio Governance Lab"

#: What DataHub splits a dataset name on to build its browse paths.
NAME_DELIMITER = "."

#: The single environment. This project has one deployment of one pipeline, and
#: inventing DEV and PROD copies of a study that exists once would be fiction.
ENVIRONMENT = "PROD"

#: The DataHub subtype each asset carries, so a reader can tell the three
#: lifecycle stages apart in a list that would otherwise be seven files.
SUBTYPES = {
    RAW_STAGE: "Raw File",
    CURATED_STAGE: "Curated File",
    QUALITY_STAGE: "Quality Report",
}

#: The property that carries the project's identity into DataHub unchanged.
CANONICAL_PROPERTY = "canonical_asset_id"

#: The glossary node the classification vocabulary lives under. The same name
#: the OpenMetadata Classification has: the vocabulary is the project's, and
#: only the kind of entity that holds it differs between the catalogues.
GLOSSARY_NODE_URN = f"urn:li:glossaryNode:{CLASSIFICATION_NAME}"
GLOSSARY_NODE_DISPLAY_NAME = "Bio Governance Classification"
GLOSSARY_NODE_DEFINITION = (
    "The sensitivity vocabulary of bio-governance-lab: public, internal, confidential "
    "and restricted. Each dataset carries the term its study's governance declaration "
    "states. The declaration, committed in bio-governance-lab, is the canonical record."
)

#: Where a glossary term was defined, in DataHub's vocabulary: here, not in an
#: external standard.
TERM_SOURCE = "INTERNAL"

#: Who applied the glossary terms, and when. DataHub's own SDK stamps terms with
#: this actor and time zero (``datahub.sdk._shared``), and so does this project:
#: the declaration carries no timestamp, and inventing one from the clock would
#: make two publications of one declaration differ.
TERMS_ACTOR = "urn:li:corpuser:__ingestion"
TERMS_TIME = 0

#: The DataHub ownership type each declared role becomes. Names match the SDK's
#: ``OwnershipTypeClass`` constants, restated so this module stays SDK-free.
OWNERSHIP_TYPES = (("owner", "BUSINESS_OWNER"), ("steward", "DATA_STEWARD"))


def dataset_name(identifier: AssetIdentifier) -> str:
    """The DataHub dataset name for a ``bio://`` identifier.

    Deterministic and one-way: the domain and path segments joined with the
    platform's delimiter. ``bio://BIO-001/raw/samples`` becomes
    ``BIO-001.raw.samples``, and DataHub browses it as BIO-001 → raw → samples.
    """
    return NAME_DELIMITER.join((identifier.domain, *identifier.path))


def dataset_urn(identifier: AssetIdentifier) -> str:
    """DataHub's address for the dataset behind a ``bio://`` identifier.

    Built as a string rather than through the SDK's ``make_dataset_urn`` so
    that deriving an identity costs nothing to import. The two are asserted to
    agree in the tests, which is where the SDK belongs: as the authority the
    convention is checked against, not as a dependency of reading a URN.
    """
    return f"urn:li:dataset:({PLATFORM_URN},{dataset_name(identifier)},{ENVIRONMENT})"


def study_urns(study_id: str) -> tuple[str, ...]:
    """The seven dataset URNs of a study, raw then curated then quality."""
    return tuple(dataset_urn(identifier) for identifier in study_identifiers(study_id))


def lifecycle_stage(identifier: AssetIdentifier) -> str:
    """Which of the three lifecycle stages an asset belongs to."""
    return identifier.path[0]


def custom_properties(asset: CatalogAsset) -> dict[str, str]:
    """The searchable facts DataHub keeps beside a dataset's description.

    Deliberately few, and every one of them derivable from evidence this
    project already produces. ``canonical_asset_id`` is the important one: it
    is what makes a DataHub URN a deployment address rather than a rename of
    the asset. The values are strings because DataHub's custom properties are
    a string map; ``size_bytes`` is omitted rather than written as ``"None"``
    when the file behind an asset was not measured.
    """
    identifier = AssetIdentifier.parse(asset.identifier)
    properties = {
        CANONICAL_PROPERTY: asset.identifier,
        "study": identifier.domain,
        "lifecycle_stage": lifecycle_stage(identifier),
        "file_format": asset.file_format.value,
    }
    if asset.size_bytes is not None:
        properties["size_bytes"] = str(asset.size_bytes)
    return properties


def subtype(asset: CatalogAsset) -> str:
    """The DataHub subtype for an asset, from its lifecycle stage."""
    return SUBTYPES[lifecycle_stage(AssetIdentifier.parse(asset.identifier))]


def term_urn(classification: Classification) -> str:
    """The glossary term for one classification value, named by the value itself."""
    return f"urn:li:glossaryTerm:{CLASSIFICATION_NAME}.{classification.value}"


def term_definition(classification: Classification) -> str:
    """A term's definition. It says where the value comes from, not what it permits."""
    return (
        f"The '{classification.value}' value of bio-governance-lab's Classification "
        "vocabulary, projected from a study's governance declaration."
    )


def owners(ownership: Ownership) -> tuple[tuple[str, str], ...]:
    """Each declared person and the DataHub ownership type their role becomes.

    Names, not URNs: a person's name may hold characters a URN must encode, and
    the SDK's ``make_user_urn`` — used by the client — is the authority on how.
    ``contact`` is not projected: an address belongs to a user's profile, and
    this project creates no users.
    """
    return tuple((getattr(ownership, role), kind) for role, kind in OWNERSHIP_TYPES)


def upstreams(study_id: str) -> dict[str, tuple[str, ...]]:
    """The six lineage edges, grouped the way DataHub records them.

    OpenMetadata's lineage API takes one edge at a time. DataHub's takes an
    ``upstreamLineage`` aspect that *replaces* the whole upstream list of one
    dataset, so the three raw inputs of the quality report have to be sent
    together or the last one would erase the other two. The edges themselves
    are the same six the OpenMetadata mapping explains; only the shape they are
    sent in differs, and that difference is exactly the kind of thing this
    project exists to notice.
    """
    grouped: dict[str, list[str]] = {}
    for edge in lineage_edges(study_id):
        grouped.setdefault(edge.to_identifier, []).append(edge.from_identifier)
    return {target: tuple(sources) for target, sources in grouped.items()}
