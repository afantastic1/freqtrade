"""Trusted fixed-transform cross-sectional research runner."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from alpha_lab.cross_sectional_store import CrossSectionalStore, canonical_json
from alpha_lab.cross_sectional_features import build_features, validate_feature_specs
from alpha_lab.data.cross_sectional_dataset import CrossSectionalDataset
from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.universe import DynamicUniverse, FixedUniverse
from alpha_lab.evaluation.cross_sectional import (
    relative_momentum, relative_volatility, relative_volume,
)
from alpha_lab.evaluation.cross_sectional_evaluator import CrossSectionalEvaluator
from alpha_lab.experiment_registry import CompletedRunAvailable, ExperimentRegistry
from alpha_lab.labels.cross_sectional import (
    CROSS_SECTIONAL_LABEL_VERSION, cross_sectional_labels,
)
from alpha_lab.models.cross_sectional import ridge_predict
from alpha_lab.research_store import CROSS_SECTIONAL_VERDICT_POLICY_VERSION
from alpha_lab.stats.multiple_testing import adjust_pvalues
from alpha_lab.stats.method_registry import REGISTRY_VERSION
from data_engineering.coverage import preflight_feature_coverage
from data_engineering.fundamentals import FundamentalDataAdapter


ENGINE_VERSION = "1.1.0-dev"
PROTOCOL_VERSION = "alpha-research-v1.1-fundamental-draft-v1"
METHOD_VERSION = "cs-time-block-centered-v1"
INFERENCE_STATUS = "BLOCKED"
TRANSFORMS = {"relative_momentum": relative_momentum,
              "relative_volatility": relative_volatility,
              "relative_volume": relative_volume}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _code_fingerprint(
    paths: list[Path],
    *,
    root: Path | None = None,
) -> str:
    repo_root = (
        root if root is not None
        else Path(__file__).resolve().parents[1]
    ).resolve()

    digests = {}

    for path in paths:
        resolved = Path(path).resolve(strict=True)

        relative = resolved.relative_to(
            repo_root
        ).as_posix()

        if relative in digests:
            raise ValueError(
                f"duplicate code path: {relative}"
            )

        digests[relative] = _sha(resolved)

    return hashlib.sha256(
        canonical_json(digests).encode("utf-8")
    ).hexdigest()

def _validate_research_window(config: dict):
    """Reject invalid or held-out windows before reading a panel artifact."""
    for key in ("start", "train_end", "test_end", "end"):
        if key not in config:
            raise ValueError("missing research window boundary")
    stamps = [pd.Timestamp(config[key]) for key in ("start", "train_end", "test_end", "end")]
    if any(t.tzinfo is None for t in stamps):
        raise ValueError("config times must be timezone-aware")
    start, train_end, test_end, end = [t.tz_convert("UTC") for t in stamps]
    if not (pd.Timestamp("2022-01-01", tz="UTC") <= start < train_end < test_end <= end <= pd.Timestamp("2025-01-01", tz="UTC")):
        raise ValueError("research dates outside development interval")
    return train_end, test_end


def _validate_config(config: dict, panel: MarketPanel):
    required = {"mode", "hypothesis", "symbols", "timeframe", "horizon", "start", "end", "train_end",
                "test_end", "universe", "features", "n_boot", "block_size"}
    allowed = required | {
        "ridge_alpha", "min_assets", "cost_bps", "target", "family_id",
        "feature_schema_version", "min_feature_coverage",
    }
    if set(config) - allowed or required - set(config):
        raise ValueError("missing or unknown cross-sectional config keys")
    if config["mode"] != "cross_sectional" or config["timeframe"] not in ("5m", "15m", "1h", "4h"):
        raise ValueError("invalid mode or timeframe")
    if not isinstance(config["hypothesis"], str) or not config["hypothesis"].strip():
        raise ValueError("hypothesis is required")
    if config.get("family_id", "2022-2024-usdm-cs-development") != "2022-2024-usdm-cs-development":
        raise ValueError("development family cannot be changed")
    if type(config["horizon"]) is not int or config["horizon"] < 1:
        raise ValueError("invalid horizon")
    if config.get("target", "relative_return") not in ("relative_return", "raw_return"):
        raise ValueError("unsupported cross-sectional target")
    train_end, test_end = _validate_research_window(config)
    start, end = pd.Timestamp(config["start"]).tz_convert("UTC"), pd.Timestamp(config["end"]).tz_convert("UTC")
    times = panel.frame.index.get_level_values("open_time")
    if len(times) == 0 or times.min() < start or times.max() >= end:
        raise ValueError("panel contains out-of-range candles")
    availability = pd.to_datetime(panel.frame.available_time, utc=True, errors="raise")
    if availability.lt(start).any() or availability.ge(end).any():
        raise ValueError("panel availability outside development window")
    if set(times.map(lambda t: t.year)) - {2022, 2023, 2024}:
        raise ValueError("held-out data is forbidden")
    if set(panel.frame.index.get_level_values("symbol")) - set(config["symbols"]):
        raise ValueError("panel contains undeclared symbols")
    specs = validate_feature_specs(
        config["features"], config.get("feature_schema_version")
    )
    if any(spec["kind"] == "market" and spec["name"] not in TRANSFORMS for spec in specs):
        raise ValueError("only registered fixed cross-sectional market transforms are supported")
    if "min_feature_coverage" in config and (
        isinstance(config["min_feature_coverage"], bool)
        or not isinstance(config["min_feature_coverage"], (int, float))
        or not 0 <= config["min_feature_coverage"] <= 1
    ):
        raise ValueError("min_feature_coverage must be between zero and one")
    return train_end, test_end


def _universe(spec: dict):
    if spec.get("kind") == "fixed" and set(spec) == {"kind", "id", "symbols"}:
        return FixedUniverse(spec["id"], tuple(spec["symbols"]))
    if spec.get("kind") == "dynamic" and set(spec) == {"kind", "id", "symbols", "lookback", "min_listing_bars", "min_coverage", "min_quote_volume"}:
        return DynamicUniverse(spec["id"], tuple(spec["symbols"]), spec["lookback"],
                               spec["min_listing_bars"], spec["min_coverage"], spec["min_quote_volume"])
    raise ValueError("unsupported universe definition")


def _valid_reuse(run: dict, identity_hash: str) -> dict | None:
    directory = Path(run["result_dir"] or "")
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["config_hash"] != identity_hash:
            return None
        expected = manifest["artifacts"]
        if set(expected) != {p.name for p in directory.iterdir() if p.is_file() and p.name != "manifest.json"}:
            return None
        if any(_sha(directory / name) != digest for name, digest in expected.items()):
            return None
        summary = json.loads((directory / "summary.json").read_text())
        if summary["run_id"] != run["run_id"] or summary["config_hash"] != identity_hash:
            return None
        return summary
    except (OSError, KeyError, ValueError, TypeError):
        return None


def run_cross_sectional(
    config: dict, panel: MarketPanel, *, root: str | Path,
    fundamental_adapter: FundamentalDataAdapter | None = None,
) -> dict:
    """Evaluate declared market and strict-PIT features; candidate Python is never run."""
    train_end, test_end = _validate_config(config, panel)
    universe = _universe(config["universe"])
    membership = universe.membership(panel)
    features, feature_provenance = build_features(
        config["features"], panel, timeframe=config["timeframe"],
        start=config["start"], end=config["end"], transforms=TRANSFORMS,
        feature_schema_version=config.get("feature_schema_version"),
        adapter=fundamental_adapter,
    )
    features = {name: values.where(membership) for name, values in features.items()}
    target = config.get("target", "relative_return")
    labels = cross_sectional_labels(panel, membership, timeframe=config["timeframe"],
                                    horizon=config["horizon"])
    panel_times = panel.frame.index.get_level_values("open_time")
    coverage_membership = (
        membership.astype(bool)
        & pd.Series(panel_times >= train_end, index=panel.frame.index)
        & pd.Series(panel_times < test_end, index=panel.frame.index)
        & labels[target].notna()
    )
    feature_coverage = preflight_feature_coverage(
        panel, features, membership=coverage_membership,
        min_assets_per_time=int(config.get("min_assets", 5)),
        min_feature_coverage=float(config.get("min_feature_coverage", 0.0)),
    )
    feature_coverage["scope"] = "labeled_test_window"
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    feature_code_paths = [
        Path(__file__).parent / "cross_sectional_features.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "catalog.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "alpha_lab_features.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "fundamentals.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "pit.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "registries.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "coverage.py",
        Path(__file__).resolve().parents[1] / "data_engineering" / "schemas" / "catalog-manifest-v2.schema.json",
        Path(__file__).resolve().parents[1] / "data_engineering" / "schemas" / "pit-evidence-v2.schema.json",
    ]
    # feature_source_hash = hashlib.sha256(canonical_json(
    #     {p.name: _sha(p) for p in feature_code_paths}
    # ).encode()).hexdigest()
    feature_source_hash = _code_fingerprint(
    feature_code_paths
    )
    code_paths = [Path(__file__), *feature_code_paths,
                  Path(__file__).parent / "cross_sectional_cli.py",
                  Path(__file__).parent / "data" / "cross_sectional_dataset.py",
                  Path(__file__).parent / "labels" / "cross_sectional.py",
                  Path(__file__).parent / "evaluation" / "cross_sectional_evaluator.py",
                  Path(__file__).parent / "evaluation" / "cross_sectional.py",
                  Path(__file__).parent / "models" / "cross_sectional.py",
                  Path(__file__).parent / "data" / "market_panel.py",
                  Path(__file__).parent / "data" / "universe.py",
                  Path(__file__).parent / "stats" / "multiple_testing.py",
                  Path(__file__).parent / "stats" / "method_registry.py"]
    # code_hash = hashlib.sha256(canonical_json({p.name: _sha(p) for p in code_paths}).encode()).hexdigest()
    code_hash = _code_fingerprint(code_paths)
    fingerprint = panel.fingerprint()
    identity = {"engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION,
                "method_version": METHOD_VERSION, "mode": "cross_sectional",
                "decision_policy_version": CROSS_SECTIONAL_VERDICT_POLICY_VERSION,
                "statistical_registry_version": REGISTRY_VERSION,
                "universe_id": universe.universe_id, "universe_hash": universe.universe_hash,
                "symbols": sorted(config["symbols"]), "timeframe": config["timeframe"],
                "horizon": config["horizon"], "data_fingerprint": fingerprint,
                "candidate_source_hash": feature_source_hash,
                "feature_implementation_hash": code_hash,
                "feature_provenance": feature_provenance,
                "label_version": CROSS_SECTIONAL_LABEL_VERSION,
                "inference_status": INFERENCE_STATUS,
                "evaluation_code_hash": code_hash, "config": config}
    identity_hash = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    db_path = root / "experiments.sqlite3"
    registry = ExperimentRegistry(db_path)
    store = CrossSectionalStore(db_path)
    for previous in registry.find_by_config(identity_hash):
        if previous["status"] == "completed":
            reusable = _valid_reuse(previous, identity_hash)
            if reusable is not None:
                return reusable
    try:
        run_id = registry.claim_run(config_hash=identity_hash, config_json=canonical_json(identity),
                                    code_version=ENGINE_VERSION, data_fingerprint=fingerprint,
                                    ignored_completed_run_ids=tuple(
                                        row["run_id"] for row in registry.find_by_config(identity_hash)
                                        if row["status"] == "completed" and _valid_reuse(row, identity_hash) is None))
    except CompletedRunAvailable:
        for row in registry.find_by_config(identity_hash):
            if row["status"] == "completed":
                reusable = _valid_reuse(row, identity_hash)
                if reusable is not None:
                    return reusable
        raise
    directory = root / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    try:
        dataset = CrossSectionalDataset(panel, membership, features, labels)
        train, test = dataset.split(train_end=train_end, test_end=test_end, target=target)
        if train.empty or test.empty:
            raise ValueError("insufficient eligible train or test observations")
        primary = next(iter(features))
        predictions = pd.DataFrame({"raw_factor": test[primary],
                                    "ridge": ridge_predict(train, test, tuple(features),
                                                           alpha=float(config.get("ridge_alpha", 1.0)), target=target),
                                    "momentum_baseline": relative_momentum(panel, periods=4,
                                                                           timeframe=config["timeframe"]).reindex(test.index),
                                    "volatility_baseline": relative_volatility(panel, periods=4,
                                                                               timeframe=config["timeframe"]).reindex(test.index),
                                    "target": test[target]}, index=test.index)
        evaluator = CrossSectionalEvaluator(min_assets=int(config.get("min_assets", 5)),
                                            n_boot=config["n_boot"], block_size=config["block_size"],
                                            cost_bps=float(config.get("cost_bps", 10.0)))
        results = {name: evaluator.evaluate(predictions[name], predictions.target)
                   for name in ("raw_factor", "ridge", "momentum_baseline", "volatility_baseline")}
        valid_names = [name for name, value in results.items()
                       if value["status"] == "EVALUATED" and value["bootstrap"].get("p_two_sided") is not None]
        if valid_names:
            adjusted = adjust_pvalues([results[name]["bootstrap"]["p_two_sided"] for name in valid_names])
            for i, name in enumerate(valid_names):
                results[name]["bootstrap"]["p_holm_within_run"] = adjusted["holm_bonferroni"][i]
                results[name]["bootstrap"]["p_bh_within_run"] = adjusted["benjamini_hochberg"][i]
                results[name]["bootstrap"]["p_by_within_run"] = adjusted["benjamini_yekutieli"][i]
        summary = {"run_id": run_id, "mode": "cross_sectional", "status": "completed",
                   "engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION,
                   "method_version": METHOD_VERSION, "config_hash": identity_hash,
                   "decision_policy_version": CROSS_SECTIONAL_VERDICT_POLICY_VERSION,
                   "statistical_registry_version": REGISTRY_VERSION,
                   "data_fingerprint": fingerprint, "candidate_source_hash": feature_source_hash,
                   "feature_implementation_hash": code_hash,
                   "feature_schema_version": feature_provenance["feature_schema_version"],
                   "feature_provenance": feature_provenance,
                   "feature_coverage": feature_coverage,
                   "label_version": CROSS_SECTIONAL_LABEL_VERSION,
                   "inference_status": INFERENCE_STATUS,
                   "universe_id": universe.universe_id, "universe_hash": universe.universe_hash,
                   "timeframe": config["timeframe"], "horizon": config["horizon"],
                   "family_id": config.get("family_id", "2022-2024-usdm-cs-development"),
                   "multiplicity_note": "within-run adjustment only; campaign-wide review required before SUPPORTED",
                   "train_rows": len(train), "test_rows": len(test),
                   "quality": panel.quality_report(config["timeframe"]), "results": results,
                   "research_label": "proxy; next open may precede data availability"}
        (directory / "summary.json").write_text(json.dumps(summary, default=str, sort_keys=True, allow_nan=False))
        predictions.reset_index().to_csv(directory / "predictions.csv", index=False)
        artifacts = {p.name: _sha(p) for p in directory.iterdir() if p.is_file()}
        (directory / "manifest.json").write_text(json.dumps({"config_hash": identity_hash,
                                                              "artifacts": artifacts}, sort_keys=True))
        store.append(run_id, identity_hash, json.loads((directory / "summary.json").read_text()))
        registry.complete_run(run_id, directory)
        return json.loads((directory / "summary.json").read_text())
    except Exception as exc:
        registry.fail_run(run_id, str(exc))
        raise
