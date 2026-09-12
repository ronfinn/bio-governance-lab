# OpenMetadata versus DataHub: a case study

This repository publishes the same governed study into two metadata catalogues.
Milestone 7 wrote the OpenMetadata integration; milestone 10 wrote the DataHub
one. Neither knows the other exists, and neither sits behind an interface. This
document is milestone 11: what the two implementations actually taught, read off
the code in `src/bio_governance/catalog/` rather than off either project's
website.

Everything here is a claim about *this* experiment. The last section says
plainly what it does not establish, which matters more than the rest.

Milestone 12 published each study's ownership and classification into both
catalogues. Section 10 records how differently the two modelled it; the counts
elsewhere in this document were re-measured after that change rather than left
at their milestone-11 values.

Milestone 13 ran the OpenMetadata integration's milestone-12 classification
against a live server for the first time, reproduced the reclassification
failure section 10 had predicted from source, and added the one request that
fixes it. The OpenMetadata claims
in section 10 are now observations, and the counts were re-measured again.

> **A note on "MCP".** DataHub abbreviates *Metadata Change Proposal* as MCP.
> This repository has a Model Context Protocol server, also MCP. They are
> unrelated; this document always writes Metadata Change Proposal in full.

## 1. Purpose and experimental setup

The two integrations are given identical inputs, deliberately:

| | |
| --- | --- |
| study | `BIO-001`, from the deterministic synthetic generator |
| assets | the same **seven**: three raw files, three curated copies, one quality report |
| identities | the same seven `bio://` URIs |
| lineage | the same **six** edges |
| evidence read | the same files, checked by the same code |
| pipeline | the same; neither catalogue is in `main.nf` |

The sameness is the instrument. `datahub_publish.py` imports `prepare_assets`
and `lineage_edges` from the OpenMetadata mapping, and imports
`study_id_from`, `asset_sizes`, `load_contracts` and `lineage_run_id` from
`publish.py`. That is not code-sharing for tidiness: two catalogues that
disagreed about which assets exist, or which files are present, would be
producing two different experiments and every difference below would be
uninterpretable. Only the *identity mapping* and the *transport* are allowed to
differ, so every difference this document records is attributable to the
catalogue rather than to the study.

What varies, then, is exactly one thing: what each catalogue believes a governed
file *is*, and how it wants to be told.

## 2. Metadata and entity modelling

Our assets are generated files. Neither catalogue has a first-class concept of
"a CSV a pipeline wrote", so each integration had to answer the modelling
question in that catalogue's own vocabulary.

**OpenMetadata** distinguishes the *storage* from the thing stored. Files are
**Containers**, and every Container belongs to a **StorageService**. The
integration registers one service, `bio_governance_lab`, of type
`CustomStorage` — the vocabulary's own answer for a store OpenMetadata has no
connector for. Calling it MySQL or Snowflake would have put a false statement in
the catalogue; `CustomStorage` says "files, pushed here, not crawled".

```
StorageService  bio_governance_lab  (CustomStorage)
└── Container   BIO-001_raw_samples
        fullPath   bio://BIO-001/raw/samples
        fileFormats [csv]
        dataModel.columns  ← the contract's declared columns
```

**DataHub** has no container entity at all. Anything with fields is a
**Dataset**, and a Dataset is addressed by *which platform produced it* and
*which environment it lives in*. So the modelling question changes shape: not
"what kind of store is this?" but "whose data is this?". The integration
registers a dedicated `bio_governance_lab` data platform rather than borrowing
`s3` or `file`, which would have said something untrue about provenance.

```
DataPlatform  urn:li:dataPlatform:bio_governance_lab
└── Dataset    BIO-001.raw.samples  (PROD)
        aspect datasetProperties   qualifiedName = bio://BIO-001/raw/samples
        aspect subTypes            ["Raw File"]
        aspect schemaMetadata      ← the contract's declared columns
```

