"""Controlled vocabularies used across the governance model.

Every enum is a ``str`` enum so that values serialize to plain strings in JSON
and stay readable in catalogues, contracts and logs.
"""

from enum import StrEnum


class AssetType(StrEnum):
    """What kind of thing a governed asset is."""

    DATASET = "dataset"
    TABLE = "table"
    FILE = "file"
    MODEL = "model"
    PIPELINE = "pipeline"
    REPORT = "report"


class LifecycleStage(StrEnum):
    """Where an asset sits in the raw -> published data lifecycle."""

    RAW = "raw"
    CURATED = "curated"
    DERIVED = "derived"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Classification(StrEnum):
    """Sensitivity of the asset's contents."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class QualityStatus(StrEnum):
    """Outcome of the most recent data-quality evaluation."""

    UNKNOWN = "unknown"
    PASSING = "passing"
    WARNING = "warning"
    FAILING = "failing"


class GovernanceStatus(StrEnum):
    """Where the asset sits in its governance review cycle."""

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
