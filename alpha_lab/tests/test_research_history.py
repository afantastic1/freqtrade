
import json

from alpha_lab.research_history import (
    compact_history,
    read_history,
)


def test_history_reads_completed_candidate_runs(tmp_path):
    run_dir = tmp_path / "run001"
    run_dir.mkdir()

    summary = {
        "run_id": "run001",
        "status": "completed",
        "config_hash": "hash001",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "horizon": 4,
        "candidate": {
            "name": "volume_divergence",
            "source_hash": "source001",
            "hypothesis": "Volume adjusts momentum",
            "parameters": {"periods": 4},
        },
        "factor_evaluation": {
            "volume_divergence_periods_4": {
                "overall": {"rank_ic": -0.06}
            }
        },
        "incremental_evaluation": {
            "factor_similarity": {
                "momentum_periods_4": {
                    "spearman": 0.95
                }
            },
            "incremental": {
                "auc": 0.004
            },
            "stability": {
                "robust_positive": False
            },
        },
        "research_feedback": {
            "classification": "INCONCLUSIVE",
            "tags": ["HIGH_REDUNDANCY"],
        },
    }

    (run_dir / "summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )

    records = read_history(tmp_path)

    assert len(records) == 1
    assert records[0]["candidate"]["name"] == "volume_divergence"

    compact = compact_history(records)

    assert compact[0]["feedback"]["classification"] == "INCONCLUSIVE"
    assert compact[0]["bootstrap_robust_positive"] is False
    assert compact[0]["incremental"]["auc"] == 0.004


def test_history_skips_failed_runs(tmp_path):
    run_dir = tmp_path / "failed"
    run_dir.mkdir()

    (run_dir / "summary.json").write_text(
        json.dumps({
            "run_id": "failed",
            "status": "failed",
            "candidate": {"name": "bad_factor"},
        }),
        encoding="utf-8",
    )

    assert read_history(tmp_path) == []


def test_candidate_filter(tmp_path):
    for name in ("alpha_one", "alpha_two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "summary.json").write_text(
            json.dumps({
                "run_id": name,
                "status": "completed",
                "candidate": {"name": name},
            }),
            encoding="utf-8",
        )

    records = read_history(
        tmp_path,
        candidate_name="alpha_two",
    )

    assert len(records) == 1
    assert records[0]["candidate"]["name"] == "alpha_two"