**The consequence.** OpenMetadata's model gave a natural home for a store that
does not exist as a service — `CustomStorage` is an honest label for "local
pipeline output". DataHub's model gave a natural home for *provenance* — the
platform URN states who produced the file, which is closer to what governance
actually cares about — but it has no way to say "this is a file rather than a
table" except through a `subTypes` aspect carrying a free string. Both
accommodated the assets without distortion. Neither did so because it was
designed for this case; each simply had one axis that happened to fit.

The second consequence is granularity. An OpenMetadata Container is one entity
carrying its columns inline. A DataHub Dataset is a *URN with aspects hung off
it*, and the description, the lifecycle subtype and the schema are three
separate writes. That difference shows up again in lineage and in idempotence,
and it is the single most important structural difference between the two.

## 3. Canonical identity

Both integrations obey one rule, stated in `mapping.py` and restated in
`datahub_mapping.py`:

> `bio://` is the project's identity. A catalogue's address belongs to that
> catalogue.

| | OpenMetadata | DataHub |
| --- | --- | --- |
| canonical | `bio://BIO-001/raw/samples` | `bio://BIO-001/raw/samples` |
| derived name | `BIO-001_raw_samples` | `BIO-001.raw.samples` |
| catalogue address | `bio_governance_lab.BIO-001_raw_samples` | `urn:li:dataset:(urn:li:dataPlatform:bio_governance_lab,BIO-001.raw.samples,PROD)` |
| canonical carried in | `fullPath` | `qualifiedName` **and** `canonical_asset_id` |
| derivation | one-way | one-way |

Both derivations are pure functions of an `AssetIdentifier`, and neither is ever
inverted: nothing in this repository parses an FQN or a URN back into a
`bio://` URI. The canonical URI travels as *data* instead, so a person reading
either catalogue can recover the governance identity without knowing the rule.

The separators differ because the catalogues' naming rules differ — OpenMetadata
entity names may not contain `/` or a scheme, and DataHub splits a dataset name
on its platform delimiter to build browse paths, so the dotted form browses as
BIO-001 → raw → samples while a `bio://…` name would browse as `bio:` and an
empty segment. That is precisely the point: **two catalogues imposed two
different naming constraints on the same asset**. Had either identifier been
adopted as the project's identity, the other catalogue's constraints would have
had to be satisfied by a translation layer, and a rename in one deployment would
have been a rename of the asset itself.

This is the cleanest result of the whole exercise, and section 12 returns to it.

## 4. Write and API model

The two clients are not stylistic variants of each other. They are shaped by
what each server accepts.

**OpenMetadata — REST entities, `PUT` whole.** `client.py` speaks ten requests
over `httpx`, six of them writes. A write is a JSON body a person can read,
and `PUT` is create-or-update — except for a container's classification, which
a `PUT` can add but not replace (section 10), and which is therefore set by the
one `PATCH`:

```
PUT   /v1/services/storageServices      → the service, returns its FQN
PUT   /v1/classifications               → the classification vocabulary, returns its FQN
PUT   /v1/tags                          → one classification value, returns its FQN
PUT   /v1/containers                    → one container, without tags; returns its entity UUID
GET   /v1/containers/{id}?fields=tags   → the tags it holds now
PATCH /v1/containers/{id}               → one `add` of /tags: other labels kept, classification set
PUT   /v1/lineage                       → one edge, addressed by two entity UUIDs
GET   /v1/system/version                → health, deliberately unauthenticated
GET   /v1/containers/name/{fqn}         → read one container back, with tags and owners
GET   /v1/lineage/container/name/{fqn}  → read the lineage graph around it
```

**DataHub — aspects inside Metadata Change Proposals.** A write is not an
entity. It is a *proposal*: an entity URN, an aspect, and a change type
(`UPSERT`). The aspects are code-generated from Avro schemas, so
`datahub_client.py` constructs `DatasetPropertiesClass`, `SubTypesClass`,
`SchemaMetadataClass`, `UpstreamLineageClass` and — since milestone 12 —
`OwnershipClass`, `GlossaryTermsClass`, `GlossaryNodeInfoClass` and
`GlossaryTermInfoClass`, and hands each to the SDK's REST emitter.

