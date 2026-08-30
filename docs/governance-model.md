# Governance model

The premise of this project is that governance facts should be typed, tested and
version-controlled — the same treatment code gets — instead of living in a
spreadsheet that drifts away from the data it describes.

This document explains what each model means. For why it is built this way, see
[architecture.md](architecture.md).

## Asset

An `Asset` is a governed thing: a dataset, a table, a file, a model, a pipeline
or a report. It carries the metadata needed to answer the questions a governance
review actually asks — what is it, who is accountable for it, how sensitive is
it, where did it come from, what is it promised to look like, and is it fit to
use.

An asset is identified by an `AssetIdentifier`, and describes itself through
`AssetType`, `LifecycleStage` and `Classification`. It has `Ownership` and
`Provenance`, may reference a contract, and reports a `QualityStatus` and a
`GovernanceStatus`.

## AssetIdentifier

Identity is a URI:

```
bio://BIO-001/raw/samples
└┬┘   └──┬──┘ └────┬────┘
scheme  domain    path
```

- **domain** — a study or data-domain code, upper case: `BIO-001`.
- **path** — one or more lower-case segments, conventionally starting with the
  lifecycle stage: `raw/samples`, `curated/subjects`.

The identifier is the join key across everything that comes later: contracts,
quality results, lineage events and catalogue entries all refer to an asset by
this string. It has to be stable and unambiguous, so the format is validated
rather than assumed.

## AssetType

`dataset`, `table`, `file`, `model`, `pipeline`, `report`.

What kind of thing is being governed. Governance expectations differ by type —
a model and a table are not reviewed the same way.

## LifecycleStage

`raw` → `curated` → `derived` → `published` → `archived`.

How far the asset is from its source. Stage drives expectations: raw data is
allowed to be messy and is not for consumption; published data is not.

## Classification

`public`, `internal`, `confidential`, `restricted`.

Sensitivity of the contents, and the basis for access decisions. In this project
every asset is synthetic, but the classification is modelled honestly so the
controls being demonstrated are the real ones.

## Ownership

Two distinct roles, because conflating them is how assets end up unowned:

- **owner** — accountable for the asset existing and for decisions about it.
  Usually a team.
- **steward** — responsible for its day-to-day quality and correctness. Usually
  a person.
- **contact** — a route to reach them.

## Provenance

Where the asset came from: the `source_system`, what `generated_by` produced it,
`generated_at` when, and the `upstream` assets it derives from. The `synthetic`
flag records that the data is generated rather than real — true by default here,
and an explicit statement rather than an assumption.

`upstream` is the seed of lineage. It is recorded as identifiers now, and is the
natural place for OpenLineage to attach later.

## ContractReference

A pointer to the data contract an asset is expected to satisfy: `name`,
a semantic `version`, and an optional `location`. The contracts themselves are a
later milestone; the reference exists now so that an asset can already say which
promise it is meant to keep.

## QualityStatus

`unknown`, `passing`, `warning`, `failing`.

The result of the most recent quality evaluation. It defaults to `unknown` — an
asset that has not been checked must not look the same as one that passed.

## GovernanceStatus

`draft` → `under_review` → `approved` → `deprecated`.

Where the asset sits in its review cycle. It defaults to `draft`: an asset is
not approved merely because someone registered it.

## The intended shape of the argument

Quality status and governance status are separate on purpose. Data can be
technically clean and not approved for use; it can be approved and currently
failing its checks. Later milestones will compute these two from evidence —
quality from executed checks, governance from contract and review state — rather
than accepting them as assertions. The model is designed to make that
substitution possible without changing its shape.
