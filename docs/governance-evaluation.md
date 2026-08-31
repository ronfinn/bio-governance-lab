# Governance evaluation

Six milestones produced *evidence*. Contracts said whether each file conformed
to its declared structure. Data quality said whether the study hung together
across its files. The pipeline gated curation on both. OpenLineage recorded what
a successful run produced. OpenMetadata made the result discoverable.

None of that answers the question a data steward actually asks:

> May this study be used?

This layer answers it, from the evidence and nothing else.

## Deterministic code decides. AI explains.

This is the principle the whole layer exists to establish, and it is worth
stating before anything about how it works.

The decision is computed by ordinary, tested Python from files on disk. It has
no clock, no network call, no catalogue lookup, no randomness and no model. The
same results directory always produces the same verdict, and anybody can read
`evaluate.py` and predict it.

A later milestone may put a language model in front of a governance report — to
explain in prose what `lineage_evidence FAIL` means, to draft the note that goes
to the study team, to suggest what to fix first. That is a genuinely useful
thing for a model to do, and it is all it will be allowed to do. A model must
never calculate the verdict, and here it *cannot*:

```python
class GovernanceReport(BaseModel):
    study_id: str
    checks: tuple[GovernanceCheckResult, ...]

    @computed_field
    @property
    def decision(self) -> GovernanceDecision: ...
```

`decision` is a computed field. There is no attribute to assign, no constructor
argument that takes effect, and nothing a caller — human, script or model — can
hand in that makes a report claim `READY` while one of its checks fails. Passing
`decision=READY` alongside a failing check is not an error; it is simply
ignored, and the report still reads `BLOCKED`.

That is the difference between a governance system and a governance
*suggestion*. An explanation can be wrong and be corrected. A verdict that could
be wrong is not a verdict.

## READY, REVIEW, BLOCKED

Three values, and no number.

| Decision | Meaning |
| --- | --- |
| `READY` | Every check passed. The study may be used. |
| `REVIEW` | Nothing failed, but at least one check warned. A person should look before the study is relied on. |
| `BLOCKED` | At least one check failed. The study must not be used. |

The decision is derived from the check statuses, worst-first:

```
any FAIL          -> BLOCKED
else any WARN     -> REVIEW
else              -> READY
```

There is no governance score, for the same reason there is none in the quality
layer. A number needs weights nobody agreed on, and "0.82" does not tell anybody
whether they may use the data. Three words do.

`REVIEW` exists so that a finding worth recording does not have to choose
between blocking a pipeline and saying nothing at all. Today only the quality
layer can produce one, by warning; the other four checks are pass-or-fail
because a missing curated file or an incoherent lineage record is not a matter
of degree.

## The five checks

Each check reads one piece of evidence the pipeline published under
`results/<STUDY>/`.

| Check | Reads | PASS when |
| --- | --- | --- |
| `samples_contract` | `contracts/samples.contract.json` | the contract result says the dataset passed |
| `compounds_contract` | `contracts/compounds.contract.json` | the contract result says the dataset passed |
| `data_quality` | `quality/dq-report.json` | the quality report's overall status is PASS |
| `curated_outputs` | `curated/` | all three curated CSVs exist |
| `lineage_evidence` | `lineage/openlineage.jsonl` | the events describe one coherent curation run |

**The contract checks** deserialize the evidence back into the very
`ContractValidationResult` the validator produced and read its `passed`
property. The verdict is therefore the validator's own, not this layer's reading
of a JSON field it hoped would be there.

**The quality check** carries the data-quality verdict straight through:
`PASS → PASS`, `WARN → WARN`, `FAIL → FAIL`. A warning is not flattened into a
pass; carrying it is exactly what `REVIEW` is for.

**The curated-outputs check** asks whether `samples.csv`, `compounds.csv` and
`expression.csv` are actually on disk. A governed pipeline that reports success
without producing its outputs is the failure mode worth catching.

**The lineage check** is the strictest, because presence is not evidence. The
events have to be:

- exactly one `START` and one `COMPLETE`, and nothing else,
- sharing a single run ID,
- naming the `bio-governance-lab` / `curate-study` job,
- naming this study's three `bio://…/raw/…` datasets among their inputs, and
- naming this study's three `bio://…/curated/…` datasets among their outputs.

Two events from different runs, or provenance that names another study, describe
something other than this curation, and a governance layer that accepted them
would be certifying a file it cannot actually trace.

Not implemented, deliberately: ownership, classification, retention, access
control and catalogue availability. Each would be a real governance rule, and
this project has no evidence for any of them yet. A check that reads nothing is
a check that always passes, which is worse than an absent one.

## The CLI

