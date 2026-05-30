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

## 2026-05-30 Commit 6：Event Log + Atomic State Writer

本阶段完成 `multi-run` 第六阶段状态与事件落盘基础：

- 复查并收紧 Commit 5 的计划产物语义：
  - `batch_plan.json` 明确包含 `schema_version = multi-run-plan.v1`、
    `batch_id`、`created_at_utc`、`manifest_sha256`、
    `generated_config_sha256`、`selected_indexes` 和
    `execution.mode = serial`。
  - `generated_config_sha256` 改为按最终写入
    `multi_run.generated.toml` 的 UTF-8 bytes 计算，避免 resume 时出现文本
    规范化歧义。
  - `sample_manifest.jsonl` 会复制进 batch 目录，`batch_plan.json`、
    `multi_run.generated.toml` 和 `multi_run_state.json` 中使用相对 batch root
    的 `sample_manifest.jsonl` 路径。
  - `--plan-only` 与 `--dry-run` 在 execution policy 中分别记录：
    `plan_only` 表示只生成计划产物后退出，`dry_run` 表示未来进入
    scheduler 后也只模拟 / 校验，不调用真实 runner。
- 新增 `multi_run_state.json` 原子写入能力：
  - `write_multi_run_state(...)` 使用临时文件 + replace 写入。
  - `read_multi_run_state_payload(...)` 能识别损坏 JSON 和 schema 不匹配。
  - 初始 state 记录 batch/product/instance/snapshot/region、manifest digest、
    batch plan digest、selected indexes 和 planned case 列表。
- 新增 `multi_run_events.jsonl` append-only 事件能力：
  - `append_next_multi_run_event(...)` 自动递增 `seq`。
  - `read_multi_run_events(...)` 支持读取 JSONL，并在损坏行上报告行号。
  - batch plan 创建时写入 `batch_created` 与 `plan_created` 事件。
- 新增 `tests/test_multi_run_state.py`，覆盖 state 原子写、损坏 state 报错、
  event seq 递增、event log 损坏行号。

本阶段仍未调用 single-run runner，未触发云操作，未读取样本文件内容；
新增 state/event 只服务后续 resume、scheduler 和 aggregate 阶段。

下一阶段建议进入 Commit 7：引入 runner interface 与 fake single-run runner。

## 2026-05-30 Commit 7：Runner Interface + Fake Runner

本阶段完成 `multi-run` 第七阶段 runner 抽象与 fake runner：

- 补查并收紧 Commit 6 的状态基础：
  - `write_multi_run_state()` 从 tmp + replace 升级为 tmp + flush/fsync +
    replace，提高状态文件落盘强度。
  - `append_next_multi_run_event()` 对不存在 / 空事件文件会稳定从
    `seq = 1` 开始。
  - 初始 planned case 现在包含 `sample_index`、`sample_id`、`case_name`、
    `case_status = planned`、`resume_eligible = false`、
    `cleanup_status = not_started`、`summary_status = not_started`、
    `evidence_status = not_started`。
- 新增 `SingleRunRequest`，用于描述 scheduler 后续调用 single-run runner
  所需的 manifest 元数据和 case 目录信息，不包含样本 bytes。
- 新增 `SingleRunRunner` Protocol，固定 runner 接口为
  `run(request) -> SingleRunRunnerResult`。
- 新增 `SingleRunRunnerResult`，将单轮结果规范化为 multi-run 可消费的
  case 状态元数据，并提供 `to_case_state(...)`。
- 新增 `FakeSingleRunRunner` 与 `fake_single_run_result(...)` fixture：
  - `completed`
  - `case_failed`
  - `environment_failed`
  - `timeout`
  - `summary_missing`
  - `cleanup_unknown`
  - `cleanup_restore_failed`
- 新增 `tests/test_multi_run_runner.py`，覆盖 fake runner 调用记录、
  per-index scenario、case failure、environment failure、timeout、summary
  missing、cleanup unknown、cleanup restore failed，以及 request 不包含样本内容。

本阶段仍未接入真实 single-run runner，未执行 scheduler，未触发云操作，
未读取样本文件内容。fake runner 只为后续 serial scheduler 和 failure
classification 提供稳定测试夹具。

下一阶段建议进入 Commit 8：使用 fake runner 串行执行 selected samples。

