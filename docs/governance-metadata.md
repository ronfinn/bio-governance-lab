# Governance metadata: ownership and classification

Milestone 12. The domain model has carried `Ownership` and `Classification`
since milestone 1, and the governance evaluation deliberately did not check
either: nothing produced evidence for them, and a check that reads nothing
always passes. This milestone supplies the evidence — one committed declaration
per study — validates it, gates the pipeline on it, adds two checks to the
decision, and projects it into both catalogues.

It is not a policy system. There is no rule language, no notion of what a
classification *permits*, no approval workflow and no directory of people.

## Where it lives

```
governance/studies/
    BIO-001.yaml
    BIO-002.yaml
    BIO-003.yaml
```

One file per study, named for it, committed. `BIO-001` is the demonstration
study. `BIO-002` and `BIO-003` are the README's two broken-data demonstrations;
they carry declarations so that those runs still stop at the contract gate and
the quality gate respectively, rather than one gate earlier for want of a
declaration.

## The format

```yaml
study_id: BIO-001
classification: internal
ownership:
  owner: Avery Example
  steward: Jordan Example
  contact: bio-001-governance@example.org
```

Three fields, and nothing else is accepted.

| Field | Type | Meaning |
| --- | --- | --- |
| `study_id` | a study code, `BIO-001` | the study this declaration is about |
| `classification` | `public`, `internal`, `confidential` or `restricted` | sensitivity of the study's contents |
| `ownership.owner` | a name | accountable for the study existing and for decisions about it |
| `ownership.steward` | a name | responsible for its day-to-day quality and correctness |
| `ownership.contact` | an email address | a route to reach them |

Nothing here is new vocabulary. `ownership` validates into the domain model's
existing `Ownership` and `classification` into its existing `Classification`
enum, so the project still has exactly one ownership model and one
classification vocabulary (`src/bio_governance/models/`). `contact` is present
only because `Ownership` requires it; the shipped files use `example.org`, a
domain reserved for documentation, so the address cannot reach anybody. The
names are invented. There are no teams, HR or directory identifiers, escalation
chains or hierarchy, because nothing in this project would read them.

The model is `GovernanceMetadata` in `src/bio_governance/governance/metadata.py`,
and it is closed (`extra="forbid"`). So, now, is `Ownership`: a
`retention_days` at the top level or a `team` inside `ownership` is a problem,
not something silently ignored. A field nothing checks would make the file look
like evidence for something it is not.

## What is valid evidence

`bio-gov governance metadata validate` checks a declaration against a study
directory, and reports every problem in one pass, each attributed to the part
of the declaration it is about:

| Problem | Attributed to |
| --- | --- |
| not valid YAML, empty, not a mapping | `declaration` |
| a top-level field other than the three | `declaration` |
| `study_id` missing, not a study code, or not the study being checked | `study_id` |
| `ownership` missing, a field missing, an unknown field, an invalid address | `ownership` |
| an owner or steward that names nobody (`"  "`) | `ownership` |
| `classification` missing, or not one of the four values (case matters) | `classification` |

The study is taken from the study directory's name, as every other layer takes
it, so a perfectly well-formed declaration of `BIO-002` is refused when checked
against `data/raw/BIO-001`. The result carries `ownership` and `classification`
separately, each only when that part validated: a declaration whose
classification is wrong still says who owns the study.

## Not a data contract

Both are YAML, and that is the whole of the resemblance. The declaration shares
no code with `contracts/`.

| | Data contract | Governance declaration |
| --- | --- | --- |
| is about | one file's structure | one study's responsibility and sensitivity |
| is applied to | a CSV, row by row | nothing — it *is* the thing under test |
| lives in | `contracts/*.yaml` | `governance/studies/<STUDY>.yaml` |
| vocabulary | columns, types, `required`, `unique`, … | `study_id`, `ownership`, `classification` |
| result | `ContractValidationResult` | `MetadataValidationResult` |

## The command and its exit codes

```bash
uv run bio-gov governance metadata validate governance/studies/BIO-001.yaml \
  --study-dir data/raw/BIO-001 \
  --json-out results/BIO-001/metadata/governance-metadata.json
```

```
Governance metadata: governance/studies/BIO-001.yaml
Study: BIO-001

PASS
Owner: Avery Example
Steward: Jordan Example
Contact: bio-001-governance@example.org
Classification: internal
```

A declaration with an unknown classification, a missing steward and an
undefined field:

```
FAIL

3 problems

declaration     unsupported field 'retention_days': a declaration holds only study_id, ownership, classification
ownership       ownership.steward: Field required
classification  classification 'secret' is not one of public, internal, confidential, restricted
```

