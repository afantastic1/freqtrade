"""Trusted fixed-transform cross-sectional research runner."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from alpha_lab.cross_sectional_store import CrossSectionalStore, canonical_json
from alpha_lab.data.cross_sectional_dataset import CrossSectionalDataset
from alpha_lab.data.market_panel import MarketPanel
from alpha_lab.data.universe import DynamicUniverse, FixedUniverse
from alpha_lab.evaluation.cross_sectional import (
    relative_momentum, relative_volatility, relative_volume,
)
from alpha_lab.evaluation.cross_sectional_evaluator import CrossSectionalEvaluator
from alpha_lab.experiment_registry import CompletedRunAvailable, ExperimentRegistry
from alpha_lab.labels.cross_sectional import cross_sectional_labels
from alpha_lab.models.cross_sectional import ridge_predict
from alpha_lab.stats.multiple_testing import adjust_pvalues


ENGINE_VERSION = "1.1.0-dev"
PROTOCOL_VERSION = "alpha-research-v1.1-draft"
METHOD_VERSION = "cs-time-block-centered-v1"
TRANSFORMS = {"relative_momentum": relative_momentum,
              "relative_volatility": relative_volatility,
              "relative_volume": relative_volume}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    allowed = required | {"ridge_alpha", "min_assets", "cost_bps", "target", "family_id"}
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
    train_end, test_end = _validate_research_window(config)
    start, end = pd.Timestamp(config["start"]).tz_convert("UTC"), pd.Timestamp(config["end"]).tz_convert("UTC")
    times = panel.frame.index.get_level_values("open_time")
    if len(times) == 0 or times.min() < start or times.max() >= end:
        raise ValueError("panel contains out-of-range candles")
    if set(times.map(lambda t: t.year)) - {2022, 2023, 2024}:
        raise ValueError("held-out data is forbidden")
    if set(panel.frame.index.get_level_values("symbol")) - set(config["symbols"]):
        raise ValueError("panel contains undeclared symbols")
    if not config["features"] or any(set(spec) != {"name", "periods"} or spec["name"] not in TRANSFORMS
                                      for spec in config["features"]):
        raise ValueError("only fixed cross-sectional transforms are supported")
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


def run_cross_sectional(config: dict, panel: MarketPanel, *, root: str | Path) -> dict:
    """Evaluate fixed transforms; candidate Python is never imported or executed."""
    train_end, test_end = _validate_config(config, panel)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    universe = _universe(config["universe"])
    membership = universe.membership(panel)
    feature_source_hash = _sha(Path(__file__).parent / "evaluation" / "cross_sectional.py")
    code_paths = [Path(__file__), Path(__file__).parent / "data" / "cross_sectional_dataset.py",
                  Path(__file__).parent / "labels" / "cross_sectional.py",
                  Path(__file__).parent / "evaluation" / "cross_sectional_evaluator.py",
                  Path(__file__).parent / "models" / "cross_sectional.py"]
    code_hash = hashlib.sha256(canonical_json({p.name: _sha(p) for p in code_paths}).encode()).hexdigest()
    fingerprint = panel.fingerprint()
    identity = {"engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION,
                "method_version": METHOD_VERSION, "mode": "cross_sectional",
                "universe_id": universe.universe_id, "universe_hash": universe.universe_hash,
                "symbols": sorted(config["symbols"]), "timeframe": config["timeframe"],
                "horizon": config["horizon"], "data_fingerprint": fingerprint,
                "candidate_source_hash": feature_source_hash,
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
        features = {}
        for spec in config["features"]:
            name = f"{spec['name']}_{spec['periods']}"
            if name in features:
                raise ValueError("duplicate feature")
            features[name] = TRANSFORMS[spec["name"]](panel, periods=spec["periods"],
                                                      timeframe=config["timeframe"]).where(membership)
        labels = cross_sectional_labels(panel, membership, timeframe=config["timeframe"],
                                        horizon=config["horizon"])
        dataset = CrossSectionalDataset(panel, membership, features, labels)
        target = config.get("target", "relative_return")
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
        summary = {"run_id": run_id, "mode": "cross_sectional", "status": "completed",
                   "engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION,
                   "method_version": METHOD_VERSION, "config_hash": identity_hash,
                   "data_fingerprint": fingerprint, "candidate_source_hash": feature_source_hash,
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