## 2026-05-30 Commit 8：Serial Scheduler + Fake Runner Execution

本阶段完成 `multi-run` 第八阶段串行调度 MVP：

- 补查并收紧 Commit 7 的 runner contract：
  - `SingleRunRequest` 已包含 `batch_id`、`sample_index`、`sample_id`、
    `case_name`、`sample_ref`、`case_dir`、`product_id`、
    `manifest_sha256`、`batch_plan_sha256`。
  - `SingleRunRunnerResult.failure_kind` 明确区分 `case_failure` 和
    `environment_failure`，后续 stop rule 不依赖模糊 failed 状态。
  - `cleanup_restore_failed` fake fixture 固定映射为
    `failure_kind = environment_failure`、`unsafe_to_continue = true`、
    `manual_intervention_required = true`、`cleanup_status = restore_failed`。
- 新增 `load_batch_plan(...)`，从 `batch_plan.json` 还原 plan / selection /
  execution policy，并校验 schema。
- 新增 `execute_multi_run_batch(...)`：
  - 从 batch root 读取 `batch_plan.json` 和相对路径
    `sample_manifest.jsonl`。
  - 校验 manifest digest 与 batch plan 一致。
  - 按 `selected_indexes` 顺序创建 `cases/<index>_<sha16>/`。
  - 构造 `SingleRunRequest` 并调用 fake runner。
  - 持续写入 `multi_run_state.json` 与 `multi_run_events.jsonl`。
  - 默认 case failure 继续执行后续样本。
  - `failure_policy = stop-on-case-failure` 时遇到 case failure 停止。
  - environment failure / unsafe cleanup 必定停止 batch。
- `multi-run` CLI 在非 `--plan-only` 时会运行 fake scheduler；`--plan-only`
  仍只生成计划产物后退出。
- 新增 `tests/test_multi_run_scheduler.py`，覆盖：
  - selected indexes 按排序后的顺序执行。
  - 每个 case 生成独立目录。
  - case failure 默认继续。
  - `stop-on-case-failure` 停止。
  - environment failure 强制停止。
  - scheduler 写入 preflight、case、single-run、finalize、batch finished
    事件。

本阶段仍未接入真实 single-run runner，未触发云操作，未读取样本文件内容；
fake scheduler 只用于压稳 state/event/failure-policy 语义。

下一阶段建议进入 Commit 9：failure classification 与更完整的状态转换规则。

## 2026-05-30 Commit 9：Failure Classification + Batch Stop Rules

本阶段完成 `multi-run` 第九阶段 failure classification 与状态收口：

- 新增统一 `classify_runner_result(...)` helper：
  - 显式 `failure_kind` 优先。
  - `unsafe_to_continue` / `manual_intervention_required` 视为
    `environment_failure`。
  - `cleanup_status = restore_failed | unknown` 视为
    `environment_failure`。
  - `case_status = failed`、`single_run_status = failed | timeout`、
    `summary_status = missing` 视为 `case_failure`。
- `SingleRunRunnerResult.to_case_state(...)` 通过统一 helper 写入
  `CaseState.failure_kind`，避免调度层和 case 状态分类不一致。
- fake runner 输出新增：
  - `result_source = fake_runner`
  - `simulated = true`
- fake / dry-run 调度结果不再标记为 `resume_eligible`，避免后续 aggregate
  或 resume 把模拟完成误当成真实完成。
- lightweight preflight 事件改名为：
  - `lightweight_preflight_started`
  - `lightweight_preflight_passed`
  该事件只表示 planning 层输入校验通过，不暗示已经检查 Guest Agent、
  Desktop Worker 或云端环境。
- case 目录命名改为基于 manifest `case_name`：
  `cases/<sample_index>_<safe_case_name_prefix>/`，不从文件名反推。
- environment failure / cleanup restore failed 会在顶层 state 写入：
  - `unsafe_to_continue = true`
  - `manual_intervention_required = true`
  - `final_status = stopped_for_environment_failure`
- 新增 / 更新测试覆盖：
  - fake runner simulated 结果不可 resume。
  - 真实 runner 成功结果仍可 resume。
  - cleanup unknown / restore failed 可被统一 helper 推断为
    `environment_failure`。
  - cleanup restore failed 会停止 batch。
  - case 目录使用 manifest `case_name`。
  - lightweight preflight 事件名称。