The counts make the difference concrete. For one publication of BIO-001:

| | OpenMetadata | DataHub |
| --- | --- | --- |
| writes per publication | **26** — 19 `PUT`s and 7 `PATCH`es (plus 7 `GET`s) | **42** Metadata Change Proposals |
| made of | 1 service + 1 classification + 4 tags + 7×(container `PUT` + tag `GET` + classification `PATCH`) + 6 edges | 1 platform + 1 glossary node + 4 terms + 7×(properties + subtype + ownership + terms) + 4 schema + 4 lineage |

(At milestone 11, before any governance metadata was published, the same
publication was 14 `PUT`s and 23 proposals; at milestone 12 it was 19 `PUT`s
and 42 proposals.)

DataHub takes more writes for the same information because an aspect is the unit
of a write. That is not overhead for its own sake — it is what makes partial
updates safe, since re-sending a description cannot disturb a schema — but it
means "publish a dataset" is never one call.

**Why the implementation choices differ.** The two decisions look inconsistent
and are not; each was argued from what the API in front of it actually is:

- `openmetadata-ingestion` resolves to **135 packages** — dbt-core, boto3,
  grpcio, the Kubernetes client — to send five readable JSON bodies. The REST
  API *is* the readable interface, so the SDK buys nothing this project needs.
- `acryl-datahub` resolves to **65 packages**, and what it wraps is not a JSON
  API but a generated Avro schema. Hand-rolling those bodies would mean
  maintaining a private copy of a schema the SDK already holds correctly — the
  exact opposite trade.

So: REST for OpenMetadata, SDK for DataHub. The rule that produced both answers
is "use the thing that is actually the interface", not "prefer SDKs" or "prefer
REST".

**The asymmetry on reads is worth recording, because it runs the other way.**
OpenMetadata's REST responses arrive as `dict[str, Any]`, and its lineage graph
reports edges as entity UUIDs with the entities in a separate list — so `cli.py`
has to join nodes to edges by ID to print anything readable (`_downstream_names`
is fourteen lines of defensive dictionary work, every access guarded because the
shape is only a convention). DataHub's `get_aspect` returns a typed aspect
object, so `get_upstreams` reads the URNs straight off `aspect.upstreams`. The SDK that cost more
dependencies gave back type safety at the boundary; the REST client that cost
almost none left the caller holding untyped JSON. Neither effect was predicted
before the code was written.

## 5. Lineage

Same six edges, expressed in two genuinely different ways.

**OpenMetadata: one edge, one request.** `PUT /v1/lineage` takes a single
`{fromEntity, toEntity}` pair, addressed by the **server-assigned entity UUIDs**
returned when the containers were created. Two consequences follow directly:
the containers must exist before any edge can be described, and `publish.py`
must keep a map from `bio://` URI to UUID for the duration of the publication.
Six edges are six requests.

**DataHub: one downstream dataset, one aspect.** `upstreamLineage` is not an
edge — it is *the entire upstream list of one dataset*, addressed by URNs the
client derived itself. So the edges must be **grouped by their target** before
sending, which `datahub_mapping.upstreams()` does:

```
curated/samples     ← [raw/samples]          1 aspect
curated/compounds   ← [raw/compounds]        1 aspect
curated/expression  ← [raw/expression]       1 aspect
quality/dq-report   ← [raw/samples,          1 aspect
                       raw/compounds,
                       raw/expression]
                                             ─────────
                     6 edges, 4 aspects
```

This is the sharpest trap the exercise found. Sending the quality report's three
upstreams as three proposals would have produced **no error and a wrong
catalogue**: each write replaces the aspect, so the report would have ended with
exactly one upstream and a green publication log. A test asserts the grouping
(`test_lineage_is_proposed_as_one_aspect_per_downstream_dataset`, which checks
both the six-edge set and that there are four aspects), because the failure mode
is silent.

