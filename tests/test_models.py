"""Tests for the core governance models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bio_governance.models import (
    Asset,
    AssetIdentifier,
    AssetType,
    Classification,
    ContractReference,
    GovernanceStatus,
    LifecycleStage,
    Ownership,
    Provenance,
    QualityStatus,
)


def test_valid_asset_creation(asset: Asset) -> None:
    assert asset.identifier.uri == "bio://BIO-001/raw/samples"
    assert asset.asset_type is AssetType.DATASET
    assert asset.lifecycle_stage is LifecycleStage.RAW
    assert asset.classification is Classification.INTERNAL


def test_asset_defaults_to_unreviewed_and_unknown_quality(asset: Asset) -> None:
    assert asset.governance_status is GovernanceStatus.DRAFT
    assert asset.quality_status is QualityStatus.UNKNOWN
    assert asset.contract is None
    assert asset.tags == ()


def test_asset_accepts_an_identifier_string(ownership: Ownership, provenance: Provenance) -> None:
    asset = Asset(
        identifier="bio://BIO-002/curated/subjects",  # type: ignore[arg-type]
        name="Curated subjects",
        asset_type=AssetType.TABLE,
        lifecycle_stage=LifecycleStage.CURATED,
        classification=Classification.CONFIDENTIAL,
        ownership=ownership,
        provenance=provenance,
    )

    assert asset.identifier == AssetIdentifier.parse("bio://BIO-002/curated/subjects")


def test_asset_rejects_a_malformed_identifier(ownership: Ownership, provenance: Provenance) -> None:
    with pytest.raises(ValidationError):
        Asset(
            identifier="not-a-uri",  # type: ignore[arg-type]
            name="Broken",
            asset_type=AssetType.DATASET,
            lifecycle_stage=LifecycleStage.RAW,
            classification=Classification.INTERNAL,
            ownership=ownership,
            provenance=provenance,
        )


@pytest.mark.parametrize(
    "field",
    ["asset_type", "lifecycle_stage", "classification", "quality_status", "governance_status"],
)
def test_enum_fields_reject_unknown_values(asset: Asset, field: str) -> None:
    payload = asset.model_dump()
    payload[field] = "not-a-real-value"

    with pytest.raises(ValidationError):
        Asset.model_validate(payload)


def test_enum_fields_accept_their_string_values(asset: Asset) -> None:
    payload = asset.model_dump()
    payload["quality_status"] = "passing"
    payload["governance_status"] = "approved"

    revalidated = Asset.model_validate(payload)

    assert revalidated.quality_status is QualityStatus.PASSING
    assert revalidated.governance_status is GovernanceStatus.APPROVED


def test_ownership_requires_owner_steward_and_contact() -> None:
    ownership = Ownership(owner="Platform", steward="Ron Finn", contact="ron@example.org")

    assert ownership.owner == "Platform"
    assert ownership.steward == "Ron Finn"
    assert ownership.contact == "ron@example.org"


@pytest.mark.parametrize(
    ("owner", "steward", "contact"),
    [
        ("", "Ron Finn", "ron@example.org"),
        ("Platform", "", "ron@example.org"),
        ("Platform", "Ron Finn", "not-an-email"),
        ("Platform", "Ron Finn", ""),
    ],
)
def test_ownership_rejects_incomplete_records(owner: str, steward: str, contact: str) -> None:
    with pytest.raises(ValidationError):
        Ownership(owner=owner, steward=steward, contact=contact)


def test_provenance_defaults_to_synthetic_with_no_upstream(provenance: Provenance) -> None:
    assert provenance.synthetic is True
    assert provenance.upstream == ()


def test_provenance_records_upstream_identifiers() -> None:
    provenance = Provenance(
        source_system="nextflow",
        generated_by="curate.nf",
        generated_at=datetime(2026, 1, 2, tzinfo=UTC),
        upstream=(AssetIdentifier.parse("bio://BIO-001/raw/samples"),),
        synthetic=False,
    )

    assert provenance.upstream[0].uri == "bio://BIO-001/raw/samples"
    assert provenance.synthetic is False


def test_contract_reference_requires_a_semantic_version() -> None:
    contract = ContractReference(name="samples", version="1.0.0")

    assert contract.version == "1.0.0"
    assert contract.location is None

    with pytest.raises(ValidationError):
        ContractReference(name="samples", version="v1")


def test_asset_serializes_identifiers_as_uri_strings(asset: Asset) -> None:
    payload = asset.model_dump()

    assert payload["identifier"] == "bio://BIO-001/raw/samples"
    assert payload["asset_type"] == "dataset"


def test_asset_round_trips_through_json(asset: Asset) -> None:
    restored = Asset.model_validate_json(asset.model_dump_json())

    assert restored == asset


def test_asset_round_trips_with_a_contract_and_upstream(
    ownership: Ownership,
) -> None:
    asset = Asset(
        identifier=AssetIdentifier.parse("bio://BIO-001/curated/samples"),
        name="Curated samples",
        asset_type=AssetType.DATASET,
        lifecycle_stage=LifecycleStage.CURATED,
        classification=Classification.RESTRICTED,
        ownership=ownership,
        provenance=Provenance(
            source_system="nextflow",
            generated_by="curate.nf",
            generated_at=datetime(2026, 1, 2, tzinfo=UTC),
            upstream=(AssetIdentifier.parse("bio://BIO-001/raw/samples"),),
        ),
        contract=ContractReference(name="samples", version="1.2.0"),
        quality_status=QualityStatus.PASSING,
        governance_status=GovernanceStatus.APPROVED,
        tags=("oncology", "synthetic"),
    )

    restored = Asset.model_validate_json(asset.model_dump_json())

    assert restored == asset
    assert restored.provenance.upstream[0].uri == "bio://BIO-001/raw/samples"
