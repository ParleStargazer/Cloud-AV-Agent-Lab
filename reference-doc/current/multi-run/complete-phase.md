# Multi-Run 阶段完成记录

## 2026-05-30 Commit 1：CLI Skeleton

本阶段完成 `multi-run` 第一阶段入口骨架：

- 新增 `multi-run` 子命令。
- 接收并解析第一版计划中的核心参数：
  - `--product`
  - `--instance-id`
  - `--snapshot-id`
  - `--region`
  - `--guest-agent-url`
  - `--desktop-worker-url`
  - `--sample-dir`
  - `--manifest`
  - `--batch-id`
  - `--batch-root`
  - `--dry-run`
  - `--plan-only`
  - `--all`
  - `--range`
  - `--indexes`
  - `--from`
  - `--to`
  - `--max-cases`
  - `--failure-policy`
  - `--resume`
  - `--rerun-failed`
  - `--force-rerun`
- 增加 selection 参数互斥校验：
  - `--all`
  - `--range`
  - `--indexes`
  - `--from/--to`
- 当前阶段只输出 skeleton JSON，不生成 manifest、不写 state、不调用
  single-run runner、不触发云操作、不读取样本内容。
- 新增 CLI 单元测试，覆盖 manifest + range + dry-run、sample-dir + all +
  plan-only、selection 冲突、`--from/--to` 成对校验。

下一阶段建议进入 Commit 2：定义 multi-run manifest、batch plan、state 和
event schema。

## 2026-05-30 Commit 2：Schema / Types

本阶段完成 `multi-run` 第二阶段 schema/type 定义：

- 新增 `orchestration.multi_run` 模块，定义纯数据结构，不接入 runner：
  - `SampleManifestEntry`
  - `BatchSelection`
  - `BatchExecutionPolicy`
  - `BatchPlan`
  - `CaseState`
  - `MultiRunState`
  - `MultiRunEvent`
- 固定第一版 schema version：
  - `multi-run-sample.v1`
  - `multi-run-plan.v1`
  - `multi-run-state.v1`
  - `multi-run-event.v1`
- 固定 multi-run 聚合层核心枚举值：
  - failure kind：`planning_or_policy_failure`、`case_failure`、
    `environment_failure`
  - verdict：`detected_or_blocked`、`allowed_executed`、`not_delivered`、
    `not_executed`、`inconclusive`、`not_evaluable`、`unknown`
  - readiness / cleanup / evidence / summary / batch state 等状态集合
- 所有 schema dataclass 提供 `to_dict()`，输出 JSON-safe payload，tuple
  字段会转换为 list，便于后续写入 JSON / JSONL。
- `orchestration.__init__` 增加稳定 re-export，方便后续 loader / runner 复用。
- 新增 `tests/test_multi_run_schema.py`，覆盖 manifest entry、batch plan、
  multi-run state、event JSONL shape 和 verdict 固定值。

本阶段仍未实现 manifest loader、digest、selection parser、scheduler 或
single-run runner 调度；没有触发云操作，没有读取样本内容。

下一阶段建议进入 Commit 3：实现 manifest loader 与 manifest digest。

## 2026-05-30 Commit 3：Manifest Loader + Digest

本阶段完成 `multi-run` 第三阶段 manifest loader 与 digest：

- 在 `orchestration.multi_run` 中新增 manifest 读取能力：
  - `load_sample_manifest(path)`
  - `compute_manifest_sha256(path)`
  - `LoadedSampleManifest`
  - `MultiRunManifestError`
- 支持读取 `sample_manifest.jsonl`，逐行解析 JSON object。
- 校验 manifest entry 的核心约束：
  - `schema_version = multi-run-sample.v1`
  - `sample_index` 必须为正整数且不能重复
  - `sample_id` / `sha256` / `md5` / `size` / `sample_ref` 必填且类型有效
  - `sha256` 必须为 64 位 hex，`md5` 必须为 32 位 hex
  - `sample_source_kind` 必须属于允许集合
  - `entry_status` 必须属于允许集合
