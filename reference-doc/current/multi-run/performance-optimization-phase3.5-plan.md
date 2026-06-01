# Multi-Run Performance Optimization Phase 3.5 Plan

> 本文档从 `performance-optimization-plan.md` 中拆出，专门记录 Phase 3 半封口后暴露的修正项。
>
> Phase 3 原文档继续保留 execution-stage product probe 的设计、实施记录和 `20260601-110658` 半封口验证结论。

## 1. 背景

`runs/batch_20260601-110658_tencent-pc-manager` 完成后，验证结果说明：

- fastmode / deferred cleanup / burn-after-use 主链路基本闭环；
- post-execution probe 对强信号 case 仍有效；
- 当前批次并未启用 `execution_product_probe_enabled`，因此不能作为完整 Phase 3 验收；
- 0039 的失败点不是云端执行或收集失败，而是本地 lock 写入鲁棒性问题；
- 上传后状态查询仍存在固定 10 秒前置等待，导致 `upload_status_timeout_seconds < 10` 时仍无法缩短实际等待。

Phase 3.5 目标是在不扩大功能范围的前提下，对这些工程边界做中低风险收口。

## 2. 安全边界

本阶段必须继续遵守：

- 不读取 `configs/real.toml`；
- 不触发真实云 API；
- 不读取、上传或执行样本文件；
- 不新增 PowerShell / cmd / wevtutil / shell=True / os.system 路径；
- 不改变 collector / evaluator 的 verdict 语义；
- 不让 product probe 直接替代正式 collection；
- 不让 product probe 直接改变最终 verdict；
- 不因性能优化绕过 cleanup / evidence / run_state 持久化边界。

## 3. 关键事实修正

### 3.1 0039 原始现场

0039 的原始现场经人工确认：

- batch 完成后，`sample_index/indexed/0039_71904da18ae322ae.exe` 仍保留原始样本；
- 该文件 sha256 与 manifest 完全一致；
- 后续为了开发机安全，该 indexed 样本由人工手动剔除。

因此，0039 不应被判定为 burn-after-use 误删或异常删除。

0039 的真实异常点是 single-run 在 case summary 阶段触发本地 lock 文件写入失败：

```text
[WinError 5] 拒绝访问。:
...\.locks\lhins-0541mqp7.lock.tmp
-> ...\.locks\lhins-0541mqp7.lock
```

0039 在失败前已经完成：

- upload：`stable`
- execution：`exited_cleanly`
- post-execution probe：45 次，最终 `activity_observed`
- collection：`collected`
- evidence salvage：`saved`
- cleanup：`restored`

0039 不满足 burn-after-use 的 summary 成功条件，因此没有执行 burn 是符合当前规则的。

### 3.2 速度对比必须绑定参数上下文

不同 batch 的耗时对比不能只看 average / p95。必须同时记录：

```text
fastmode
cleanup_strategy / effective cleanup strategy
settling_cooldown_seconds
upload_status_timeout_seconds
post_execution_collection_delay_seconds
product_probe_enabled
post_execution_probe_interval_seconds
execution_product_probe_enabled
execution_product_probe_interval_seconds
post_execution_quarantine_delay_seconds
```

否则容易把参数变化误判为架构优化收益。

## 4. Phase 3.5 修正目标

### 4.1 probe 开关联动

现状：

```text
product_probe_enabled = true
execution_product_probe_enabled = false
```

用户在引导界面选择“启用产品 probe”时，直觉上期望执行层和等待层都使用产品侧信号。

目标：

```text
product_probe_enabled = true
-> post-execution probe enabled
-> execution_product_probe_enabled = true
```

规则：

- 只在产品已注册 compatible probe 时启用；
- 产品未注册 probe 时：

```text
product_probe_available = false
product_probe_enabled = false
execution_product_probe_enabled = false
```

- 不显示重复或不可用的 probe 选项；
- 不新增第二个需要用户理解的 probe 开关；
- CLI 参数仍可保留内部覆盖能力，但交互引导默认应走联动语义。

### 4.2 上传后状态查询去掉固定 10 秒前置等待

现状：

```text
upload HTTP 200
-> 固定 sleep 10s
-> upload status polling
```

问题：

- `upload_status_timeout_seconds` 设置为小于 10 秒时，实际仍至少等待 10 秒；
- 性能分析中 `upload_sample` 固定偏高；
- 用户设置的 timeout 和实际行为不一致。

目标：

```text
upload HTTP 200 / saved_once
-> 立即开始 upload status polling
-> 每 interval 查询状态
-> timeout 内根据 stable / removed_after_save / locked_or_busy 决策
```

