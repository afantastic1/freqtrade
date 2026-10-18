# Alpha Lab 施工进度

## 2026-10-18
- 读取 `docs/work.md`，确认范围覆盖 CLI、Runner、快照、研究索引、历史补录、去重、解释质量与五道验收 Gate。
- 读取并启用 `using-superpowers`、`brainstorming`、`writing-plans`、`planning-with-files`。
- 检查仓库状态，识别用户已有未提交修改并记录保护约束。
- 开始阶段 1 现状审计。
- 审阅 `config.py`、`candidate_cli.py`、`runner.py`、`experiment_registry.py`、候选加载、增量评价、反馈与现有 JSON 历史入口，记录首批差距。

## 测试日志
- 改造前全量：`107 passed in 14.88s`。
- 数据库只读审计完成：experiments=4，candidates/evaluations 尚不存在。
- Task 1 默认基线/CLI 合同：先见证 5 个测试失败，再实现 mom4+mom12、`--no-baseline`、`--baseline-config`、`--force` 与紧凑 JSON；聚焦测试 11 passed，全量 112 passed。
- Task 2 不可变快照：先见证 3 个测试失败，再实现 copy/hash/revalidate、`candidate_snapshot/<name>/`、原子 manifest，以及从快照路径执行；聚焦测试 8 passed，全量 115 passed。
- Task 3 ResearchStore：先见证 5 个测试失败，再实现 additive schema migration、FK、索引、候选身份冲突保护、signed IC 与 NULL 增量字段；聚焦测试 11 passed，全量 120 passed。
- Task 4 研究键/生命周期：先见证 4 个测试失败，再实现数据文件 stat 指纹、评价代码版本、原子 running claim、stale 恢复、可信产物复用、force 与 completed-but-unindexed 可补录语义；聚焦测试 8 passed，全量 125 passed。
- Task 5 评价模式/诚实解释：先见证 4 个测试失败，再加入 baseline source/feature 映射、single/incremental 状态、共同样本元数据、有符号指标断言与 Bootstrap/开发集/机制假说限制；聚焦测试 9 passed，全量 128 passed。
- Task 6 SQLite CLI/backfill：先见证 4 个测试失败，再实现 parent-authoritative dry-run/apply、missing/invalid/conflict 分类、两遍幂等与 list/show JSON/文本查询；聚焦测试 9 passed，全量 132 passed。
- Task 7 真实验收：先备份数据库；真实迁移/补录、历史查询、force 完整研究、默认复用、快照不变性与 SQLite FK 检查均已执行。验收中发现并用 RED→GREEN 修复 orphan 分类、CLI force 透传及候选 manifest 元数据升级。
- 独立最终审查：7 项 Important 全部在单轮修复中完成 RED→GREEN；最终全量 139 passed，最终真实 run 为 `bd4260f5df084ca583d9b7f51c8e845e`，随后默认命令复用同一 run。
- 最终 fresh 命令：`compileall` exit 0；全量 `139 passed in 11.15s`；`git diff --check` exit 0；真实数据库 experiments=8/candidates=3/evaluations=8，`foreign_key_check` 为空。
