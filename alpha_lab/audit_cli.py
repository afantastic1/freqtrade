"""统计审计 CLI —— 对已有 run 做只读重评，并生成报告。

用法::

    # 查看将被审计的族构成（不计算）
    .venv/bin/python -m alpha_lab.audit_cli plan

    # 完整重评（默认不写 DB）
    .venv/bin/python -m alpha_lab.audit_cli run --n-boot 1000

    # 重评并追加到 SQLite 的 statistical_reviews 表（append-only）
    .venv/bin/python -m alpha_lab.audit_cli run --n-boot 1000 --record

本命令**不修改**任何原始 append-only 记录（experiments / candidates /
evaluations / research_decisions）。它只新增派生的重评记录。
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from alpha_lab.stats.audit import audit_campaign, audit_run, load_run_summary
from alpha_lab.stats.selfcheck import (
    build_highlights as build_selfcheck_highlights,
    family_sensitivity,
    neff_mde_consistency,
    placebo_from_predictions,
    placebo_walk_forward,
    primary_metric_sensitivity,
    render_selfcheck_markdown,
    _json_safe as selfcheck_json_safe,
)
from alpha_lab.stats.verdict import VerdictPolicy

DEFAULT_ROOT = Path("user_data/research/alpha_lab")
METHOD_VERSION = "stats-audit-v3-centered-null"
SELFCHECK_METHOD_VERSION = "stats-selfcheck-v2-centered-null"
METHOD_HISTORY = (
    "`stats-audit-v1` 的逐月符号检验存在缺陷（只取一侧尾，当少数侧为负时会把 "
    "p 误算成 ≈1），已在记录任何结论前撤回；`v2` 修正了该缺陷，并在自检中"
    "发现「候选因子键定位」在缺失 `evidence_report` 的回填 run 上会失败"
    "（机制轴被静默跳过），一并修正。数据库中的 `statistical_reviews` "
    "只保留修正后的 `v2` 一套记录。"
)

# 预注册方向：+1 表示 hypothesis（或因子源码自带的预注册声明）明确指出
# "因子值与未来收益正相关"，-1 表示负相关，None 表示未声明或明确中立。
#
# 抄录规则（避免事后拟合）：方向必须来自**运行前就已固定**的文本 —— 只能是
# candidate.json 的 hypothesis，或 candidate 的 factor.py 文档/构造中自带的
# 符号约定。不得参考任何已观测的 RankIC / ΔAUC。
PREREGISTERED_SIGNS = {
    # "the negated normalized band-pass (=-cyclical extension) should predict
    #  positive forward 4h return. Reversal sign is preregistered"
    "d455286f": +1,  # bandpass_cycle_reversal_v1 (15m, h=16) —— hypothesis 明确写 L
    # "构造反向因子 factor = -mean(CLV, pressure_window)，预期与未来 4 小时收益正相关"
    "f6bb1b6e": +1,  # intrabar_pressure_reversal (1h, h=4) —— hypothesis 明确写正
    # "factor_t = VW_t - EW_t ... predicts the next 24 bars with a POSITIVE sign"
    "27673851": +1,  # volume_weight_return_premium_5m_v1 (5m, h=24)
    # "Predicted sign: POSITIVE"
    "868202c3": +1,  # vol_asym_flow_4h_v1 (4h, h=6)
    # "Preregistered sign: POSITIVE"
    "34017247": +1,  # variance_ratio_regime_momentum_5m_v1 (5m, h=24)
    # "PREREGISTERED DIRECTION: POSITIVE"
    "dbd0d0ac": +1,  # long_scale_trend_4h_v1 (4h, h=42)
    # hypothesis 只写"短期价格上涨但成交量没有同步增加时，未来收益可能存在反转
    # 倾向"；结合 factor.py 的构造 factor = mom4 / rel_volume（数值大 = 涨得多且
    # 量能不足），"反转"意味着因子与未来收益**负**相关。derivation 完全来自
    # 运行前固定的 hypothesis + 构造式，与观测 RankIC 无关。
    "c00c5b96": -1,  # volume_divergence (1h, h=4)
    # factor.py 自带约定：sig = -1 于 failed up-break（"expect reversal down"），
    # +1 于 failed down-break（"expect reversal up"）—— 正值即预期上涨。
    "eb12259b": +1,  # fbr_intraday_failed_breakout_v1 (1h, h=1)
    # hypothesis：wick_asym 为正（下影线主导）"可能预示低位反弹" -> 正值预期上涨。
    "9ae5af22": +1,  # wick_asymmetry_rejection (1h, h=4)
    # factor.py 文档串明确写 "Preregistered sign: POSITIVE"（同一 wall-clock
    # bucket 的历史收益与当前 bar 的未来收益正聚集）。
    "eeeb48ad": +1,  # intraday_seasonality_5m_v1 (5m, h=24)
    # 明确中立："预注册符号方向：中立"
    "86e4a35e": None,  # volume_divergence_resid_mom4_w24 (1h, h=4)
}

# 协议外重跑（trace-only，不进主族）：round 5b 把 5m 候选误跑在 1h。
EXCLUDED_RUN_PREFIXES = {
    "034ff7df48ba": "round5b off-protocol rerun (5m candidate simulated at 1h)",
}


def _resolve_sign(candidate_id: str) -> int | None:
    for prefix, sign in PREREGISTERED_SIGNS.items():
        if candidate_id.startswith(prefix):
            return sign
    return None


def collect_family(root: Path) -> dict:
    """枚举 run，按 (candidate_id, timeframe, horizon) 去重成"实验族"。"""
    runs_dir = Path(root) / "runs"
    db_path = Path(root) / "experiments.sqlite3"

    candidate_of = {}
    if db_path.is_file():
        with sqlite3.connect(db_path) as conn:
            for run_id, candidate_id in conn.execute(
                "SELECT run_id, candidate_id FROM evaluations"
            ):
                candidate_of[run_id] = candidate_id

    entries = []
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if not (run_dir / "predictions_combined.csv").is_file():
            continue
        if not (run_dir / "predictions_baseline.csv").is_file():
            continue
        try:
            summary = load_run_summary(run_dir)
        except (FileNotFoundError, json.JSONDecodeError):
            continue

        evidence = summary.get("evidence_report") or {}
        context = evidence.get("evaluation_context") or {}
        data_block = context.get("data") or {}

        entries.append({
            "run_id": summary.get("run_id", run_dir.name),
            "run_dir": str(run_dir),
            "candidate_id": candidate_of.get(
                summary.get("run_id", run_dir.name), "unknown"
            ),
            "candidate": (summary.get("candidate") or {}).get("name"),
            "timeframe": data_block.get("timeframe") or summary.get("timeframe"),
            "horizon": int(
                data_block.get("label_horizon")
                or summary.get("horizon")
                or (summary.get("candidate") or {}).get("prediction_horizon")
                or 1
            ),
        })

    groups: dict[tuple, list[dict]] = {}
    for entry in entries:
        key = (entry["candidate_id"], entry["timeframe"], entry["horizon"])
        groups.setdefault(key, []).append(entry)

    primary, excluded = [], []
    for (candidate_id, timeframe, horizon), members in sorted(
        groups.items(), key=lambda item: (item[0][1] or "", item[0][2], item[0][0])
    ):
        members = sorted(members, key=lambda e: e["run_id"])
        representative = members[0]
        duplicates = [m["run_id"] for m in members[1:]]

        record = {
            **representative,
            "duplicate_runs": duplicates,
            "group_size": len(members),
        }

        excluded_reason = next(
            (
                reason
                for prefix, reason in EXCLUDED_RUN_PREFIXES.items()
                if representative["run_id"].startswith(prefix)
            ),
            None,
        )
        if excluded_reason:
            record["exclusion_reason"] = excluded_reason
            excluded.append(record)
        else:
            primary.append(record)

    return {
        "root": str(root),
        "n_run_dirs": len(entries),
        "n_groups": len(groups),
        "primary_family": primary,
        "excluded": excluded,
    }


def _fmt(value, digits=6):
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:  # nan
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_sci(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:
            return "nan"
        return f"{value:.3e}"
    return str(value)


def render_markdown(campaign: dict, family: dict) -> str:
    runs = campaign["runs"]
    policy = campaign["policy"]

    lines: list[str] = []
    add = lines.append

    add("# Alpha Lab 统计审计报告（只读重评）")
    add("")
    add(f"- 方法版本：`{METHOD_VERSION}`")
    add(f"- 方法修订记录：{METHOD_HISTORY}")
    add(
        f"- bootstrap：`n_boot={campaign['n_boot']}`，"
        f"`random_state={campaign['random_state']}`"
    )
    add(
        f"- policy：alpha={policy['alpha']}, power={policy['power']}, "
        f"redundancy_threshold={policy['redundancy_threshold']}, "
        f"min_stability_ratio={policy['min_stability_ratio']}"
    )
    add(f"- 实际意义阈值：{policy['practical_thresholds']}")
    add(
        f"- 族规模（多重检验）：{campaign['multiple_testing']['family_size']} "
        f"个实验，主指标 `{campaign['multiple_testing']['metric']}`"
    )
    add("- 本报告不修改任何原始 append-only 记录。")
    add("")

    add("## 0. 结论摘要")
    add("")
    overall_counts: dict[str, int] = {}
    for run in runs:
        label = run["overall_verdict"]["label"]
        overall_counts[label] = overall_counts.get(label, 0) + 1
    inc_counts: dict[str, int] = {}
    mech_counts: dict[str, int] = {}
    for run in runs:
        inc_counts[run["verdict"]["label"]] = (
            inc_counts.get(run["verdict"]["label"], 0) + 1
        )
        mech_counts[run["mechanism_verdict"]["label"]] = (
            mech_counts.get(run["mechanism_verdict"]["label"], 0) + 1
        )
    add(f"- 最终标签：{overall_counts}")
    add(f"- 增量价值轴：{inc_counts}")
    add(f"- 机制方向轴：{mech_counts}")
    add("")

    add("## 1. 族构成")
    add("")
    add(
        f"run 目录总数 {family['n_run_dirs']}，去重后实验数 "
        f"{family['n_groups']}；其中主族 {len(family['primary_family'])}，"
        f"协议外 {len(family['excluded'])}。"
    )
    add("")
    add("| run_id | candidate | tf | horizon | 组内重复 |")
    add("|---|---|---|---|---|")
    for entry in family["primary_family"]:
        add(
            f"| `{entry['run_id'][:12]}` | {entry['candidate']} | "
            f"{entry['timeframe']} | {entry['horizon']} | "
            f"{len(entry['duplicate_runs'])} |"
        )
    for entry in family["excluded"]:
        add(
            f"| `{entry['run_id'][:12]}` | {entry['candidate']} | "
            f"{entry['timeframe']} | {entry['horizon']} | "
            f"排除：{entry['exclusion_reason']} |"
        )
    add("")

    add("## 2. 分辨能力（核心）")
    add("")
    add(
        "`n_eff_design = n / horizon`（设计口径）；`VIF` 为逐样本配对增益的 "
        "Newey–West 方差膨胀因子；`MDE` 为 alpha=0.05、power=0.80 下的最小可检测效应。"
    )
    add("")
    add(
        "| run | n | n_eff | VIF | block | ΔAUC MDE | ΔAUC 阈值 | 效力充足? "
        "| Δlogloss MDE | Δlogloss 阈值 | 效力充足? |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        n_eff = run["n_eff"]
        auc = run["metrics"]["auc"]
        ll = run["metrics"]["logloss"]
        add(
            f"| `{run['run_id'][:12]}` | {run['n']} | "
            f"{_fmt(n_eff['design'], 0)} | {_fmt(n_eff['variance_inflation'], 3)} | "
            f"{run['block_size']} | {_fmt(auc['mde'], 5)} | "
            f"{_fmt(auc['practical_threshold'], 5)} | "
            f"{'是' if auc['design_adequate'] else '否'} | "
            f"{_fmt(ll['mde'], 6)} | {_fmt(ll['practical_threshold'], 6)} | "
            f"{'是' if ll['design_adequate'] else '否'} |"
        )
    add("")

    add("## 3. 逐 run 重评")
    add("")
    add(
        "`p` 为 bootstrap 百分位 p 值，`BF 调整 p` 为 Benjamini–Hochberg "
        "校正（族 = 主族全部实验）。`增量判定` 只看 Δ 指标；`机制判定` 只看 "
        "RankIC 与预注册符号；`最终` 为两者合并。"
    )
    add("")
    add(
        "| run | tf/h | ΔAUC | ΔAUC 95%CI | p | BF 调整 p | Δlogloss | "
        "Δlogloss 95%CI | 月度正比 | max\\|ρ\\| | 增量判定 | 机制判定 | 最终 |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        auc = run["metrics"]["auc"]
        ll = run["metrics"]["logloss"]
        add(
            f"| `{run['run_id'][:12]}` | {run['timeframe']}/{run['horizon']} | "
            f"{_fmt(auc['point'], 5)} | "
            f"[{_fmt(auc['ci_95_lower'], 5)}, {_fmt(auc['ci_95_upper'], 5)}] | "
            f"{_fmt(auc['p_two_sided'], 3)} | {_fmt(auc.get('adjusted_p'), 3)} | "
            f"{_fmt(ll['point'], 6)} | "
            f"[{_fmt(ll['ci_95_lower'], 6)}, {_fmt(ll['ci_95_upper'], 6)}] | "
            f"{_fmt(run['stability'].get('positive_ratio'), 2)} | "
            f"{_fmt(run['redundancy']['max_abs_spearman'], 3)} | "
            f"{run['verdict']['label']} | {run['mechanism_verdict']['label']} | "
            f"**{run['overall_verdict']['label']}** |"
        )
    add("")

    add("### 判定理由")
    add("")
    for run in runs:
        add(f"- `{run['run_id'][:12]}` {run.get('candidate')}")
        add(
            f"  - 增量：{run['verdict']['label']} —— "
            f"{', '.join(run['verdict']['reasons'])}"
        )
        add(
            f"  - 机制：{run['mechanism_verdict']['label']} —— "
            f"{', '.join(run['mechanism_verdict']['reasons'])}"
            f"（RankIC={_fmt(run['rank_ic_evidence']['rank_ic'], 4)}, "
            f"符号检验 p={_fmt(run['rank_ic_evidence']['sign_test_p'], 4)}"
            f" / BH 调整 {_fmt(run['rank_ic_evidence'].get('sign_test_p_adjusted'), 4)}, "
            f"n_eff={_fmt(run['n_eff']['design'], 0)}）"
        )
        add(
            f"  - 最终：{run['overall_verdict']['label']}"
        )
    add("")

    add("## 4. 与原 append-only 点估计的一致性校验")
    add("")
    add(
        "重算的 ΔAUC / Δlogloss 点估计必须与原始记录一致（差异只应来自"
        "浮点与文件读取）。若不一致，说明重评代码不可信。"
    )
    add("")
    add("| run | 原 ΔAUC | 重算 ΔAUC | 差 | 原 Δlogloss | 重算 Δlogloss | 差 |")
    add("|---|---|---|---|---|---|---|")
    for run in runs:
        stored = (run["stored"]["incremental"] or {}).get("auc")
        stored_ll = (run["stored"]["incremental"] or {}).get("logloss")
        mine = run["metrics"]["auc"]["point"]
        mine_ll = run["metrics"]["logloss"]["point"]
        auc_diff = abs(stored - mine) if stored is not None else None
        ll_diff = abs(stored_ll - mine_ll) if stored_ll is not None else None
        add(
            f"| `{run['run_id'][:12]}` | {_fmt_sci(stored)} | {_fmt_sci(mine)} | "
            f"{_fmt_sci(auc_diff)} | {_fmt_sci(stored_ll)} | {_fmt_sci(mine_ll)} | "
            f"{_fmt_sci(ll_diff)} |"
        )
    add("")

    add("## 5. 实际意义阈值敏感性")
    add("")
    add(
        "两个关键候选的 ΔAUC 点估计（0.00495 / 0.00502）距阈值 0.005 不到 2%，"
        "因此判定可能被阈值的第三位小数决定。下表把全部 A 指标阈值同时乘以 "
        "0.5 / 0.8 / 1.0 / 1.5 / 2.0 后重新判定（只改阈值，不重算 bootstrap）。"
    )
    add("")
    sensitivity = campaign.get("threshold_sensitivity") or {}
    scales = sorted(sensitivity, key=lambda k: float(k[1:]))
    header = "| run | " + " | ".join(
        f"×{s[1:]} (AUC≥{sensitivity[s]['auc_threshold']:.5f})" for s in scales
    ) + " |"
    add(header)
    add("|---" * (len(scales) + 1) + "|")
    for run in runs:
        cells = []
        for s in scales:
            entry = sensitivity[s]["labels"].get(run["run_id"], {})
            cells.append(entry.get("overall", "—"))
        add(f"| `{run['run_id'][:12]}` | " + " | ".join(cells) + " |")
    add("")

    add("## 6. bootstrap 块长敏感性")
    add("")
    add(
        "主口径 block=horizon。原实现使用 24/72/168。下表给出 ΔAUC 与 Δlogloss "
        "的区间随块长的变化，用于检验结论对块长的稳健性；低于 horizon 的块长在"
        "方法上无效（块内无法容纳一个完整标签窗口），标注为 skipped。"
    )
    add("")
    add("| run | 块长 | ΔAUC | ΔAUC 95%CI | Δlogloss 95%CI |")
    add("|---|---|---|---|---|")
    for run in runs:
        primary = run["metrics"]
        add(
            f"| `{run['run_id'][:12]}` | {run['block_size']} (horizon) | "
            f"{_fmt(primary['auc']['point'], 5)} | "
            f"[{_fmt(primary['auc']['ci_95_lower'], 5)}, "
            f"{_fmt(primary['auc']['ci_95_upper'], 5)}] | "
            f"[{_fmt(primary['logloss']['ci_95_lower'], 6)}, "
            f"{_fmt(primary['logloss']['ci_95_upper'], 6)}] |"
        )
        for block, values in sorted(
            (run["robustness_blocks"] or {}).items(), key=lambda kv: int(kv[0])
        ):
            if "auc" not in values:
                add(
                    f"| `{run['run_id'][:12]}` | {block} | "
                    f"{values.get('skipped', 'n/a')} | — | — |"
                )
                continue
            add(
                f"| `{run['run_id'][:12]}` | {block} | "
                f"{_fmt(values['auc']['point'], 5)} | "
                f"[{_fmt(values['auc']['ci_95_lower'], 5)}, "
                f"{_fmt(values['auc']['ci_95_upper'], 5)}] | "
                f"[{_fmt(values['logloss']['ci_95_lower'], 6)}, "
                f"{_fmt(values['logloss']['ci_95_upper'], 6)}] |"
            )
    add("")

    return "\n".join(lines)


def command_plan(family: dict) -> int:
    print(f"run 目录：{family['n_run_dirs']}")
    print(f"去重实验：{family['n_groups']}")
    print(f"主族：{len(family['primary_family'])}")
    for entry in family["primary_family"]:
        sign = _resolve_sign(entry["candidate_id"])
        print(
            f"  {entry['run_id'][:12]} {str(entry['candidate'])[:38]:38} "
            f"tf={entry['timeframe']:>3} h={entry['horizon']:>3} "
            f"dup={len(entry['duplicate_runs'])} prereg={sign}"
        )
    for entry in family["excluded"]:
        print(
            f"  [EXCLUDED] {entry['run_id'][:12]} {entry['candidate']} "
            f"({entry['exclusion_reason']})"
        )
    return 0


def command_run(args) -> int:
    root = Path(args.research_root)
    family = collect_family(root)

    run_dirs = [Path(entry["run_dir"]) for entry in family["primary_family"]]
    for entry in family["excluded"]:
        if args.include_excluded:
            run_dirs.append(Path(entry["run_dir"]))

    signs = {}
    for entry in family["primary_family"] + (
        family["excluded"] if args.include_excluded else []
    ):
        sign = _resolve_sign(entry["candidate_id"])
        if sign is not None:
            signs[entry["run_id"]] = sign

    policy = VerdictPolicy()
    campaign = audit_campaign(
        run_dirs,
        preregistered_signs=signs,
        n_boot=args.n_boot,
        robustness_boot=args.robustness_boot,
        random_state=args.random_state,
        policy=policy,
    )
    campaign["method_version"] = METHOD_VERSION
    campaign["family"] = family

    notes = Path(root) / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    json_path = notes / "statistical_audit.json"
    md_path = notes / "statistical_audit.md"

    json_path.write_text(
        json.dumps(campaign, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(campaign, family), encoding="utf-8")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    if args.record:
        from alpha_lab.research_store import ResearchStore

        store = ResearchStore(Path(root) / "experiments.sqlite3")
        recorded = 0
        for run in campaign["runs"]:
            candidate_id = None
            for entry in family["primary_family"] + family["excluded"]:
                if entry["run_id"] == run["run_id"]:
                    candidate_id = entry["candidate_id"]
                    break
            store.record_statistical_review(
                {
                    **run,
                    "analysis_id": f"stats-audit-{run['run_id']}",
                    "config_id": run["run_id"],
                    "primary_metric": "auc",
                    "random_state": args.random_state,
                },
                method_version=METHOD_VERSION,
                candidate_id=candidate_id,
            )
            recorded += 1
        print(f"recorded {recorded} statistical_reviews rows")

    return 0


def _family_variants(family: dict) -> dict:
    """若干**都说得通**的族定义，用于检验 BH 是否依赖族定义。"""
    recorded = [entry["run_id"] for entry in family["primary_family"]]
    excluded = [entry["run_id"] for entry in family["excluded"]]
    duplicates = [
        run_id
        for entry in family["primary_family"]
        for run_id in entry.get("duplicate_runs", [])
    ]
    variants = {
        "recorded_family": recorded,
        "recorded_plus_excluded": recorded + excluded,
        "no_dedupe": recorded + duplicates,
        "all_run_dirs": recorded + duplicates + excluded,
    }
    return variants


def command_selfcheck(args) -> int:
    root = Path(args.research_root)
    notes = root / "notes"
    audit_path = notes / "statistical_audit.json"
    if not audit_path.is_file():
        raise SystemExit(
            f"missing {audit_path}; run `audit_cli run --record` first"
        )
    campaign = json.loads(audit_path.read_text(encoding="utf-8"))
    runs = campaign["runs"]
    family = collect_family(root)
    policy = VerdictPolicy()

    # 为了检验"族定义"这一自由度，需要把**未被重评**的 run（重复 + 协议外）
    # 也重评一次（低 n_boot，只用于 BH 敏感性）。不写任何记录。
    in_campaign = {run["run_id"] for run in runs}
    extra_dirs = []
    for entry in family["primary_family"]:
        for run_id in entry.get("duplicate_runs", []):
            extra_dirs.append(Path(root) / "runs" / run_id)
    for entry in family["excluded"]:
        extra_dirs.append(Path(root) / "runs" / entry["run_id"])

    extra_runs = []
    for run_dir in extra_dirs:
        try:
            audit = audit_run(
                run_dir,
                n_boot=args.placebo_boot,
                robustness_boot=max(1, args.placebo_boot // 3),
                random_state=args.random_state,
                policy=policy,
            )
        except (FileNotFoundError, ValueError):
            continue
        if audit["run_id"] not in in_campaign:
            extra_runs.append(audit)
        in_campaign.add(audit["run_id"])

    runs_for_family = runs + extra_runs

    payload = {
        "method_version": SELFCHECK_METHOD_VERSION,
        "n_family_runs": len(runs),
        "n_extra_runs_recomputed": len(extra_runs),
        "primary_metric_sensitivity": primary_metric_sensitivity(runs, policy),
        "family_sensitivity": family_sensitivity(
            runs_for_family, _family_variants(family), policy
        ),
        "neff_mde_consistency": neff_mde_consistency(runs),
    }

    if not args.skip_placebo:
        run_dirs = [
            Path(entry["run_dir"]) for entry in family["primary_family"]
        ]
        payload["placebo_predictions"] = placebo_from_predictions(
            run_dirs,
            n_shift=args.placebo_shifts,
            n_boot=args.placebo_boot,
            random_state=args.random_state,
        )

    if not args.skip_walk_forward_placebo:
        payload["placebo_walk_forward"] = placebo_walk_forward(
            timeframe=args.wf_timeframe,
            horizon=args.wf_horizon,
            n_shift=args.wf_shifts,
            n_boot=args.placebo_boot,
            random_state=args.random_state,
        )

    payload["highlights"] = build_selfcheck_highlights(payload)

    notes.mkdir(parents=True, exist_ok=True)
    json_path = notes / "method_audit.json"
    md_path = notes / "method_audit.md"
    json_path.write_text(
        json.dumps(
            selfcheck_json_safe(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    md_path.write_text(render_selfcheck_markdown(payload), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="alpha_lab.audit_cli")
    parser.add_argument("--research-root", default=str(DEFAULT_ROOT))

    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="只列出族构成")
    plan.add_argument("--research-root", default=str(DEFAULT_ROOT))

    run = sub.add_parser("run", help="执行重评并写报告")
    run.add_argument("--research-root", default=str(DEFAULT_ROOT))
    run.add_argument("--n-boot", type=int, default=1000)
    run.add_argument("--robustness-boot", type=int, default=300)
    run.add_argument("--random-state", type=int, default=42)
    run.add_argument("--record", action="store_true", help="写入 statistical_reviews")
    run.add_argument(
        "--include-excluded",
        action="store_true",
        help="把协议外 run 也纳入重评（默认只报告不纳入主族口径）",
    )

    check = sub.add_parser(
        "selfcheck", help="统计方法自检（Q1-Q4 方法学审计）"
    )
    check.add_argument("--research-root", default=str(DEFAULT_ROOT))
    check.add_argument("--random-state", type=int, default=42)
    check.add_argument("--placebo-shifts", type=int, default=60)
    check.add_argument("--placebo-boot", type=int, default=60)
    check.add_argument("--skip-placebo", action="store_true")
    check.add_argument("--skip-walk-forward-placebo", action="store_true")
    check.add_argument("--wf-timeframe", default="1h")
    check.add_argument("--wf-horizon", type=int, default=4)
    check.add_argument("--wf-shifts", type=int, default=30)

    args = parser.parse_args(argv)

    if args.command == "plan":
        return command_plan(collect_family(Path(args.research_root)))
    if args.command == "selfcheck":
        return command_selfcheck(args)
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