要求：

- 不再有无条件 10 秒前置 sleep；
- `upload_status_timeout_seconds` 表示上传后状态观测总窗口；
- 如果需要保守等待，使用 polling interval / timeout 表达，而不是隐藏固定 sleep；
- `removed_after_save` / `locked_or_busy` 仍不是基础设施失败；
- 不改变 upload endpoint 的安全边界，不读取样本内容。

### 4.3 summary / aggregate 增加参数上下文

目标：

- 在 batch plan / aggregate summary / markdown 中记录关键时间参数；
- 人类阅读 `aggregate_summary.md` 时能区分：
  - 参数导致的速度变化；
  - 架构优化导致的速度变化；
  - 云端启动波动导致的速度变化。

建议 JSON 字段：

```json
{
  "runtime_parameters": {
    "fastmode": true,
    "cleanup_strategy": "per_case",
    "effective_cleanup_strategy": "deferred_between_cases",
    "settling_cooldown_seconds": 3.0,
    "upload_status_timeout_seconds": 3.0,
    "post_execution_collection_delay_seconds": 45.0,
    "product_probe_enabled": true,
    "post_execution_probe_interval_seconds": 1.0,
    "execution_product_probe_enabled": true,
    "execution_product_probe_interval_seconds": 1.0,
    "post_execution_quarantine_delay_seconds": 3.0
  }
}
```

Markdown 面向快速阅读，可显示简版：

```text
Runtime parameters:
- fastmode: enabled
- upload status timeout: 3s
- post-execution default delay: 45s
- execution-stage probe: enabled
- post-execution probe: enabled
- quarantine short delay: 3s
```

### 4.4 lock 写入鲁棒性收口

问题：

0039 在证据链已完成后，因为 `.lock.tmp -> .lock` 触发 `[WinError 5]` 而进入 case failure。

目标：

- 减少 Windows 文件锁、杀软、索引器造成的偶发 replace 失败；
- 避免 heartbeat 写失败掩盖已完成的 upload / execution / collection / evidence salvage / cleanup；
- 不弱化 instance 并发保护。

可选修正方向：

1. lock atomic write 增加短重试与 backoff；
2. 使用更唯一的 temp 文件名，避免同名 `.lock.tmp` 被残留文件干扰；
3. 写入前清理当前进程可安全识别的 stale temp；
4. heartbeat 写失败在已完成主要 case flow 后降级为 warning；
5. multi-run 外层已持有调度锁时，评估 single-run 内层锁是否可以隔离到 run 子目录，避免跨层状态相互干扰。

## 5. Commit 拆分计划

Phase 3.5 应按中低风险小步提交。每个 commit 都应能单独通过相关测试，且失败时容易回滚。

### Commit 1：probe 开关联动与 plan/schema 测试

目标：

- 将交互式 `product_probe_enabled` 语义调整为“一次启用，两层 probe 联动”；
- 更新 single-run / multi-run plan 写入：

```text
product_probe_enabled = true
execution_product_probe_enabled = true
```

- 未注册 probe 的产品保持：

```text
product_probe_available = false
product_probe_enabled = false
execution_product_probe_enabled = false
```

建议修改范围：

```text
src/cloud_av_agent_lab/cli.py
src/cloud_av_agent_lab/orchestration/multi_run.py
tests/test_cli.py
tests/test_multi_run_schema.py
tests/test_multi_run_runner.py
```

测试重点：

- multi-run 引导启用 probe 后，batch_plan 中 `execution_product_probe_enabled = true`；
- single-run 引导启用 probe 后，传入 `SingleRunOptions.execution_product_probe_enabled = true`；
- 不支持 probe 的产品不显示或不启用 probe；
- 旧 plan 未显式设置 `execution_product_probe_enabled` 时仍可兼容读取；
- 不改变 collector / evaluator。

建议验证：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest tests.test_cli tests.test_multi_run_schema tests.test_multi_run_runner
```

### Commit 2：upload status immediate polling

目标：

- 移除 upload 成功后的固定 10 秒前置等待；
- upload HTTP 200 后立即进入 status polling；
- `upload_status_timeout_seconds` 成为真实观测窗口。

建议修改范围：

```text
src/cloud_av_agent_lab/orchestration/single_run.py
tests/test_single_run.py
```

测试重点：

- `upload_status_timeout_seconds = 3` 时不会固定等待 10 秒；
- `stable` 仍可进入 execution；
- `removed_after_save` 仍跳过 execution 并进入 collection / summary / evidence；
- `locked_or_busy` 仍按现有策略处理；
- 轮询日志清楚显示 elapsed / timeout；
- 不读取样本内容。

建议验证：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest tests.test_single_run
```