| Exit | Meaning |
| --- | --- |
| `0` | the declaration is valid for this study |
| `1` | it is not — including malformed YAML, which is a defect *in the evidence* |
| `2` | there is nothing to judge: the declaration file does not exist or cannot be read, or the study directory is missing or not named for a study |

`--json-out` writes the `MetadataValidationResult` before the exit status is
decided, pass or fail, as every other layer does. `passed` is a computed field:
editing the JSON to say `"passed": true` beside a list of problems changes
nothing, because the evidence is always read back into the model
(`test_evidence_edited_to_claim_it_passed_still_fails`).

## Where it sits in the pipeline

```
GOVERNANCE_METADATA_GATE
  -> CONTRACT_GATE_COMPOUNDS -> CONTRACT_GATE_SAMPLES -> RUN_DATA_QUALITY
    -> CURATE -> EMIT_OPENLINEAGE -> EVALUATE_GOVERNANCE
```

First, and structurally: `CONTRACT_GATE_COMPOUNDS` consumes the gate's output
channel, so nothing downstream can start until it has passed, and
`EVALUATE_GOVERNANCE` joins its evidence with everything else's. It is first
because it is a question about the study rather than its files. Classification
is what decides how data may be handled, and an owner is who answers for it; a
study nobody has declared responsibility for is not copied into `curated/`,
however well-formed its files are.

The declaration is read from `--governance_dir` (default
`governance/studies/`) as `<STUDY>.yaml`. It is deliberately *not* opened with
`checkIfExists`: a missing declaration is a governance finding, and it is
reported by the gate that owns the question — `bio-gov` exits 2 inside
`GOVERNANCE_METADATA_GATE` — rather than by Nextflow refusing to start. An
invalid one exits 1 there. Either way `errorStrategy = 'terminate'` stops the
run and no `contracts/`, `quality/`, `curated/`, `lineage/` or `governance/`
directory is published. A passing gate publishes
`results/<STUDY>/metadata/governance-metadata.{txt,json}`.

Tested for real under Nextflow:
`test_a_study_without_a_governance_declaration_stops_at_the_first_gate` and
`test_an_invalid_governance_declaration_stops_at_the_first_gate`.

## What it does to the decision

Two checks join the five, both reading `metadata/governance-metadata.json`:

| Check | PASS when |
| --- | --- |
| `ownership` | the evidence is for this study and nothing is wrong with the declaration as a whole, its `study_id` or its `ownership` |
| `classification` | the evidence is for this study and nothing is wrong with the declaration as a whole, its `study_id` or its `classification` |

| Situation | `ownership` | `classification` | Decision |
| --- | --- | --- | --- |
| valid declaration | PASS | PASS | as the other five allow |
| evidence missing or unreadable | FAIL | FAIL | `BLOCKED` |
| evidence judged against another study | FAIL | FAIL | `BLOCKED` |
| malformed YAML, unknown field, wrong `study_id` | FAIL | FAIL | `BLOCKED` |
| invalid classification only | PASS | FAIL | `BLOCKED` |
| missing steward only | FAIL | PASS | `BLOCKED` |

There is no `WARN` from either check and no score. The decision is still
derived — any `FAIL` gives `BLOCKED` — so a study whose governance metadata is
missing or invalid cannot be `READY` however clean its data is
(`test_missing_governance_metadata_cannot_be_ready`).

Neither check asks what a classification allows. A `restricted` study is exactly
as `READY` as a `public` one (`test_a_restricted_study_is_as_ready_as_a_public_one`):
the claim is that responsibility and sensitivity were declared, validly, for
this study. What each class permits is a policy question, and answering it would
need a policy engine this project does not have.

## Projection into the catalogues

```
governance/studies/BIO-001.yaml          (canonical, committed)
            ↓  GOVERNANCE_METADATA_GATE
results/BIO-001/metadata/governance-metadata.json   (validated evidence)
            ↓  deterministic mapping
       ↙                        ↘
OpenMetadata                   DataHub
```

Publication reads the *evidence*, not the YAML, so a catalogue can only ever
carry a declaration a gate has judged. `governance_metadata()` in
`catalog/publish.py` is shared by both publications and refuses, before the
first request, evidence that is missing, did not validate, or describes another
study (`test_publication_refuses_governance_metadata_it_cannot_rely_on`, three
cases, in both catalogue test files). Publication remains an explicit post-run
command; neither catalogue is in `main.nf`, and both can be offline while the
pipeline validates the declaration and reaches a decision.

The two catalogues were allowed to model the same evidence differently, and do.

### OpenMetadata

