"""Shared fixtures for the test suite."""

from datetime import UTC, datetime

import pytest

from bio_governance.models import (
    Asset,
    AssetIdentifier,
    AssetType,
    Classification,
    LifecycleStage,
    Ownership,
    Provenance,
)


@pytest.fixture
def ownership() -> Ownership:
    return Ownership(
        owner="Translational Data Platform",
        steward="Ron Finn",
        contact="steward@example.org",
    )


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        source_system="synthetic-generator",
        generated_by="bio-gov",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def asset(ownership: Ownership, provenance: Provenance) -> Asset:
    return Asset(
        identifier=AssetIdentifier.parse("bio://BIO-001/raw/samples"),
        name="Raw samples",
        asset_type=AssetType.DATASET,
        lifecycle_stage=LifecycleStage.RAW,
        classification=Classification.INTERNAL,
        ownership=ownership,
        provenance=provenance,
    )
