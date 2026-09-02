"""Publishing one study's governed outputs into a local DataHub.

The evidence is the same evidence the OpenMetadata publication reads — the same
seven files under the same two directories, checked the same way and refused the
same way — so the checking is imported from :mod:`bio_governance.catalog.publish`
rather than written twice. Two catalogues that disagreed about which files exist
would not be comparable, and a second copy of the existence check is exactly how
that disagreement would arrive.

What differs is everything downstream of the check, and it differs because the
catalogues do:

===================  ==========================================  ==========================
                     OpenMetadata                                DataHub
===================  ==========================================  ==========================
container            storage service → container                 data platform → dataset
write                ``PUT`` an entity                           propose an aspect
identity             FQN, with ``bio://`` in ``fullPath``        URN, with ``bio://`` in
                                                                 ``qualifiedName``
lineage              one ``PUT`` per edge, in entity IDs         one aspect per downstream
                                                                 dataset, in URNs
ordering             service, then containers, then edges        anything, then lineage
===================  ==========================================  ==========================

The ordering line is the one that shows the models apart. OpenMetadata's lineage
API works in entity IDs, so the containers have to exist before an edge between
them can be described. DataHub's works in URNs, which are derived rather than
assigned, so an upstream can be named before it exists. The datasets are still
emitted first here — a graph whose nodes arrive after its edges is confusing to
watch — but nothing would break if they were not, and that is a real difference
rather than a stylistic one.
"""

from __future__ import annotations

from pathlib import Path

from bio_governance.catalog.datahub_client import DataHubClient
from bio_governance.catalog.datahub_mapping import dataset_urn, upstreams
from bio_governance.catalog.mapping import lineage_edges, prepare_assets
from bio_governance.catalog.models import PublishedCatalog
from bio_governance.catalog.publish import (
    DEFAULT_CONTRACT_DIR,
    LINEAGE_EVENTS,
    asset_sizes,
    lineage_run_id,
    load_contracts,
    study_id_from,
)
from bio_governance.models import AssetIdentifier


def publish_study_to_datahub(
    client: DataHubClient,
    raw_dir: Path,
    results_dir: Path,
    *,
    contract_dir: Path | None = None,
) -> PublishedCatalog:
    """Publish a study's seven governed assets and their lineage to DataHub.

    ``raw_dir`` is the generated study, ``results_dir`` the pipeline output that
    holds ``curated/``, ``quality/dq-report.json`` and ``lineage/``. Every file
    the catalogue will claim is checked first: a catalogue entry for a file that
    was never written is worse than no entry at all.

    Re-running against the same directories updates the same entities. The URNs
    are derived from the ``bio://`` identifiers rather than assigned by the
    server, and every proposal is an upsert, so a second run leaves seven
    datasets and six edges, not fourteen and twelve.
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

    platform = client.emit_platform()
    for asset in assets:
        client.emit_dataset(asset)
    for target, sources in upstreams(study_id).items():
        client.emit_upstreams(
            urn=_urn(target),
            upstream_urns=tuple(_urn(source) for source in sources),
        )
    # DataHub's REST emitter may batch, and a publication that has not reached
    # the server is not a publication. Nothing is reported until it has.
    client.flush()

    return PublishedCatalog(
        study_id=study_id,
        service=platform,
        assets=assets,
        edges=edges,
        lineage_run_id=run_id,
    )


def _urn(identifier: str) -> str:
    """The DataHub address of a ``bio://`` identifier, parsed rather than pasted."""
    return dataset_urn(AssetIdentifier.parse(identifier))