The mirror-image trap does not exist on the OpenMetadata side: an edge is
additive there, so the worst case of getting it wrong is a missing edge or a
duplicate one, both visible. But OpenMetadata pays for that with an ordering
constraint DataHub does not have — because DataHub URNs are derived rather than
assigned, a proposal may legally name an upstream that does not exist yet.

## 6. Idempotence

Publishing BIO-001 twice must leave seven assets and six edges. Both
implementations achieve it, and neither keeps a record of what it published —
there is no read-then-decide step and no local state anywhere in either path.
Since milestone 13, OpenMetadata's classification is written after a read of
the container's current tags, but the read decides only *which other labels to
keep*, never *whether* to write: the same `PATCH` goes out every time.

| | OpenMetadata | DataHub |
| --- | --- | --- |
| what makes the second run safe | `PUT` is create-or-update; the classification `PATCH` sets a value | `UPSERT` replaces the named aspect |
| what addresses the entity | FQN, derived from `bio://` | URN, derived from `bio://` |
| who assigns the primary key | the **server** (entity UUID) | the **client** (the URN *is* the key) |
| duplicate risk | none, while the FQN is deterministic | none, while the URN is deterministic |

The shared mechanism is the one that matters: **identity is derived, never
assigned.** Both catalogues will happily create a second entity if you hand them
a second name, so idempotence is a property of the *mapping* — a pure function
from `bio://` URI to catalogue address — and not of the transport, the API verb,
or any cleverness in the client.

The difference is where the server's authority begins. OpenMetadata still mints
an internal UUID, which the lineage API then requires; the FQN is a unique key
layered above it. DataHub has no separate identifier: the URN the client
computed is the primary key, which is why a DataHub write can reference an
entity the server has never seen.

Verified live for DataHub after three publications of BIO-001 (two by hand, one
by the live test): an SDK search of the platform returned **7 datasets, 7
unique**, with six upstream edges. Verified live for OpenMetadata in milestone
13 by `test_catalog_live.py` on the 1.13.4 quickstart: after two publications,
seven containers, one classification with four tags, six edges, and a read-back
identical in tags, owners, `fullPath` and entity version.

## 7. Dependency footprint

The project's whole lock file is 99 packages, so a catalogue SDK is a
significant fraction of it.

| package | packages a fresh resolve installs | used |
| --- | --- | --- |
| `httpx` | 7 | yes — the OpenMetadata client |
| `acryl-datahub` | 65 | yes — the DataHub client |
| `openmetadata-ingestion` | 135 | **no** |

The trade-offs, stated as trade-offs:

- **Avoiding `openmetadata-ingestion` cost almost nothing** because the REST API
  is documented, stable, and the same interface the SDK calls. The price paid is
  untyped responses and hand-written request bodies — six of them, the sixth a
  one-operation JSON Patch, small enough to read in one screen.
- **Adopting `acryl-datahub` cost 65 packages** and an import the CLI must defer
  (about 80 ms on a process that starts in about 230 ms, which is why
  `datahub_client.py` is imported inside the command functions and is not
  re-exported from `catalog/__init__.py`). It also cost one narrowing of mypy
  strictness: the generated aspect constructors carry no annotations, handled
  with `untyped_calls_exclude = ["datahub"]` rather than a `type: ignore`.
  What it bought is correctness insurance on a schema this project could not
  otherwise validate, plus typed reads.

Neither choice generalises into "SDKs are heavy" or "REST is simpler". The
honest generalisation is narrower: **the cost of an SDK should be weighed
against how much of the wire format you would otherwise have to own.** Six JSON
bodies is a small thing to own. A generated Avro aspect model is not.

## 8. Local developer experience

Both run locally in Docker; the experience differs more than the APIs do.