### Commit 3：aggregate summary 记录 runtime parameters

目标：

- 在 `aggregate_summary.json` 中记录 runtime parameters；
- 在 `aggregate_summary.md` 中显示简版参数；
- 让后续速度分析能区分参数变化和架构收益。

建议修改范围：

```text
src/cloud_av_agent_lab/orchestration/multi_run.py
tests/test_multi_run_aggregate.py 或现有 aggregate 相关测试
```

测试重点：

- JSON 包含 `runtime_parameters`；
- Markdown 不泄露 Guest Agent URL / Desktop Worker URL；
- fastmode、upload timeout、post-execution delay、probe 开关和 interval 均可见；
- 旧 batch state 缺字段时不崩溃。

建议验证：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest tests.test_multi_run_runner tests.test_multi_run_schema
```

### Commit 4：Windows-safe lock atomic write retry

目标：

- 对 lock 文件 `_write_json_atomic()` 增加 Windows 友好的短重试；
- 使用唯一 temp 文件名；
- 清理当前目标对应的 stale temp 时必须保守；
- 不改变并发锁语义。

建议修改范围：

```text
src/cloud_av_agent_lab/orchestration/locks.py
tests/test_locks.py 或新增锁相关测试
```

测试重点：

- 正常 acquire / heartbeat / release 行为不变；
- `replace()` 首次失败后可重试成功；
- 重试耗尽时仍抛出清晰异常；
- stale `.tmp` 不会误删有效 `.lock`；
- 不引入 shell / PowerShell / cmd。

建议验证：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest tests.test_single_run tests.test_multi_run_runner
```

### Commit 5：lock failure attribution review

目标：

- 在 Commit 4 smoke 后再决定是否需要调整错误归因；
- 如果 lock heartbeat 仍可能在 case 证据链已完成后导致 case failure，则考虑将特定 heartbeat 失败降级为 warning；
- 不应把真正的 acquire lock failure 降级，因为那仍是并发保护失败。

建议修改范围：

```text
src/cloud_av_agent_lab/orchestration/single_run.py
src/cloud_av_agent_lab/orchestration/locks.py
tests/test_single_run.py
tests/test_multi_run_runner.py
```

允许降级的条件必须严格：

```text
case_started = true
collection completed or evidence salvage saved
cleanup restored or emergency handling recorded
failure source = heartbeat / lock refresh
failure source != initial acquire lock
```

禁止降级：

- 初始 acquire lock 失败；
- 发现 active lock；
- unsafe_to_continue；
- cleanup restore failed；
- emergency stop failed。

建议验证：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest tests.test_single_run tests.test_multi_run_runner
python -m unittest discover -s tests
```

### Commit 6：小批量 smoke 与文档封口

目标：

- 用 Tencent PC Manager 小批量验证 Phase 3.5；
- 确认启用 probe 后：

```text
product_probe_enabled = true
execution_product_probe_enabled = true
```

- 确认 upload 阶段不再固定等待 10 秒；
- 确认 aggregate summary 记录 runtime parameters；
- 确认 lock retry 没有破坏正常 acquire / release。

建议 smoke：

```text
multi-run --product tencent-pc-manager --max-cases 5
```

观察重点：

- `batch_plan.json`
- `multi_run_state.json`
- `aggregate_summary.json`
- `aggregate_summary.md`
- 单 case `run.log`
- 单 case `run_state.json`

文档收口：

- 本文档追加每个 commit 的完成记录；
- Phase 3 原文档只保留半封口结论和 Phase 3.5 跳转。

## 6. 验收标准

Phase 3.5 完成后应满足：

- 交互式启用 product probe 时，execution-stage probe 与 post-execution probe 同步启用；
- 未注册 probe 的产品自动回退到固定等待，不显示误导性 probe 选项；
- upload 成功后立即进入 status polling，不再固定等待 10 秒；
- batch summary 能看到关键时间参数上下文；
- lock 写入在 Windows 上对短暂拒绝访问具备重试能力；
- 0039 类似场景不再轻易因 heartbeat/replace 偶发失败掩盖已完成证据链；
- 不改变 collector / evaluator verdict 语义；
- 不读取 `configs/real.toml`；
- 不触发真实云 API；
- 不读取、上传或执行样本文件；
- 不新增 shell / PowerShell / cmd / subprocess 路径。

## 7. 最终验证命令

完成全部 commits 后运行：

```text
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest discover -s tests
python -m compileall src tests
python -m cloud_av_agent_lab validate --config configs/lab.example.toml
git diff --check -- . ':!configs/real.toml'
```

额外建议：

```text
rg "execution_product_probe_enabled|product_probe_enabled|upload_status_timeout" src tests reference-doc
rg "lock.tmp|heartbeat|replace\\(" src tests
rg "real.toml|TOKEN|SECRET|AKID|TENCENTCLOUD" src tests reference-doc
rg "shell=True|os.system|PowerShell|cmd.exe|wevtutil|subprocess" src tests
```

## 8. Phase 3.5 实施记录

后续每完成一个 commit，在本节追加：

```text
### Commit N：标题