本阶段仍未接入真实 single-run runner，未触发云操作，未读取样本文件内容；
所有调度执行仍由 fake runner 驱动。

下一阶段建议进入 Commit 10：接入真实 single-run runner adapter 前的
preflight / resume 设计收口。

## 2026-05-30 Commit 10：Aggregate Summary

本阶段完成 `multi-run` 第十阶段 aggregate summary MVP：

- 新增 `multi-run-aggregate-summary.v1` schema。
- batch scheduler 在最终状态落盘后生成：
  - `aggregate_summary.json`
  - `aggregate_summary.md`
- `aggregate_summary.json` 汇总：
  - `selected_samples`
  - `planned_cases`
  - `completed_cases`
  - `evaluable_cases`
  - `not_evaluable_cases`
  - `case_failures`
  - `environment_failures`
  - `environment_stopped`
  - verdict breakdown
  - readiness breakdown
  - case / cleanup / evidence / summary status breakdown
  - case error summary
  - relative case summary / run state / evidence bundle path
- detection rate denominator 只统计可评测 case；`not_evaluable`、case failure、
  environment failure 不进入检测率分母。
- `aggregate_summary.md` 提供简洁人类可读摘要，便于快速查看 batch 结果。
- `multi-run` CLI 在 scheduler 路径输出：
  - `aggregate_summary_path`
  - `aggregate_summary_markdown_path`
- 新增 `aggregate_summary_written` event，并保持最终事件仍为
  `batch_finished`。
- 新增 / 更新测试覆盖：
  - aggregate summary 文件写入。
  - selected samples / evaluable cases / case failures 统计。
  - environment stopped 单独统计。
  - verdict / readiness breakdown。
  - evidence bundle path 使用 batch-relative path。
  - CLI 输出 aggregate summary 路径。

本阶段只聚合 multi-run state 与单轮结果元数据路径，不读取样本本体、不读取
raw product logs、不读取 `configs/real.toml`、不触发云操作。

下一阶段建议进入 Commit 11：resume / rerun / force-rerun 模式。

## 2026-05-30 Commit 11：Resume / Rerun / Force-Rerun

本阶段完成 `multi-run` 第十一阶段 resume / rerun / force-rerun MVP：

- 补查 Commit 10 aggregate summary：
  - `aggregate_summary.json` 已包含 `schema_version`、`batch_id`、
    `manifest_sha256`、`batch_plan_sha256`、`generated_at_utc`。
  - `aggregate_summary.md` 不展示完整 Guest Agent / Desktop Worker URL。
  - fake / dry-run case 已包含 `simulated = true` 与
    `result_source = fake_runner`。
  - detection rate 新增 `simulated` 与 `rate_kind`，fake runner 结果会标记为
    `simulated_detection_rate`，避免误读为真实检测率。
- 新增 `load_existing_multi_run_batch(...)`：
  - 读取既有 `batch_plan.json` 与 `multi_run_state.json`。
  - 校验 manifest digest。
  - 校验 batch plan sha256。
  - 校验 product / instance / snapshot / region。
  - 校验 selected indexes。
  - 校验 state 与 plan 的核心 metadata 一致。
- `execute_multi_run_batch(...)` 新增 `execution_mode`：
  - `run`
  - `resume`
  - `rerun_failed`
  - `force_rerun`
- `resume` 行为：
  - `resume_eligible = true` 的 case 会跳过。
  - 非 resume eligible 的 case 会重新进入 fake runner。
  - `unsafe_to_continue = true` 或 `manual_intervention_required = true`
    会拒绝继续。
- `rerun_failed` 行为：
  - 只重跑 `failure_kind = case_failure` 或 `case_status = failed` 的 case。
- `force_rerun` 行为：
  - 重跑 selected indexes 中的所有 case。
- rerun / resume 的新执行请求会把 `attempt` 增加 1。
- 新增 `case_skipped_by_execution_mode` event，记录 resume/rerun 下被跳过的
  case。
- CLI 接入：
  - `--resume`
  - `--rerun-failed`
  - `--force-rerun`
  三者互斥，且 resume/rerun 模式要求提供 `--batch-id`。
