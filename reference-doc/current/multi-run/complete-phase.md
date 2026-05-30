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
