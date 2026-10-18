from copy import deepcopy


EVIDENCE_REPORT_SCHEMA_VERSION = 1


def build_evidence_report(
    *,
    run_id: str,
    candidate: dict | None,
    factor_evaluation: dict,
    model_evaluation: dict,
    incremental_evaluation: dict | None,
    evaluation_mode: str,
    incremental_status: str,
    baseline: dict | None,
    data: dict,
    model: dict,
    bootstrap: dict,
) -> dict:
    """Assemble observed experiment outputs without research policy."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id is required")
    if candidate is not None:
        if not isinstance(candidate, dict):
            raise TypeError("candidate must be a mapping or None")
        if not candidate.get("source_hash"):
            raise ValueError("candidate source_hash is required")

    return {
        "schema_version": EVIDENCE_REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "candidate": deepcopy(candidate),
        "evaluation_context": {
            "evaluation_mode": evaluation_mode,
            "incremental_status": incremental_status,
            "baseline": deepcopy(baseline),
            "data": deepcopy(data),
            "model": deepcopy(model),
            "bootstrap": deepcopy(bootstrap),
        },
        "factor_evaluation": deepcopy(factor_evaluation),
        "model_evaluation": deepcopy(model_evaluation),
        "incremental_evaluation": deepcopy(incremental_evaluation),
    }