- 新增 / 更新测试覆盖：
  - resume 跳过 completed + cleanup restored 的 case。
  - completed 但 cleanup unknown 且 state 安全时不会被跳过。
  - unsafe batch 拒绝 resume。
  - rerun-failed 只运行 case failure。
  - force-rerun 运行全部 selected case。
  - manifest digest mismatch 拒绝。
  - batch plan sha256 mismatch 拒绝。
  - product 改变拒绝。
  - CLI 拒绝冲突 execution mode。

本阶段仍使用 fake runner，不触发真实云操作，不读取样本文件内容，不读取
`configs/real.toml`。

下一阶段建议进入 Commit 12：platform sample manifest indexer。

## 2026-05-30 Commit 12：Platform Sample Manifest Indexer

本阶段完成 `multi-run` 第十二阶段平台样本目录索引器：

- 先补查并收紧 Commit 11：
  - `rerun_failed` 不会重跑 `failure_kind = environment_failure` 的 case，
    即使其 `case_status = failed`。
  - rerun / resume 的 attempt > 1 会写入
    `cases/<case>/attempts/attempt_002/`，避免覆盖旧 attempt 输出。
  - 保留 completed 但 cleanup unknown 不可 resume-skip 的测试。
- 新增 `build_sample_manifest_from_directory(...)`：
  - 扫描 `raw_sample` 目录。
  - 只处理普通文件。
  - 跳过 symlink / reparse point / 路径逃逸 / ADS 风格名称。
  - 流式计算 sha256 / md5 / size。
  - 按 full sha256 排序生成 1-based `sample_index`。
  - 同 sha256 去重，只生成一个 runnable entry。
  - duplicate 文件名进入 `aliases`。
  - sha16 冲突时通过 `unique_sha_prefixes(...)` 扩展 prefix。
  - 生成 `renamed_filename = 0001_prefix.ext`。
  - 复制 primary 样本到 indexed mirror。
  - 生成 `sample_manifest.jsonl`。
  - 生成 `sample_name_map.txt`。
- `multi-run --sample-dir ... --platform-sample-dir` 在未提供 `--manifest` 时会自动生成：
  - `sample_index/sample_manifest.jsonl`
  - `sample_index/sample_name_map.txt`
  - `sample_index/indexed/`
  然后进入原有 batch planning 流程。
- `--sample-dir` 明确只用于云端平台机索引目录；开发机模式必须使用
  `--manifest`，避免把真实样本放到开发机。
- resume/rerun 模式如果未显式传 `--manifest`，会读取既有
  `batch_root/batch_id/sample_manifest.jsonl`，不重新扫描 `sample_dir`。
- 新增 / 更新测试覆盖：
  - regular file indexing。
  - duplicate sha256 去重与 aliases。
  - sha16 collision prefix 扩展。
  - indexed mirror 生成。
  - `sample_ref` 指向 indexed mirror，不指向 raw_sample 原始路径。
  - indexed mirror 输出已存在时拒绝覆盖。
  - raw_sample 不被修改。
  - 已存在 index 输出时拒绝覆盖。
  - aliases 包含 primary 与 duplicate 原始相对路径。
  - 生成的 manifest 可被现有 loader 读取。
  - CLI `--sample-dir --platform-sample-dir` 可生成 manifest / name map /
    indexed mirror。

本阶段测试只使用临时无害文件。索引器会读取用户显式指定的 `sample_dir`
以计算 hash 和生成镜像，但不执行、不打开解析、不解压样本，不读取
`configs/real.toml`，不触发真实云操作。

补查结论：开发机继续要求 `--manifest`；`--sample-dir` 必须显式确认
`--platform-sample-dir` 后才会索引。manifest 中的 `sample_ref` 指向
`sample_index/indexed/0001_xxx.ext`，不会指向 raw_sample；`aliases` 保留包含
primary 在内的所有同 hash 原始相对路径。

下一阶段建议进入 Commit 13：preflight checks before execution。

## 2026-05-30 Commit 13：Preflight Checks Before Execution

本阶段完成 `multi-run` 第十三阶段 preflight 检查：

- 新增 `MultiRunPreflightCheck` / `MultiRunPreflightReport` /
  `MultiRunPreflightChecker` schema。
