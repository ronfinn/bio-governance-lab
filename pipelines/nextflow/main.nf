#!/usr/bin/env nextflow

/*
 * bio-governance-lab — contract-gated curation pipeline.
 *
 * Raw synthetic study -> contract gate -> curated output.
 *
 * The gate is the point of this pipeline. CURATE has no input that does not
 * come through CONTRACT_GATE_SAMPLES, which in turn runs only after
 * CONTRACT_GATE_COMPOUNDS, so a dataset that breaks its contract cannot reach
 * the curated directory. `bio-gov contract validate` exits non-zero on a
 * violation, Nextflow terminates the run, and nothing is published.
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

process CURATE {
    tag "${study}"
    publishDir "${params.outdir}/${study}", mode: 'copy'

    input:
    tuple val(study), path(samples), path(compounds), path(expression)

    output:
    path 'curated'

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

workflow {
    def study_dir = file(params.study_dir, checkIfExists: true)
    def study     = study_dir.name

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

    CURATE(
        samples_passed.map { s, _report -> tuple(s, samples, compounds, expression) }
    )
}
