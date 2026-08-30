"""Core governance models: who owns an asset, where it came from, and its state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bio_governance.models.enums import (
    AssetType,
    Classification,
    GovernanceStatus,
    LifecycleStage,
    QualityStatus,
)
from bio_governance.models.identifiers import AssetIdentifier

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Ownership(BaseModel):
    """Accountable parties for an asset.

    ``owner`` is accountable for the asset existing; ``steward`` is responsible
    for its day-to-day quality and correctness.
    """

    model_config = ConfigDict(frozen=True)

    owner: str = Field(min_length=1)
    steward: str = Field(min_length=1)
    contact: str = Field(pattern=EMAIL_PATTERN)


class Provenance(BaseModel):
    """Where an asset came from and what produced it."""

    model_config = ConfigDict(frozen=True)

    source_system: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    generated_at: datetime
    upstream: tuple[AssetIdentifier, ...] = ()
    synthetic: bool = True


class ContractReference(BaseModel):
    """A pointer to the data contract an asset is expected to satisfy."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    location: str | None = None


class Asset(BaseModel):
    """A governed data asset and the metadata that makes it governable."""

    model_config = ConfigDict(frozen=True)

    identifier: AssetIdentifier
    name: str = Field(min_length=1)
    asset_type: AssetType
    lifecycle_stage: LifecycleStage
    classification: Classification
    ownership: Ownership
    provenance: Provenance
    contract: ContractReference | None = None
    quality_status: QualityStatus = QualityStatus.UNKNOWN
    governance_status: GovernanceStatus = GovernanceStatus.DRAFT
    description: str | None = None
    tags: tuple[str, ...] = ()