**Classification — projected.** OpenMetadata's own word for a controlled,
mutually exclusive tag vocabulary is a *Classification*. The project's four
values become the four tags of one `bio_governance_classification`, created
with `mutuallyExclusive: true` — OpenMetadata's distinction between
*classifying* an entity and *categorising* it — and each container carries the
declared value as a tag label:

```
PUT   /v1/classifications   bio_governance_classification   mutuallyExclusive: true
PUT   /v1/tags              public | internal | confidential | restricted
PUT   /v1/containers        ... no tags
GET   /v1/containers/{id}?fields=tags
PATCH /v1/containers/{id}   [{"op": "add", "path": "/tags", "value": [...other labels...,
                              {"tagFQN": "bio_governance_classification.internal",
                               "source": "Classification", "labelType": "Manual",
                               "state": "Confirmed"}]}]
```

All four tags are created, not only the one in use: a mutually exclusive
classification with one member would not say what the alternatives are. Tags
must exist before a container can carry one, so publication runs service,
classification, tags, then each container's `PUT` and classification, then
edges. (Milestone 12 sent the tag label inside the container `PUT`; milestone 13
moved it into the `PATCH`, for the reason under *Write semantics* below.)

**Ownership — deliberately not sent.** An OpenMetadata owner is an
`EntityReference` whose `id` is required: the server-assigned UUID of a User,
or of a Team of type `Group`, that already exists. `EntityRepository.validateOwners`
looks each one up by that ID and rejects anything else. The declaration names
people; giving OpenMetadata an owner would mean creating accounts for them (user
provisioning) or looking up accounts a local quickstart does not have. Both are
outside this project. Two workarounds were considered and rejected:

- *A sentence in the container description.* On a `PUT` from a bot token,
  `EntityRepository` reverts a change to a non-empty description, so the
  sentence would silently not update on any container that already exists.
- *A custom property.* It needs the deployment's `container` type changed, and
  would put a second "owner" field in the catalogue beside OpenMetadata's own,
  empty, Owners field — two answers to one question.

So ownership stays canonical in the declaration and absent from OpenMetadata,
and `catalog openmetadata publish` says so in its summary.

**Write semantics.** On a `PUT`, OpenMetadata *merges* the request's tags into
those the container already holds and then enforces mutual exclusivity.
Milestone 12 read that from the 1.13.4 source and predicted three consequences;
milestone 13 observed all three on the live server. Republishing the same
classification is a no-op. A tag somebody added in the UI survives. And a
*changed* classification is refused — a `confidential` declaration published
over `internal` containers got HTTP 400, "Tag labels
bio_governance_classification.internal and
bio_governance_classification.confidential are mutually exclusive and can't be
assigned together", and nothing on the server changed.