| | OpenMetadata | DataHub |
| --- | --- | --- |
| how it starts | download the release `docker-compose.yml`, `docker compose up -d` | `datahub docker quickstart --version v1.7.0` (the CLI ships with the SDK) |
| containers | 4 — MySQL, Elasticsearch, a migration job, the server | 7 — MySQL, OpenSearch, Kafka, a system-update job, GMS, frontend, actions |
| memory | 6 GB documented; runs in about 4 GB without the ingestion container | **hard refusal below 4.3 GB** of Docker memory; needed Docker raised from 3.9 GB to 6 GB |
| cold start | slow — Elasticsearch, then migrations, then the server | slow — images, then setup job, then a couple of minutes before GMS opens its port |
| auth, locally | a JWT is **required for every write**; minted from the UI or the login endpoint | **none by default**; a token is optional and only needed if the deployment enables it |
| health endpoint | `GET /v1/system/version`, unauthenticated by design | `GET /config`, via the SDK's graph client |
| UI | `:8585` | `:9002`; the API this project uses (GMS) is `:8080` |

Two observations that only come from running both:

- **OpenMetadata's mandatory local token is the larger friction**, and the
  reason `OpenMetadataConfig.require_token()` exists and raises a message naming
  the variable. DataHub's quickstart needed no credential at all, which made the
  first publication quicker — and is also, for the same reason, less like a
  production deployment.
- **DataHub asks for more machine.** Seven containers including Kafka against
  four, and a hard memory check that stopped the milestone until Docker was
  reconfigured. On an 8 GB laptop the two stacks cannot run at once, which is
  documented in `infra/datahub/README.md`. Milestone 13 met it from the other
  side: DataHub was holding about 4 GB of a 5.8 GB Docker VM, and its
  containers had to be stopped before OpenMetadata could be started.

The CLI surface was made identical on purpose, so that the *only* difference a
user meets is the catalogue:

```bash
bio-gov catalog openmetadata health | publish <raw> <results> | get <study>
bio-gov catalog datahub      health | publish <raw> <results> | get <study>
```

That symmetry is a property of the two command groups, not of a shared
abstraction underneath them — the modules behind them have almost nothing in
common, which is the point of section 11's last row.

**Test strategy** is where the API difference reappears:

| | OpenMetadata | DataHub |
| --- | --- | --- |
| what is faked | HTTP, with `respx` | the SDK's **emitter**, with a recording double |
| what is real in the test | the request bodies this project builds | the real `MetadataChangeProposalWrapper` and real aspect classes |
| mocked tests | 44 | 39 |
| live tests | 3, skipped unless `OPENMETADATA_INTEGRATION_TEST=1` | 2, skipped unless `DATAHUB_INTEGRATION_TEST=1` |

Because DataHub's boundary is an object rather than a socket, the double records
*proposals*, and the tests can assert on the aspect types, the change type and
the URNs without inventing a wire format. The corresponding OpenMetadata fake
has to impersonate a server — it keys containers by FQN and mints entity IDs —
precisely because the server owns the identifiers. Neither CI job starts a
catalogue.

## 9. Governance architecture

The most important result is what *did not* change when the second catalogue
arrived.

- The governance decision is computed by `bio-gov governance evaluate` from
  files on disk. It reads no catalogue, and `GovernanceReport.decision` is a
  computed field, so no catalogue can be the reason a study is READY.
- The governance metadata result, the contracts, the quality report, the
  OpenLineage events and the governance report are all produced by the pipeline
  and all survive both catalogues being offline. `main.nf` is unchanged by
  milestones 7 and 10 alike, milestone 12 changed it only to add a gate, and
  milestone 13's reclassification touched nothing outside `catalog/`, its tests
  and the documentation.
- Publication is an explicit post-run command in both cases. Nothing polls,
  reconciles or syncs, and a catalogue outage cannot fail a governed run.
- The MCP server exposes evidence read from disk. Neither catalogue is exposed
  through it.

