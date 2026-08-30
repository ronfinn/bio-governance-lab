"""Tests for the URI-style asset identifier."""

import pytest
from pydantic import ValidationError

from bio_governance.models import AssetIdentifier


def test_parses_uri_into_domain_and_path() -> None:
    identifier = AssetIdentifier.parse("bio://BIO-001/raw/samples")

    assert identifier.domain == "BIO-001"
    assert identifier.path == ("raw", "samples")


def test_round_trips_through_its_uri_form() -> None:
    identifier = AssetIdentifier.parse("bio://BIO-001/raw/samples")

    assert identifier.uri == "bio://BIO-001/raw/samples"
    assert str(identifier) == "bio://BIO-001/raw/samples"
    assert AssetIdentifier.parse(identifier.uri) == identifier


def test_accepts_a_single_path_segment() -> None:
    assert AssetIdentifier.parse("bio://BIO-001/samples").path == ("samples",)


def test_can_be_built_from_parts() -> None:
    identifier = AssetIdentifier(domain="BIO-001", path=("raw", "samples"))

    assert identifier.uri == "bio://BIO-001/raw/samples"


def test_is_hashable_and_frozen() -> None:
    identifier = AssetIdentifier.parse("bio://BIO-001/raw/samples")

    assert {identifier, AssetIdentifier.parse("bio://BIO-001/raw/samples")} == {identifier}
    with pytest.raises(ValidationError):
        identifier.domain = "BIO-002"


@pytest.mark.parametrize(
    "uri",
    [
        "BIO-001/raw/samples",  # no scheme
        "https://BIO-001/raw/samples",  # wrong scheme
        "bio://BIO-001",  # no path
        "bio://BIO-001/",  # empty path
        "bio://BIO-001/raw//samples",  # empty segment
        "bio://bio-001/raw/samples",  # lower-case domain
        "bio://BIO_001/raw/samples",  # underscore in domain
        "bio://BIO-001/Raw/samples",  # upper-case segment
        "bio://BIO-001/raw samples",  # space in segment
        "",
    ],
)
def test_rejects_malformed_identifiers(uri: str) -> None:
    with pytest.raises(ValidationError):
        AssetIdentifier.parse(uri)