```bash
uv run bio-gov governance evaluate results/BIO-001
```

```
Study: BIO-001
Decision: READY

PASS  samples_contract
PASS  compounds_contract
PASS  data_quality
PASS  curated_outputs
PASS  lineage_evidence
```

Delete the provenance and the verdict changes:

```
Study: BIO-001
Decision: BLOCKED

PASS  samples_contract
PASS  compounds_contract
PASS  data_quality
PASS  curated_outputs
FAIL  lineage_evidence    lineage evidence is missing: results/BIO-001/lineage/openlineage.jsonl
```

Turn one quality finding into a warning and it asks for a person instead:

```
Study: BIO-001
Decision: REVIEW

PASS  samples_contract
PASS  compounds_contract
WARN  data_quality        data quality WARN: compound_coverage
PASS  curated_outputs
PASS  lineage_evidence
```

`--json-out` writes the structured report, decision included:

```bash
uv run bio-gov governance evaluate results/BIO-001 \
  --json-out results/BIO-001/governance/governance-report.json
```

```json
{
  "study_id": "BIO-001",
  "checks": [
    {"check_id": "samples_contract", "status": "pass", "message": "bio.samples@1.0.0 passed over 20 rows"},
    …
  ],
  "decision": "ready"
}
```

### Exit codes

| Status | Meaning |
| --- | --- |
| `0` | `READY` |
| `1` | `REVIEW` or `BLOCKED` |
| `2` | the results directory itself cannot be read as a study's evidence |

Note where the line falls. A missing `dq-report.json`, an unparseable contract
result, an absent curated file, incoherent lineage — none of these is an error.
They are all governance failures, reported as a `FAIL` check and a `BLOCKED`
decision. Absent evidence is precisely the case a governance layer exists to
catch, and one that crashed instead of returning `BLOCKED` would have failed at
its job.

Status `2` is reserved for the one situation with no verdict to give: the
directory is not there, or it is not named for a study, so there is nothing to
issue a decision *about*.

## Structured contract evidence

The contract gates previously published only a human-readable report. They now
publish both:

```
results/BIO-001/contracts/
    compounds.contract.txt     what a person reads in the run log
    compounds.contract.json    the ContractValidationResult itself
    samples.contract.txt
    samples.contract.json
```

`bio-gov contract validate --json-out` writes the existing
`ContractValidationResult` — no second result model was introduced, so the
evidence and the validator cannot drift apart. `passed` is a computed field on
that model, so the JSON states its own verdict rather than leaving a reader to
re-derive it from the violation list.

Both artefacts are written before the exit status is decided, exactly as the
quality report is. A gate that withheld its evidence on the way out would leave
the governance layer with nothing to evaluate in the one case that matters most.

## In the pipeline

`EVALUATE_GOVERNANCE` runs last:

```
CONTRACT_GATE_COMPOUNDS -> CONTRACT_GATE_SAMPLES -> RUN_DATA_QUALITY
    -> CURATE -> EMIT_OPENLINEAGE -> EVALUATE_GOVERNANCE
```

It joins the output channels of every process before it, so it cannot run until
all of that evidence exists — the same structural argument the earlier gates
rest on, rather than a check inside a step. The process reassembles a results
directory in its work directory from the staged evidence, names it after the
study, and runs `bio-gov governance evaluate` against it. The report is
published to `results/<STUDY>/governance/governance-report.json`.

A clean run therefore ends with:

```
results/BIO-001/
    contracts/{samples,compounds}.contract.{txt,json}
    quality/dq-report.json
    curated/{samples,compounds,expression}.csv
    lineage/openlineage.jsonl
    governance/governance-report.json
```

In practice a pipeline run that reaches `EVALUATE_GOVERNANCE` is always `READY`:
the earlier gates already stopped everything that would have failed a governance
check. That is the point. The evaluator is not there to catch the pipeline out —
it is there so the verdict is a first-class, re-checkable artefact rather than
an inference from the fact that Nextflow exited zero. Run it again next month
against the same results directory, after somebody has deleted a curated file,
and it says `BLOCKED`.

## What is deferred

No policy engine, no rule language, no Rego, no YAML-defined policies. The check
vocabulary is a closed enum, exactly like the contract rules and the quality
checks, because every finding has to be a named identifier something downstream
can act on. Five checks do not need an engine, and building one before the sixth
check exists would be guessing at what it needs.

No approval workflow, no sign-off, no history and no stored decisions. A report
describes one evaluation of one results directory.

No catalogue requirement. Whether "is this discoverable in OpenMetadata?" should
be a governance rule is a real question, and it is not answered here — the
pipeline must keep running with OpenMetadata switched off.

And no model. That is the point of the milestone.