So both catalogues are **projections of governed state, not owners of it**. This
was an assertion in milestone 7, when there was one projection and therefore no
way to test it. Milestone 10 tested it: a second catalogue, with a different
entity model, different identifiers, a different write protocol and different
lineage semantics, was added without changing a single line of the contract,
quality, lineage, governance, MCP or pipeline layers. The diff for milestone 10
touched `catalog/`, the CLI, tests, documentation and the dependency files —
nothing else.

That is the load-bearing evidence for section 12.

## 10. Governance metadata: one declaration, two projections

Milestone 12 gave each study a committed, validated declaration of its owner,
steward, contact and classification, and published it to both catalogues from
the same evidence, through the same helper (`governance_metadata()` in
`publish.py`, which refuses missing, failed or wrong-study evidence before the
first request in either). The catalogues then took different amounts of it.

| | OpenMetadata | DataHub |
| --- | --- | --- |
| classification vocabulary | a `Classification`, `mutuallyExclusive: true` | a glossary node |
| the four values | four `Tag`s | four glossary terms |
| on each asset | a tag label inside the container body | a `glossaryTerms` aspect |
| owner and steward | **not sent** | an `ownership` aspect: `BUSINESS_OWNER`, `DATA_STEWARD` |
| why | an owner is the UUID of a User or Group that must already exist | an owner is a URN the client derives; no account needed |
| a changed classification | replaced by one JSON Patch of `/tags` that keeps every other label (milestone 12: refused, because `PUT` merges tags, then enforces exclusivity) | replaced: the aspect is replaced whole |
| a tag or owner added in the UI | a tag survives a republish, deliberately | overwritten by a republish |

**Classification** fitted both, in each catalogue's own vocabulary. OpenMetadata
literally calls a controlled, mutually exclusive tag set a *Classification*.
DataHub reserves "controlled vocabulary for governance" for its glossary and
calls tags informal. Neither was made to imitate the other: the same four
values became tags in one and glossary terms in the other.

**Ownership** did not fit both, and the reason is section 6 again. OpenMetadata's
`EntityRepository.validateOwners` resolves each owner by a server-assigned UUID
and requires a User, or a Team of type `Group`, that already exists; the only
ways to satisfy it were to provision accounts or look up accounts a quickstart
does not have, and both are out of scope. DataHub let the client name a
principal it had never seen — verified on the local v1.7.0 quickstart, where the
ownership aspect was stored and read back while `exists()` stayed false for both
corpusers — exactly as it let lineage name an upstream before it existed.
*Who owns the primary key* decided how lineage was addressed in milestone 10; in
milestone 12 it decided whether ownership could be expressed at all.

The write semantics also invert, and both are the catalogue's model rather than
this project's choice. OpenMetadata *merges* tags on `PUT` (so manual tags
survive and a reclassification is refused, loudly), where DataHub *replaces*
aspects (so a reclassification simply lands, and a manually added owner is
silently overwritten). A shared "set classification" abstraction would have had
to pick one of those and lie about the other.

Milestone 12 read the OpenMetadata half of that from the 1.13.4 source.
Milestone 13 ran it: republishing `internal` was a no-op, a steward's tag
survived, and a validated `confidential` declaration published over `internal`
containers got HTTP 400 — "Tag labels bio_governance_classification.internal
and bio_governance_classification.confidential are mutually exclusive and can't
be assigned together" — with nothing changed on the server. The prediction was
right, and it exposed a distinction worth naming: **idempotence is not
convergence.** OpenMetadata's merging `PUT`
was perfectly idempotent for an unchanged declaration and could never reach a
changed one. Getting there took a request with *replace* semantics — the one
JSON Patch `add` of `/tags` — and, because a replace would also sweep away labels
this project does not own, a read of the current tags first, filtered to
everything outside `bio_governance_classification`. DataHub reached the same end
state with no change at all, because replacing is what its aspects already do;
the price is that DataHub also replaces what somebody added by hand, which
OpenMetadata's path now deliberately does not.