- `LoadedSampleManifest` 暴露 `indexes` 与 `by_index()`，供后续 selection
  parser 使用。
- `compute_manifest_sha256()` 按文件原始 bytes 计算 digest，因此 JSONL 行顺序变化会
  改变 digest。
- 新增 `tests/test_multi_run_manifest.py`，覆盖：
  - valid manifest passes
  - duplicate `sample_index` fails
  - missing `sha256` fails
  - invalid `sample_source_kind` fails
  - manifest digest stable
  - manifest line order changes digest

本阶段仍未实现 selection parser、batch plan 写入、scheduler、runner 或
single-run 调度；loader 只读取 manifest 文本，不读取样本文件内容。

下一阶段建议进入 Commit 4：实现 sample selection parser。

## 2026-05-30 Commit 4：Selection Parser

本阶段完成 `multi-run` 第四阶段 sample selection parser：

- 新增 `parse_sample_selection(...)`，输入 manifest 已有 index 集合和 CLI
  selection 形态，输出冻结的 `BatchSelection.selected_indexes`。
- 支持第一版选择模式：
  - `--all`
  - `--range START-END`
  - `--indexes 1,3,7`
  - `--from N --to M`
  - `--max-cases N`
- 固定选择语义：
  - `--range` / `--from --to` 为闭区间。
  - `--indexes 7,3,3,1` 会去重并排序为 `(1, 3, 7)`。
  - `--all` 只选择 manifest 中实际存在的 index。
  - `--max-cases` 在排序 / 闭区间展开后截断。
  - 选择不存在的 index 会抛出 `MultiRunSelectionError`，其
    `failure_kind = planning_or_policy_failure`。
  - 同时传入多个互斥 selection 模式会失败。
- 保持 Commit 3 补查要求：
  - manifest loader 只要求 `sample_index` 为 1-based 正整数且唯一，不要求连续。
  - manifest digest 继续按原始 bytes 计算。
  - manifest loader 错误信息包含 manifest 路径和行号。
  - `sample_ref` 仍只做类型校验，不检查文件是否存在。
- 新增 `tests/test_multi_run_selection.py`，覆盖闭区间、去重排序、越界、
  非连续 manifest 缺失 index、互斥模式、`--from/--to` 配对和非法 index。

本阶段仍未生成 batch plan，不写 state/event log，不调用 single-run runner，
不读取样本文件内容。

下一阶段建议进入 Commit 5：生成 immutable batch plan 和 generated config。

## 2026-05-30 Commit 5：Batch Plan + Generated Config + Dry-Run

本阶段完成 `multi-run` 第五阶段不可变计划产物生成：

- `multi-run` CLI 从 skeleton 进入 planning 阶段，基于已有
  `sample_manifest.jsonl` 生成批次计划。
- 新增 batch plan 产物：
  - `batch_plan.json`
  - `multi_run.generated.toml`
  - `sample_manifest.sha256`
- 新增 `create_multi_run_batch_plan(...)` 与 `MultiRunPlanArtifacts`。
- `batch_plan.json` 记录：
  - `manifest_sha256`
  - `generated_config_sha256`
  - `selected_indexes`
  - product / instance / snapshot / region
  - serial execution policy
  - `dry_run`
- `multi_run.generated.toml` 只写非敏感批次元数据，不写 token、secret、
  cloud key 或真实配置内容。
- `--plan-only` 只生成计划产物，不创建 `cases/`，不调用 runner。
- `--dry-run` 写入 batch plan 的 execution policy，但仍不触发云操作。
- `--sample-dir` 扫描生成 manifest 尚未实现，本阶段要求显式传入
  `--manifest`。
- 补齐 selection 边界测试：
  - `--max-cases <= 0` 作为 planning failure 拒绝。
  - 空可选 index 在 batch plan 前拒绝。

本阶段仍未写 `multi_run_state.json` / `multi_run_events.jsonl`，未调用
single-run runner，未触发云操作，未读取样本文件内容。

下一阶段建议进入 Commit 6：实现 event log 和 atomic state writer。
