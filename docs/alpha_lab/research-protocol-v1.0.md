# Alpha Lab Research Protocol v1.0

Status: release candidate until every v1.0 acceptance gate passes.

The authorized development window is 2022-01-01 through 2024-12-31. It has been repeatedly consulted and is development data. Data from 2025 onward is reserved. Supported candle timeframes are 5m, 15m, 1h, and 4h; every experiment records its timeframe and integer prediction horizon.

Candidate input is a time ordered pandas frame containing only completed candle `open`, `high`, `low`, `close`, and `volume`. Output is one numeric Series with the identical index. A signal is available after its source candle closes. Labels are forward binary returns from the next executable point over the declared horizon, named by `label_version`.

Walk forward fitting uses only labels available before each training cutoff and purges the complete label horizon. Dates, train window, retrain interval, sample floor, scaling, model, and baseline are identity inputs. The frozen default baseline is momentum periods 4 and 12 with logistic regression.

IC is Pearson correlation and RankIC is Spearman correlation after joint finite alignment. AUC is pooled ROC AUC over saved out of fold predictions. Log loss and Brier use the same paired rows.

Incremental metrics use paired moving block bootstrap with blocks at least as long as the label horizon. Version `paired-mbb-centered-null-v2` forms two sided p values from a centered null distribution with finite sample correction. Holm and Benjamini Hochberg adjustments cover all hypotheses in the preregistered development campaign; a distinct hypothesis on the same data remains in that family.

MDE is `(z_(1-alpha/2) + z_power) * SE`. AUC and RankIC use their own bootstrap SE. Mean loss gains may also report a dependence adjusted analytic approximation. VerdictPolicy alone defines practical thresholds: ΔAUC 0.005, ΔLogLoss 0.0005, ΔBrier 0.00025.

SUPPORTED requires meaningful positive effect, adequate design, adjusted significance, stability, nonredundancy, and no mechanism conflict. REJECTED requires resolved harm or adequate power excluding the practical positive threshold. Underpowered, missing, unstable, redundant, or contradictory evidence is INCONCLUSIVE.

Research Evidence is deterministic engine output. A Research Decision is append only interpretation tied to exact evidence. Each run records engine and protocol versions, content data fingerprint, candidate source hash, config hash, label/evaluator versions, model protocol, bootstrap settings, and multiple testing family.

Reuse requires identical identity and valid hashes for summary, manifest, snapshot, predictions, folds, and evidence. Failed runs never complete. Historical method versions remain immutable.
