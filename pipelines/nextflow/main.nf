#!/usr/bin/env nextflow

/*
 * bio-governance-lab — governance-gated curation pipeline.
 *
 * Raw synthetic study -> contract gates -> data-quality gate -> curated output
 * -> OpenLineage provenance evidence.
 *
 * The gates are the point of this pipeline. CURATE has no input that does not
 * come through RUN_DATA_QUALITY, which runs only after CONTRACT_GATE_SAMPLES,
 * which runs only after CONTRACT_GATE_COMPOUNDS. So a study that breaks a
 * contract or fails a quality check cannot reach the curated directory: the
 * `bio-gov` command exits non-zero, Nextflow terminates the run, and nothing
 * downstream is published.
 *
 * The two gates ask different questions. A contract asks whether one file
 * conforms to its declared structure; data quality asks whether the study as a
 * whole is consistent and usable. Deleting the vehicle controls from
 * samples.csv passes every contract and fails the quality gate.
 *
 * EMIT_OPENLINEAGE sits at the end for the same structural reason: it consumes
 * CURATE's output directory, so provenance is only ever claimed for a curated
 * directory that exists. A stopped run emits nothing — lineage for failed runs
 * is a later milestone.
 */

nextflow.enable.dsl = 2

process CONTRACT_GATE_COMPOUNDS {
    tag "${study}"
    publishDir "${params.outdir}/${study}/contracts", mode: 'copy'

    input:
    tuple val(study), path(compounds), path(contract)

    output:
    tuple val(study), path('compounds.contract.txt')

    script:
    """
    set -o pipefail
    echo "CONTRACT GATE compounds: ${compounds} against ${contract}"
    ${params.bio_gov} contract validate ${contract} ${compounds} | tee compounds.contract.txt
    """
}

process CONTRACT_GATE_SAMPLES {
    tag "${study}"
    publishDir "${params.outdir}/${study}/contracts", mode: 'copy'

    input:
    tuple val(study), path(samples), path(compounds), path(contract)

    output:
    tuple val(study), path('samples.contract.txt')

    script:
    // compounds.csv is staged beside samples.csv so the contract's foreign key
    // resolves the way it does on disk: to a bare sibling file name.
    """
    set -o pipefail
    echo "CONTRACT GATE samples: ${samples} against ${contract}"
    ${params.bio_gov} contract validate ${contract} ${samples} | tee samples.contract.txt
    """
}

process RUN_DATA_QUALITY {
    tag "${study}"
    publishDir "${params.outdir}/${study}/quality", mode: 'copy'

    input:
    tuple val(study), path(study_json), path(samples), path(compounds), path(expression)

    output:
    tuple val(study), path('dq-report.json')

    script:
    // All four files are staged here, so the work directory *is* the study
    // directory the checks read. The JSON report is written before the exit
    // status is decided, so a failing run still leaves its evidence behind.
    """
    echo "DATA QUALITY GATE: ${study}"
    ${params.bio_gov} dq run . --json-out dq-report.json
    """
}

process CURATE {
    tag "${study}"
    publishDir "${params.outdir}/${study}", mode: 'copy'

    input:
    tuple val(study), path(samples), path(compounds), path(expression)

    output:
    tuple val(study), path('curated')

    script:
    // Deliberately trivial: the governance decision has already been made by
    // the time anything reaches here, and inventing a transformation would
    // only obscure it.
    """
    mkdir curated
    cp ${samples}    curated/samples.csv
    cp ${compounds}  curated/compounds.csv
    cp ${expression} curated/expression.csv
    """
}

process EMIT_OPENLINEAGE {
    tag "${study}"
    publishDir "${params.outdir}/${study}", mode: 'copy'

    input:
    tuple val(study), path(raw), path(curated), path(dq_report)

    output:
    tuple val(study), path('lineage')

    script:
    // The raw directory is staged under its own name, so the study identifier
    // the events carry is read from the data rather than passed in. `curated`
    // arrives on CURATE's output channel, which is what makes this process
    // unreachable for a run that was stopped at a gate.
    """
    echo "LINEAGE: ${study}"
    ${params.bio_gov} lineage emit ${raw} ${curated} \\
        --quality-report ${dq_report} \\
        --output lineage/openlineage.jsonl
    """
}

workflow {
    def study_dir = file(params.study_dir, checkIfExists: true)
    def study     = study_dir.name

    def study_json = file("${study_dir}/study.json",     checkIfExists: true)
    def samples    = file("${study_dir}/samples.csv",    checkIfExists: true)
    def compounds  = file("${study_dir}/compounds.csv",  checkIfExists: true)
    def expression = file("${study_dir}/expression.csv", checkIfExists: true)

    def samples_contract   = file(params.samples_contract,   checkIfExists: true)
    def compounds_contract = file(params.compounds_contract, checkIfExists: true)

    log.info "study      : ${study} (${study_dir})"
    log.info "contracts  : ${compounds_contract.name}, ${samples_contract.name}"
    log.info "outdir     : ${params.outdir}"

    def compounds_passed = CONTRACT_GATE_COMPOUNDS(
        Channel.of(tuple(study, compounds, compounds_contract))
    )

    def samples_passed = CONTRACT_GATE_SAMPLES(
        compounds_passed.map { s, _report -> tuple(s, samples, compounds, samples_contract) }
    )

    def quality_passed = RUN_DATA_QUALITY(
        samples_passed.map { s, _report -> tuple(s, study_json, samples, compounds, expression) }
    )

    def curated = CURATE(
        quality_passed.map { s, _report -> tuple(s, samples, compounds, expression) }
    )

    EMIT_OPENLINEAGE(
        quality_passed.join(curated).map { s, report, dir -> tuple(s, study_dir, dir, report) }
    )
}