完成时间：

完成内容：

验证结果：

边界确认：
```

### Commit 1：probe 开关联动与 plan/schema 测试

完成时间：2026-06-01

完成内容：

- 更新 `single-run` / `multi-run` 的 `--enable-product-probe` 语义：
  - 启用产品 probe 时，同步启用 execution-stage probe；
  - `product_probe_enabled = true` 时写入 / 传递 `execution_product_probe_enabled = true`；
  - 未注册 probe 的产品仍保持本地提前失败或自动关闭 probe。
- 更新 CLI 帮助和交互提示，说明产品侧轻量 probe 会作用于 execution observation 与 post-execution delay 两个阶段。
- 更新测试断言，确保：
  - multi-run batch plan 写入 `execution_product_probe_enabled = true`；
  - generated config 写入 `execution_product_probe_enabled = true`；
  - single-run options 传入 `execution_product_probe_enabled = true`。

验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest tests.test_cli tests.test_multi_run_schema tests.test_multi_run_runner
Ran 92 tests in 0.824s
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实云 API。
- 未读取、上传或执行样本文件。
- 未改变 collector / evaluator verdict 语义。
- 未新增 shell / PowerShell / cmd / subprocess 路径。

### Commit 2：upload status immediate polling

完成时间：2026-06-01

完成内容：

- 将 `DEFAULT_UPLOAD_INITIAL_WAIT_SECONDS` 从 `10.0` 调整为 `0.0`。
- 上传 HTTP 成功后默认立即进入 post-upload status polling。
- 保留内部 `upload_initial_wait_seconds` 字段作为兼容扩展点，但默认行为不再包含隐藏的 10 秒前置等待。
- 更新 upload polling 日志：
  - 默认记录 `upload saved; polling post-upload state immediately`；
  - 只有内部显式设置初始等待时才记录 `waiting Ns before polling`。
- 新增测试覆盖：
  - `upload_status_timeout_seconds = 0` 时仍会立即查询 `case_status`；
  - 不会调用固定 10 秒 sleep；
  - run log 不再出现 `upload saved; waiting 10s`。

验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest tests.test_single_run
Ran 35 tests in 15.279s
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实云 API。
- 未读取、上传或执行样本文件。
- 未改变 upload endpoint、collector 或 evaluator 的安全边界。
- 未新增 shell / PowerShell / cmd / subprocess 路径。

### Commit 3：aggregate summary 记录 runtime parameters

完成时间：2026-06-01

完成内容：

- `aggregate_summary.json` 新增 `runtime_parameters` 区域。
- `runtime_parameters` 从 batch 根目录下的 `batch_plan.json` 读取非敏感 execution 参数：
  - `fastmode`
  - `cleanup_strategy`
  - `effective_cleanup_strategy`
  - `settling_cooldown_seconds`
  - `upload_status_timeout_seconds`
  - `post_execution_collection_delay_seconds`
  - `product_probe_enabled`
  - `post_execution_probe_interval_seconds`
  - `execution_product_probe_enabled`
  - `execution_product_probe_interval_seconds`
  - `post_execution_quarantine_delay_seconds`
- `aggregate_summary.md` 新增 `Runtime Parameters` 小节，显示便于人工对比的简版参数。
- Markdown 不展示 Guest Agent URL / Desktop Worker URL，避免把运行地址混入外部摘要。
- 旧 batch 缺少 `batch_plan.json` 或 execution 字段时，runtime parameters 仍可生成默认安全值，不影响 summary 构建。

验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest tests.test_multi_run_runner tests.test_multi_run_schema tests.test_multi_run_scheduler
Ran 46 tests in 1.940s
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实云 API。
- 未读取、上传或执行样本文件。
- 未向 Markdown 暴露 Guest Agent / Desktop Worker URL。
- 未改变 collector / evaluator verdict 语义。
- 未新增 shell / PowerShell / cmd / subprocess 路径。

### Commit 4：Windows-safe lock atomic write retry

完成时间：2026-06-01

完成内容：

- `orchestration/locks.py` 的 lock JSON 原子写入改为唯一临时文件名：
  - 不再固定使用 `*.lock.tmp`；
  - 临时文件格式包含随机 UUID，降低 stale temp 与杀软/索引器短暂占用导致的冲突概率。
- 对 `temp_path.replace(lock_path)` 增加短重试：
  - 默认 3 次；
  - 默认重试间隔 0.05 秒；
  - 重试耗尽后继续抛出原始 `OSError`，不吞掉真实锁失败。
- 重试失败时会尽量删除本次创建的唯一临时文件。
- 新增测试覆盖：
  - 第一次 `replace()` 失败、第二次成功时能够完成写入；
  - 重试耗尽时抛出 `PermissionError`；
  - 失败后不残留本次 `.tmp` 文件；
  - 原有 acquire / force unlock 行为不变。

验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest tests.test_single_run tests.test_multi_run_runner
Ran 51 tests in 17.874s
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实云 API。
- 未读取、上传或执行样本文件。
- 未改变 instance lock 的并发保护语义。
- 未新增 shell / PowerShell / cmd / subprocess 路径。

### Commit 5：lock failure attribution review

完成时间：2026-06-01

完成内容：

- 在 `single_run.py` 中新增 `_heartbeat_lock()` 辅助函数。
- 保持严格边界：
  - 初始 `acquire_lock()` 失败不降级；
  - collection 之前的 heartbeat 失败不降级；
  - cleanup restore / emergency stop 失败不降级；
  - unsafe / manual intervention 语义不变。
- 仅在 collection 阶段已经完成后，`case_summary` / `export_evidence` 前的 lock heartbeat `OSError` 会降级为 warning：
  - 继续生成 `case_summary.json` / `case_summary.md`；
  - 继续导出 evidence bundle；
  - `run_state.json` 记录 `lock_heartbeat_status = "warning"` 和具体错误；
  - 最终状态按 warning 处理，而不是误判为 case failure。
- 新增测试：
  - 模拟 collection 完成后 heartbeat 发生 `PermissionError`；
  - 验证 summary / evidence 仍然落盘；
  - 验证最终状态为 `completed_with_warnings`；
  - 验证 lock release 仍然执行。

验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest tests.test_single_run tests.test_multi_run_runner
Ran 52 tests in 20.267s
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实云 API。
- 未读取、上传或执行样本文件。
- 未降低初始锁获取、active lock、cleanup failure、unsafe_to_continue 的优先级。
- 未新增 shell / PowerShell / cmd / subprocess 路径。

### Commit 6：Phase 3.5 final verification

完成时间：2026-06-01

封口结论：

- Phase 3.5 的四个修正点已经完成：
  - probe 引导统一为 execution-stage + post-execution waiting 双阶段语义；
  - upload 状态查询移除固定 10 秒等待，文件落地后立即开始轮询；
  - aggregate summary / Markdown 记录关键运行参数，避免跨 batch 耗时误读；
  - lock/run_state 写入与 post-collection heartbeat 归因完成 Windows 兼容收口。
- 当前实现没有改变 collector / evaluator 的产品证据语义。
- 当前实现没有把 fastmode / probe 结果解释为最终 verdict；最终结论仍由 summary/evaluator 输出决定。
- 0039 类似的“证据已产生但本地状态写入/lock refresh 偶发失败”场景已经降低误判概率：
  - run_state 原子写入已有 Windows 短重试；
  - lock heartbeat 写入已有唯一临时文件与短重试；
  - collection 完成后的 heartbeat 失败不会再阻断 summary/evidence 持久化。

完整验证结果：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff format --check --no-cache src tests
140 files already formatted

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m ruff check --no-cache src tests
All checks passed!

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m unittest discover -s tests
Ran 485 tests in 26.899s
OK

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m compileall src tests
OK

C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe -m cloud_av_agent_lab validate --config configs/lab.example.toml
ok: 2 samples, 4 products, 4 VMs, 8 planned cases

git diff --check -- . ':!configs/real.toml'
OK
```

边界确认：

- 未读取 `configs/real.toml`。
- 未触发真实腾讯云 API。
- 未读取、上传或执行样本文件。
- 未新增 shell / PowerShell / cmd / subprocess 路径。
- 未改变安全产品 collector / evaluator 的职责边界。