- 新增 `preflight_report.json`，在 scheduler 开始 case 前写入。
- preflight 覆盖：
  - batch directory 可写。
  - manifest digest 与 batch plan 一致。
  - selected indexes 都存在且非空。
  - single-run runner 可调用。
  - product profile 存在。
  - instance id / snapshot id / region 基础格式有效。
  - Guest Agent / Desktop Worker URL 形状有效。
  - 同 instance 没有 sibling running / stopping batch。
  - evidence 输出目录可写。
  - generated config 不含 token / secret / password / credential 等敏感字段。
  - batch plan sha256 形状有效。
- 默认 `StaticMultiRunPreflightChecker` 不做网络 I/O，只把 Guest Agent /
  Desktop Worker reachability 标记为 `skipped`，为后续真实 checker 预留接口。
- `execute_multi_run_batch(..., preflight_checker=...)` 支持测试注入 checker。
- preflight 失败时：
  - 写入 `preflight_report.json`。
  - 记录 `preflight_failed` 和 `batch_finished` 事件。
  - `multi_run_state.json` 设置 `batch_state = failed_preflight`。
  - 不进入 scheduler，不调用 runner。
- `BatchPlan` 现在记录 `guest_agent_url` / `desktop_worker_url`，方便 preflight 和后续
 真实 runner 使用。
- 新增 / 更新测试覆盖：
  - preflight report schema 与关键 check。
  - checker 注入失败时不调用 runner。
  - unknown product 在 preflight 阶段失败。
  - plan-only 不生成 preflight report。

本阶段仍未接入真实 single-run runner，不触发真实云操作，不读取
`configs/real.toml`，不执行样本；默认 preflight 也不会主动连接 Guest Agent 或
Desktop Worker。

下一阶段建议进入 Commit 14：real single-run runner adapter。

## 2026-05-30 Commit 14：Real Single-Run Runner Adapter

本阶段完成 `multi-run` 第十四阶段真实 single-run runner adapter：

- 新增 `RealSingleRunRunner`：
  - 将 manifest entry / `SingleRunRequest` 转换为 `SingleRunOptions`。
  - 传入 `product_id` / `instance_id` / `snapshot_id` / `region`。
  - 传入 `guest_agent_url` / `desktop_worker_url`。
  - 将 `sample_ref` 作为 `SingleRunOptions.sample_path`，即后续真实 runner 只使用
    indexed mirror。
  - single-run 输出会映射回 `SingleRunRunnerResult`。
- 真实 runner 启动前会校验 indexed mirror：
  - `sample_ref` 必须存在。
  - 实际 sha256 / md5 / size 必须与 manifest 一致。
  - 不匹配时不调用 single-run，直接记录 case failure。
- `BatchPlan` / `SingleRunRequest` 补充 Guest Agent 和 Desktop Worker URL，
  供真实 runner 与 preflight 使用。
- CLI 行为调整：
  - `--dry-run` 继续使用 `FakeSingleRunRunner`，不会真实调用 single-run。
  - 非 `--dry-run` 且非 `--plan-only` 时使用 `RealSingleRunRunner`。
- 真实 runner 结果映射：
  - 收集 single-run `run_id` / `case_id`。
  - 收集 `run_state.json` 路径。
  - 收集 `case_summary.json` 路径。
  - 收集 evidence bundle 路径。
  - 将 cleanup restore failed 映射为 `environment_failure`、
    `unsafe_to_continue = true`、`manual_intervention_required = true`。
  - 将普通 single-run failure 映射为 `case_failure`。
- 补查 Commit 13 的敏感字段检查：
  - generated config preflight 现在也检查 `authorization` / `bearer`。
  - 不检查普通 `url` / `agent`，避免 Guest Agent URL 误报。
- 新增 / 更新测试覆盖：
  - real runner 把 `sample_ref` 传给 single-run options。
  - real runner 传递 product / Guest Agent / Desktop Worker 参数。
  - digest mismatch 不调用 single-run。
  - cleanup restore failed 映射为 environment failure。
  - `.to_dict()` 不包含样本 bytes。

本阶段自动测试仍使用 mock / fake entrypoint，不触发真实云操作，不读取
`configs/real.toml`，不执行样本。真实云 smoke 应在本 commit 后手工执行。

下一阶段建议进入 Commit 15：redaction check + docs / smoke。