So the two paths are further apart after milestone 13 than before it, and that
is the honest result: OpenMetadata keeps others' tags and needs a read to do it;
DataHub needs no read and keeps nothing. See
[governance-metadata.md](governance-metadata.md) and
[openmetadata.md](openmetadata.md#classification-lifecycle) for the full
account.

## 11. Side-by-side decision matrix

| Dimension | OpenMetadata implementation | DataHub implementation |
| --- | --- | --- |
| Asset representation | `Container` under one `CustomStorage` `StorageService` | `Dataset` under one custom `DataPlatform`, env `PROD` |
| Canonical identifier mapping | `bio://` → entity name `BIO-001_raw_samples`, FQN `bio_governance_lab.BIO-001_raw_samples`; URI carried in `fullPath` | `bio://` → dataset name `BIO-001.raw.samples`, URN `urn:li:dataset:(…,PROD)`; URI carried in `qualifiedName` + `canonical_asset_id` |
| Publication mechanism | 19 REST `PUT`s, 7 `GET`s and 7 JSON Patches over `httpx` | 42 Metadata Change Proposals (`UPSERT` aspects) over the SDK's REST emitter |
| Classification | a tag of a mutually exclusive `Classification`, set by a `PATCH` of `/tags` that keeps every other label | a glossary term under a glossary node, in an aspect replaced whole |
| Ownership | not projected — owners must be existing users or Group teams | `ownership` aspect, owner and data steward, derived corpuser URNs |
| SDK requirement | none — REST called directly | required in practice; the aspect model is generated from Avro |
| Lineage representation | one `PUT /v1/lineage` per edge, addressed by server-assigned entity UUIDs; 6 edges = 6 requests | one `upstreamLineage` aspect per *downstream* dataset, addressed by URNs; 6 edges = 4 aspects |
| Idempotence mechanism | `PUT` is create-or-update on a deterministic FQN | `UPSERT` replaces the aspect at a client-derived URN |
| Local setup | release compose file, 4 containers, ~4 GB, JWT required for writes | `datahub docker quickstart`, 7 containers, hard 4.3 GB floor, no token needed |
| Dependency footprint | `httpx` (7 packages) | `acryl-datahub` (65 packages), lazily imported (~80 ms) |
| Implementation complexity | client 290 non-blank lines, mapping 255, publish 238; simple requests, untyped responses, server-assigned IDs to track | client 359 non-blank lines, mapping 175, publish 102 (helpers reused); more writes, typed reads, grouping required before send |
| Strength demonstrated by this lab | an honest home for files (`CustomStorage`), a readable wire format, and a near-zero dependency cost | client-derived URNs (no ID bookkeeping, order-independent writes) and typed aspects on both write and read |
| Limitation demonstrated by this lab | server-assigned entity UUIDs force ordering and an ID map; untyped JSON on reads pushes defensive parsing into the caller; a merging `PUT` cannot reclassify, so a second write model (JSON Patch, after a read) was needed; a token is mandatory even locally | an aspect replaces rather than merges, so grouped lineage is mandatory and getting it wrong fails **silently**; heavier to run and to depend on |

No scores, no ranking, and no benchmark — nothing here was timed under load or
measured for search quality.

## 12. What this exercise taught

**A governance layer should keep its own canonical identity and its own
evidence, and treat every catalogue as a projection.**

This project asserted that before it had grounds to. It now has grounds, and
they are specific:

1. **The two catalogues imposed incompatible naming constraints on the same
   asset.** `BIO-001_raw_samples` and `BIO-001.raw.samples` are both correct and
   neither is negotiable — OpenMetadata rejects the scheme and slashes, DataHub
   splits on its delimiter to build browse paths. A project whose identity was
   an FQN would have had to translate on the way into DataHub, and a rename in
   one deployment would have propagated into the governance record. Because
   `bio://` is the identity and both catalogue addresses are derived from it,
   each catalogue got a name it accepts and the governance record was untouched.

2. **The same six edges have different shapes in the two systems** — six
   additive writes versus four replacing aspects. Any common lineage abstraction
   would have had to pick one of those shapes and emulate the other, and the
   emulation of DataHub's semantics on top of an edge-at-a-time interface is
   exactly where the silent-truncation bug would have lived.

3. **Adding the second catalogue changed nothing below `catalog/`.** The
   milestone-10 commit touched 19 files: the `catalog/` package, `cli.py`,
   tests, documentation, and `pyproject.toml`/`uv.lock` for the new dependency.
   Not one line of `contracts/`, `quality/`, `lineage/`, `governance/`, `mcp/`
   or `pipelines/`. A layer that can absorb a second projection without moving
   is a layer that owns its state.

4. **Idempotence turned out to be a property of the mapping, not the protocol.**
   Two very different write models produced the same guarantee, from the same
   cause: a deterministic, one-way derivation of the catalogue's address from
   `bio://`. Nothing about `PUT` or `UPSERT` provides it.

**And the abstraction was right to skip.** A `CatalogAdapter` with `health()`,
`publish()` and `get()` was available at every moment of milestone 10 and would
have been actively harmful: those three method names are the *only* thing the
two implementations share. Behind them sit different entity models, different
identifier ownership, different write granularity, different lineage semantics,
different failure modes and different auth requirements. An interface is a claim
that the differences below it do not matter to the caller, and here every one of
them does. The shared code that *did* emerge was not an interface at all — it
was the evidence-reading in `publish.py` and the asset preparation in
`mapping.py`, both of which are about the *study*, which is genuinely the same
thing on both sides.

The narrower lesson for anyone choosing between them from this evidence alone:
prefer OpenMetadata's model if your assets are files and you want a small
dependency and a readable wire format; prefer DataHub's if you want client-owned
identity and a typed metadata model, and can afford the machine and the
dependency. That is a much smaller claim than "X is better than Y", and it is
the largest one this experiment supports.

## 13. What this comparison does **not** prove

This is one developer, one laptop, one synthetic study of seven small files and
six edges, published a handful of times into two local quickstart deployments.
It establishes nothing whatsoever about:

- **Enterprise-scale performance** — no load test, no concurrency, no volume.
  Seven assets is not a workload.
- **Search and discovery quality** — neither catalogue's search was evaluated at
  all; with seven entities there is nothing to find.
- **Production reliability** — no failover, restart, upgrade, backup, restore or
  data-migration testing. Both stacks were run in quickstart mode, which both
  projects say is for demonstration.
- **Organizational adoption** — no teams, no domains, no business glossary, no
  ownership or stewardship workflows. Milestone 12 publishes one synthetic owner,
  one synthetic steward and one classification per study; that shows how each
  model *holds* a declaration, and nothing about how either supports stewardship
  across an organisation.
- **Managed-cloud capabilities** — DataHub Cloud and Collate were not used or
  assessed. Only the open-source cores were compared.
- **Large-scale ingestion performance** — neither project's connector framework
  was used. This repository *pushes* metadata; it never asked either catalogue
  to crawl anything, which is the workload most real deployments care about.
- **Authorization sophistication** — policies, roles, column-level access and
  fine-grained permissions were not exercised. The DataHub quickstart ran with
  authentication off.
- **Total cost of ownership** — no operational history, no upgrade path, no
  support experience, no staffing model.

What it *does* establish is narrow and real: how two entity models accommodate
the same governed files, what each API costs to write against, how each defines
identity and idempotence, and — the part that generalises — that keeping the
governance layer's own identity and evidence is what made publishing to both
possible without either one distorting the other.

---

See [openmetadata.md](openmetadata.md) and [datahub.md](datahub.md) for each
integration on its own terms, and [architecture.md](architecture.md) for where
this conclusion sits among the project's other decisions.