That separates **initial classification**, which a merge can do, from
**reclassification**, which it cannot. So the container `PUT` no longer carries
tags, and the classification is set by a JSON Patch that *replaces* the tag
list: every label outside `bio_governance_classification` exactly as the server
returned it, then the declared value. The old value is removed in the same
request that adds the new one; `PII`, `Tier`, glossary terms and a steward's
tags are left alone, because the project owns only its own classification's
namespace. The same `PATCH` is sent whether or not anything changed, and a
`PATCH` to the tags a container already holds was observed to be a no-op, so
publication stays idempotent without deciding anything from the catalogue's
state. The five transitions — `none → internal`, `internal → internal`,
`internal → confidential`, `confidential → restricted`, `restricted → public` —
were each published twice against the live server with a steward's tag in
place; [openmetadata.md](openmetadata.md#classification-lifecycle) has the
table.

### DataHub

**Classification — a glossary term.** DataHub's documentation describes tags as
"informal, loosely controlled labels" and glossary terms as "a controlled
vocabulary … for governance", and DataHub's own classifier applies glossary
terms. So the vocabulary is a glossary node, `bio_governance_classification`,
with four terms, `urn:li:glossaryTerm:bio_governance_classification.internal`
and so on, each with a `glossaryTermInfo`; each dataset carries a
`glossaryTerms` aspect naming the declared one. The terms are stamped the way
DataHub's own SDK stamps them — actor `urn:li:corpuser:__ingestion`, time `0`
(`datahub/sdk/_shared.py`) — so no clock enters a proposal and two publications
of one declaration send identical aspects.

**Ownership — the `ownership` aspect.** The owner becomes a `BUSINESS_OWNER`
and the steward a `DATA_STEWARD`: DataHub has a steward role of its own, where
OpenMetadata has only "owners". Each person is a corpuser URN built by the SDK's
`make_user_urn(name)`, which is also what DataHub's SDK does with a bare owner
name. No user is created. DataHub accepts an owner URN for a user it has never
seen, exactly as it accepts an upstream dataset it has never seen, because the
client derives the URN. Verified against the local v1.7.0 quickstart: the
ownership aspect is stored and read back, and `exists()` is false for both
corpusers.

The declaration does not say whether a name is a person or a team, and saying
so would be the team structure this milestone excludes. A name is projected the
way DataHub itself projects a bare name, as a user; for an owner that is really
a team, that is imprecise, and it is the price of not modelling teams. The
`contact` address is not projected: it belongs to a user's profile, and no
profile is created.

**Write semantics.** An `UPSERT` replaces the whole `ownership` or
`glossaryTerms` aspect. A reclassification therefore simply replaces the term,
and a changed declaration replaces the owners — including an owner somebody
added by hand in the DataHub UI. That is what "the catalogue is a projection"
means in DataHub's model.

### Side by side

| | OpenMetadata | DataHub |
| --- | --- | --- |
| classification vocabulary | one `Classification`, mutually exclusive | one glossary node |
| the four values | four `Tag`s | four glossary terms |
| on each asset | a tag label in the container body | a `glossaryTerms` aspect |
| owner | **not sent** — needs an existing User or Group by UUID | `BUSINESS_OWNER` in the `ownership` aspect |
| steward | **not sent** — no steward role on containers | `DATA_STEWARD` in the `ownership` aspect |
| principal | must be provisioned first | a URN the client derives; no account |
| contact | not sent | not sent |
| republish, same declaration | tag list re-set to itself: no change | aspects replaced: no change |
| republish, changed classification | old tag replaced by one `PATCH` of `/tags` (milestone 12: refused by the `PUT`) | the term is replaced |
| a tag or owner added in the UI | a tag survives a republish | overwritten by a republish |
| governance requests per publication | 5 `PUT`s (1 + 4), 7 `GET`s, 7 `PATCH`es | 19 more proposals (1 + 4 + 7 + 7) |

One publication of BIO-001 is now **33** requests to OpenMetadata (1 service, 1
classification, 4 tags, 7 × container `PUT`, tag `GET` and classification
`PATCH`, 6 edges — 26 of them writes) and **42** Metadata Change Proposals to
DataHub (1 platform, 1 glossary node, 4 terms, 7 × properties, subtype,
ownership and terms, 4 schemas, 4 lineage aspects). Both counts are asserted in
the idempotence tests. (Milestone 12's OpenMetadata count was 19 `PUT`s.)

This is the milestone-11 finding reappearing in governance. Who owns a primary
key decided how lineage was addressed; here it decides whether ownership can be
expressed at all. DataHub lets a client *name* a principal, so ownership
projects without provisioning anyone. OpenMetadata needs the principal to exist
first, so it does not.

## What remains canonical

`governance/studies/<STUDY>.yaml` is the record, and the validated
`metadata/governance-metadata.json` beside a run is the evidence of it. Neither
catalogue is authoritative for either value, and neither is ever read back to
decide anything: the governance evaluation reads files on disk only, and the
pipeline reaches its decision with both catalogues switched off. Everything a
catalogue holds about a study's ownership or classification is a derivation
that can be regenerated from the declaration — and, in OpenMetadata's case,
half of it is not there at all, which costs the governance record nothing.

## What was verified, and how

- The validator, the two checks, the refusals and both projections: the normal
  test suite, with neither catalogue running (`tests/test_governance_metadata.py`,
  `tests/test_governance.py`, `tests/test_catalog.py`,
  `tests/test_catalog_datahub.py`).
- The pipeline gate: real Nextflow runs in `tests/test_pipeline.py`.
- DataHub: `tests/test_catalog_datahub_live.py` against the local v1.7.0
  quickstart, which now also asserts the owners and the glossary term on every
  dataset after two publications.
- OpenMetadata: mocked only in milestone 12, then run live in milestone 13
  against the local 1.13.4 quickstart. `tests/test_catalog_live.py` asserts the
  classification tag, `"owners": []` read back with owners requested (milestone
  12's version read containers with `fields=tags` only, and OpenMetadata then
  omits `owners` entirely, so that assertion could not have failed), an
  identical read-back after a second publication, and the five-transition
  reclassification walk with a steward's tag preserved throughout. Tag merging on
  `PUT`, mutual exclusivity and the refused reclassification are therefore now
  observed, not inferred. Owners-by-UUID and a bot's description revert are
  still read from 1.13.4's `EntityRepository.java`: nothing this project sends
  exercises them.

## Deliberately not done

Retention, access control and catalogue presence are still not checks. The one
OpenMetadata `PATCH` sets the classification and nothing else — no general
patching, diffing or reconciliation. There is no user, team or group entity in
either catalogue, no DataHub domains, tags, structured properties or custom
ownership types, no notion of what a classification permits, and no approval,
notification or stewardship workflow.
