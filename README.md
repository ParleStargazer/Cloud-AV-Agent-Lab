# Cloud AV Agent Lab

Cloud AV Agent Lab 是一个本地自动化编排框架，用于把 AI Agent、云端 Windows 虚拟机、杀软告警采集和结构化报告串成闭环。项目边界很明确：本地只保存代码、配置、任务状态和报告，不保存真实病毒样本，不下载真实病毒样本，不在本地执行任何样本。开发阶段只允许用户显式指定 EICAR 或无害测试文件，通过 HTTP 上传到云端 Guest Agent 做链路验证。

现有目录里的 `Spore` 可作为透明可控的 AI Agent 外壳，`vmware-mcp` 可作为本地 VMware 适配器参考。本项目新增的根目录框架优先面向云端虚拟机，后续也可以用同样接口接入 VMware。

## 安全边界

- 真实病毒样本只能以云端对象引用形式出现，例如 `cos://bucket/redacted/case-001.bin`；开发阶段可用用户显式指定路径的 EICAR 或无害测试文件验证上传链路。
- 本地 `.gitignore` 已忽略 `samples/`、`malware/` 和常见可执行样本后缀。
- 框架只提供编排、日志解析、结果判断和报告生成接口，不提供样本、不生成样本、不包含绕过或规避杀软的逻辑。
- 真实测试中的投递、运行、截图、日志采集必须由云端隔离虚拟机和 Guest Agent 完成，本地控制面不执行样本。
- 每个测试用例都应恢复到基线快照，避免样本之间互相污染。
- 一旦样本在测试云实例内被投送或触发，该实例内的静态文件、日志、工具、解释器、Guest Agent、Desktop Worker 和临时目录都默认不可信；默认 evidence bundle 只回传脱敏后的结构化 text-format 观察结果，不把 raw binary、raw SQLite DB、WAL/SHM 或可执行文件搬回本地。

## 目录结构

```text
.
├── agent-skills/av-test-analysis/   # Spore/Agent 可复用分析 skill 草案
├── configs/lab.example.toml         # 云端测试矩阵示例配置
├── docs/                            # 架构、安全与开发说明
├── reports/                         # 运行报告输出目录
├── src/cloud_av_agent_lab/          # 本地编排框架
└── tests/                           # 无外部依赖的基础测试
```

## 开发文档

详细开发流程见 [DEVELOPMENT.md](docs/DEVELOPMENT.md)，包括 conda 环境、腾讯云凭据、dry-run、临时代理、Guest Agent 接入方向和测试命令。

## 开发环境

本项目使用独立 conda 环境承载 Python 运行时，但不使用 `conda install` 管理项目依赖。依赖统一使用环境内的 `python -m pip` 或 `python -m uv`。

```powershell
conda create -y -n cloud-av-agent-lab python=3.11 pip
conda activate cloud-av-agent-lab
python -m pip install -e .
```

不激活环境时也可以使用：

```powershell
conda run -n cloud-av-agent-lab python -m pip install -e .
```

如果团队后续选择 uv，也请通过 Python 模块入口调用：

```powershell
python -m pip install uv
python -m uv pip install -e .
```

### Editable Install 更新注意

云端或平台机如果通过源码 checkout 运行控制面、Guest Agent 或 Desktop Worker，更新代码后必须在该 checkout 根目录重新执行本地 editable install：

```powershell
git pull
python -m pip install -e ".[guest-agent,desktop-worker]"
```

如果使用 `requirements.txt` 复现环境，也必须在仓库根目录执行，因为其中的项目安装项指向当前 checkout：

```powershell
python -m pip install -r requirements.txt
```

不要在 `requirements.txt` 或部署脚本里使用固定 commit 的 editable 安装，例如 `-e git+https://...@<commit>`。这种写法会让运行环境继续加载旧提交，导致本地源码、云端 Agent 和传回的 `run_state.json` / evidence bundle 行为不一致。排查时先确认实际加载路径：

```powershell
python -c "import cloud_av_agent_lab.orchestration.single_run as s; print(s.__file__)"
```

## 快速开始

```powershell
cloud-av-agent-lab validate --config configs/lab.example.toml
cloud-av-agent-lab plan --config configs/lab.example.toml
cloud-av-agent-lab cloud-status --config configs/lab.example.toml --vm-id win10-tencent-manager
cloud-av-agent-lab report-template --config configs/lab.example.toml --out reports/template.md
```

如果不安装包，也可以临时设置 `PYTHONPATH=src` 后使用 `py -m cloud_av_agent_lab ...`。

## Single-Run 单轮测试入口

`single-run` 是面向普通用户的第一版简化入口。它不要求提前手写完整 TOML，而是通过参数或交互输入收集 Lighthouse instance id、baseline snapshot id、region、样本名称、显式本地 EICAR/无害文件路径、产品 ID 和 Guest Agent URL，然后自动生成一次运行所需的非敏感配置和产物目录。

最小交互入口：

```powershell
python -m cloud_av_agent_lab single-run
```

也可以用参数运行：

```powershell
python -m cloud_av_agent_lab single-run `
  --instance-id lhins-xxxxxxxx `
  --snapshot-id lhsnap-xxxxxxxx `
  --region ap-singapore `
  --sample-name eicar-001 `
  --sample-path C:\Temp\eicar.txt `
  --product huorong `
  --guest-agent-url http://x.x.x.x:8080 `
  --desktop-worker-url http://127.0.0.1:8001
```

默认会按真实单轮流程生成 `mode = "real"`、`dry_run = false` 的 `lab.generated.toml`，并在启动前提示用户确认：

```text
此操作会进行实例真实操作，请务必检查实例id和快照id是否正确，并了解此操作的风险，是否确认？
```

确认后，single-run 会完全按用户输入的 instance id 和 snapshot id 进行生命周期操作，并把这两个值作为内部适配器确认值，不再要求用户重复输入 `--confirm-instance` / `--confirm-snapshot`。如果只是演练流程，请显式加 `--dry-run`；此时生成配置会回到 `mock + dry_run`，云写操作、Desktop Worker gate 和受控 action 都不会真实执行。

真实 single-run 现在会生成 `[guest_agent.desktop_worker]` 并默认要求云端 Control Agent 的 `/worker/status` 返回 ready 后才进入投送阶段。Desktop Worker 是为了解决 Windows Session 0 执行问题而引入的执行层：Control Agent 继续常驻 Session 0，真实 `execute_uploaded_sample` 会由 Control Agent 签发短期 execution lease 后转发给运行在交互式桌面 session 的 Worker；Worker 只执行当前 case 已登记的 `.exe`，并就近观测执行状态。需要诊断旧环境时可以临时使用 `--disable-desktop-worker-gate`，但这会回到不可判定的环境风险模型。详细见 [DESKTOP_WORKER.md](docs/DESKTOP_WORKER.md)。

single-run 会在 `prepare-case` 成功后、`upload-sample` 之前自动调用 `security_product_readiness`，把产品日志可观察性写入 `run_state.stages.security_product_readiness` 和云端 case workspace。该阶段目前是 warning-only：`ready` 记录为 `ok`，`partial`、`not_ready`、`unknown`、`unsupported` 或 API 调用失败都只记录 warning 并继续投送样本，不触发 strict mode。后续 evaluator 只把 readiness 用作 `no_detection_observed` 的保守闸门：只有 `ready` 才允许输出未检出，明确检出证据不受影响。

产品选择现在集中在入口解析：`--product` 可显式选择 `huorong` 或 `windows-defender`。`guest-prepare-case`、`guest-collect-logs`、`guest-check-security-product-readiness` 和 `guest-security-product-readiness-status` 都要求显式传入 `--product`；帮助文本会提示普通 Huorong 流程使用 `huorong`。交互式 `single-run` 会在生成配置前首先询问 product，默认提示为 `huorong`；非交互 single-run 也应显式传入 `--product`。`guest-prepare-case` 会把解析后的 `product_id` 写入云端 `case_state.json`，single-run 同时写入 `run_state.json.selected_product_id`。后续 readiness、collection、summary 和 evidence 都读取同一 case 绑定产品；显式 `--product` 与 case 绑定值冲突时会失败，不会静默跨产品覆盖。

真实触发仍然只允许云端 Guest Agent 执行当前 case 已登记上传文件，且必须满足云端 execution 开关和 token 校验；本地控制面仍不执行样本。single-run 会根据上传轮询结果决定是否触发：只有 `stable` 才请求执行接口；`removed_after_save`、`locked_or_busy` 或未知状态会记录为执行跳过，并继续 collection、summary 和 evidence 导出。若执行接口返回样本已不存在或启动失败这类业务状态，也会作为本轮观察结果继续收集证据，而不是直接中断整个流程。

summary 现在按保守原则生成：只有产品日志明确匹配当前 case 时才输出
`detected_or_blocked`；collection 失败、未收集、partial、readiness 未确认、Worker busy、lease
失败、样本缺失、sha256 不匹配、进程消失但没有产品日志证据等情况，不会被写成
`no_detection_observed`，而是进入 `inconclusive` 或 `execution_not_observed`。
CLI 默认只打印简洁结论，`guest-case-summary --json` 可查看完整
`case_summary.json`、决策输入和关键时间线。

每次运行会创建：

```text
runs/
  20260516-153012_eicar-001__huorong/
    lab.generated.toml
    run_state.json
    run.log
    case_summary.json
    case_summary.md
    case_evidence_<case_id>.zip
```

`run_state.json` 会记录每一步状态、样本 SHA-256、证据导出状态、清理状态和错误，并额外维护 `stages.environment/delivery/execution/collection/summary/evidence/cleanup` 结构，方便后续 multi-run 聚合。`run.log` 每行带 `[instance_id][run_id]` 上下文。`runs/.locks/<instance_id>.lock` 防止同一 Lighthouse 实例被并发操作，过期或心跳陈旧的锁会被归档，必要时可使用 `--force-unlock`。证据导出发生在结尾快照回滚之前；如果流程异常，会尝试短超时 fast-fail 证据打捞，随后再进行清理回滚。若回滚失败，会尝试 emergency stop，并在最终输出中提示人工介入。

证据包采用 `evidence-bundle.v2` + Redaction MVP 策略：默认只包含 guest-reported、已脱敏的 text-format artifacts，例如 case 元数据、`sample/sample.json`、summary、events、Worker 状态、`case_security_product_readiness.json` 和 normalized evidence；不包含上传样本本体、token、云密钥、`configs/real.toml`、递归 zip、symlink/junction、未知根目录、raw SQLite/WAL/SHM、`security-product-readiness/` 下的 readiness snapshot 或其他未脱敏二进制产品日志。raw 证据如确需保留，应走停止实例、云快照/克隆盘、干净取证环境只读挂载、可信 collector/redactor 导出的离线取证链路。

Manifest 的 `redaction_policy` 会自描述当前边界：redaction 已启用、text-format 文件会被脱敏、binary 文件不会尝试脱敏而是默认排除、hash 关联字段会保留，并记录 bundle 文件数和大小上限。产品语义级脱敏由 collector 声明，evidence exporter 只做全局兜底脱敏与打包安全裁决；当前不开放 `include_raw_binary` 或关闭 redaction 的配置项。

## 工作流

1. 在云端创建 Windows 基线镜像，安装目标杀软和日志采集组件。
2. 为每个杀软基线创建只读快照，例如 `snap-clean-tencent-manager`。
3. 在 `configs/lab.example.toml` 中登记云厂商、快照、杀软产品、样本云端引用和测试矩阵。
4. 使用 `validate` 检查配置是否违反本地安全边界。
5. 使用 `plan` 审核将要执行的样本 x 杀软矩阵。
6. 接入云厂商适配器和客户机自动化适配器后执行真实云端流程。
7. 采集杀软日志、UI 告警截图、进程/文件/注册表等观测结果，生成结构化报告。

## 核心模块

- `config.py`：读取 TOML 配置并做形状校验。
- `core/contracts.py`：定义样本引用、杀软配置、虚拟机配置、测试用例和结果模型。
- `core/safety.py`：拦截本地样本路径、非云端隔离等不安全配置。
- `core/pipeline.py`：构建测试矩阵，并定义恢复快照、云端投递、执行、采集、报告的流程骨架。
- `adapters/`：云厂商和客户机自动化接口，已包含腾讯云 Lighthouse 适配器、Guest Agent 客户端和只读计划适配器。
- `network/`：统一网络客户端和临时代理支持，业务适配器只依赖 `NetworkClient`。
- `detectors/`：基于日志关键字和行为观测的通用判定逻辑。
- `reporting/markdown.py`：输出检出率、差异样本和逐用例结果。

## 腾讯云 Lighthouse 接入

`src/cloud_av_agent_lab/adapters/tencent_cloud.py` 是对外兼容 facade，继续导出 `TencentCloudLighthouseAdapter`、`parse_lighthouse_instance_status`、`build_tc3_headers` 等稳定 API。具体实现已拆入 `src/cloud_av_agent_lab/adapters/tencent_lighthouse/`：鉴权、TC3 签名、响应解析、状态模型和 adapter 主逻辑分模块维护。当前实现支持 `mock` / `real` mode，并已接入腾讯云 API 3.0 的 TC3-HMAC-SHA256 签名和统一请求路径。

`mode = "mock"` 时只返回统一的 `VMOperationResponse`。`mode = "real"` 且 `dry_run = true` 时会拦截所有云 API 调用，并输出类似：

```text
[DRY-RUN] Would call: StartInstances with Params: {'InstanceIds': ['lhins-xxxx']}
```

`VMOperationResponse` 至少包含 `status`、`task_id`、`message`、`action`、`params`、`data`、`dry_run` 和 `provider`，后续真实 API 返回也应保持同一结构。

`DescribeInstances` 的真实返回会额外解析出结构化 `InstanceStatus`，保留在 `response.data["InstanceStatus"]` 中。它会提取实例 ID、状态、限制状态、最近操作状态、公私网地址等关键字段，并给出保守的操作判断：`guest_access`、`start`、`stop`、`reboot`、`restore_snapshot`。后续接入写操作、轮询或 Guest Agent 前，应先复用这份只读状态校验。

当前已用一次真实 `DescribeInstances` 查询的脱敏响应固化为测试 fixture，覆盖 `RUNNING` / `NORMAL` / `SUCCESS` 的可访问状态，以及未知状态阻断逻辑。这里的判断偏保守：只有实例处于稳定状态时，后续流程才会继续考虑 Guest Agent 访问或写操作；未知状态不会被自动放行。

示例配置采用“一台 Lighthouse 实例，多个测试快照”的模式：多个 `[[vms]]` 测试 profile 可以共用同一个 `instance_id`，但分别绑定不同 `baseline_snapshot` 和 `product_id`。这适合实例数量不足时复用单台云主机。注意该模式必须串行调度，不能对同一 `instance_id` 的多个 profile 并发执行。

### 腾讯云鉴权配置

配置文件中预留了字段，但示例值保持为空：

```toml
[cloud]
mode = "mock"
dry_run = true
region = "ap-guangzhou"
secret_id = ""
secret_key = ""
```

加载优先级为：环境变量 > 配置文件。开发和交付时优先使用环境变量，不要把真实密钥写入 TOML：

```powershell
$env:TENCENTCLOUD_SECRET_ID="AKIDxxxxxxxx"
$env:TENCENTCLOUD_SECRET_KEY="xxxxxxxx"
$env:TENCENTCLOUD_REGION="ap-guangzhou"
```

实例 ID 可写在 VM 配置中：

```toml
[[vms]]
id = "win10-tencent-manager"
instance_id = "lhins-xxxxxxxx"
```

也可以用环境变量覆盖，适合临时调试：

```powershell
$env:TENCENTCLOUD_INSTANCE_ID="lhins-xxxxxxxx"
$env:TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER="lhins-yyyyyyyy"
```

其中带 VM ID 后缀的变量优先级最高，后缀会把 VM ID 转成大写并把非字母数字替换为 `_`。

真实 API 调用集中在 `TencentCloudLighthouseAdapter._call_api()`，实现位于 `adapters/tencent_lighthouse/adapter.py`：

- 使用已解析的 `TencentCloudAuth` 凭据；
- 使用 TC3-HMAC-SHA256 生成请求签名；
- 通过 `NetworkClient` 发起云 API 请求；
- 将 `Response.Error` 转换为 `TencentCloudApiError`；
- 按产品基线补充 Lighthouse 实例与系统盘快照的 API 映射，快照回滚使用 `ApplyInstanceSnapshot`。

只读连通性验证可以使用：

```powershell
cloud-av-agent-lab cloud-status --config configs/lab.local.toml --vm-id win10-tencent-manager
```

当配置仍为 `dry_run = true` 时，该命令只打印 `DescribeInstances` 的 dry-run 计划；将本地配置改为 `mode = "real"` 且 `dry_run = false` 并设置好环境变量后，才会发起真实只读 API 请求。

安全生命周期命令用于开发阶段手动控制云主机：

```powershell
cloud-av-agent-lab cloud-start --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
cloud-av-agent-lab cloud-stop --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
cloud-av-agent-lab cloud-reboot --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
cloud-av-agent-lab cloud-restore-snapshot --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx --confirm-snapshot snap-xxxxxxxx
```

真实写操作必须同时满足 `mode = "real"`、`dry_run = false`，并且 `--confirm-instance` 与解析后的 Lighthouse 实例 ID 完全一致。否则命令只打印 `[DRY-RUN]` 计划，不会调用写操作 API。写操作成功提交后会轮询 `DescribeInstances`，默认每 5 秒检查一次，直到达到目标状态；如果 `LatestOperationState` 变为 `FAILED`，命令会立即中断并报错。

`cloud-restore-snapshot` 额外要求 `--confirm-snapshot` 与 VM 配置中的 `baseline_snapshot` 完全一致。真实回滚前会先调用 `DescribeInstances` 做前置校验：只有实例状态为 `STOPPED` 且无进行中任务时才允许调用 `ApplyInstanceSnapshot`。如果实例仍为 `RUNNING`，命令会提示先停止实例。快照回滚完成后如果 Lighthouse 没有自动启动实例，适配器会继续调用 `StartInstances` 并轮询到最终稳定状态 `RUNNING`。

真实写操作被腾讯云接受后，会先输出请求确认行，随后输出轮询进度：

```text
API Request Accepted, RequestId: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Polling instance lhins-xxxxxxxx: state=RUNNING, latest_operation=RebootInstances, latest_operation_state=SUCCESS, waited=5.1s
```

## 开发期代理

`configs/lab.example.toml` 中的 `[network.proxy]` 是临时代理配置，用于开发阶段本地主机跨网络访问腾讯云 API 或云端 Guest Agent。默认 `enabled = false`，此时程序不会注入任何代理参数，行为与无代理版本一致。

支持的代理类型为 `http`、`https`、`socks5`。代理逻辑集中在 `src/cloud_av_agent_lab/network/proxy.py` 和 `src/cloud_av_agent_lab/network/client.py`，业务代码只依赖统一的 `NetworkClient`。正式交付时可以保持 `enabled = false`，或者删除 `[network.proxy]` 配置和 `network/proxy.py` 模块后替换为直连客户端。

代理只影响控制面网络访问，不改变样本安全边界：本地仍不保存、不下载、不执行病毒样本。

## Agent 集成

`agent-skills/av-test-analysis/SKILL.md` 是一个 Spore/Claude Skill 风格的分析 skill 草案。它的职责是读取已经采集好的结构化结果，辅助判断：

- 哪些产品产生了拦截或隔离信号；
- 哪些关键行为被阻断，例如持久化、提权、注入、网络外联；
- 哪些场景属于“竞品检出但目标产品未检出”；
- 报告结论是否有证据支撑。

Agent 不应直接操作本地样本，也不应生成规避检测建议。

## Guest Agent MVP

Guest Agent 运行在云端隔离 Windows 主机内，本地只通过统一 `NetworkClient` 发起 HTTP 控制面调用。MVP 支持 `/health`、`/system-info`、`/prepare-case`、`/cases/{case_id}/sample`、`/cases/{case_id}/status`、`/cases/{case_id}/report`、`/cases/{case_id}/summary`、`/cases/{case_id}/evidence-bundle`、`/cases/{case_id}/execution-status`、受控 `/cases/{case_id}/actions`、`/cases/{case_id}/security-product-readiness/{product_id}`，以及 `/cases/{case_id}/collection/{product_id}` 日志收集接口，用于连通性、系统信息、无害工作目录准备、安全产品测试前只读就绪检查、EICAR/无害测试文件上传、上传后状态观测、case 报告、保守评测摘要、脱敏后的 guest-reported 证据包、默认关闭的受控触发、低侵入式执行观测和产品日志归一化。不接触真实病毒样本，不暴露任意命令执行接口；默认工作流不执行样本，真实触发必须显式启用 execution 并限定为当前 case 已登记的上传文件。

配置默认关闭：

```toml
[guest_agent]
enabled = false
base_url = "http://127.0.0.1:8080"
token_env = "CLOUD_AV_GUEST_AGENT_TOKEN"
timeout_seconds = 10

[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

启用后 token 从 `token_env` 指定的环境变量读取，不写入配置文件。CLI：

```powershell
python -m cloud_av_agent_lab guest-health --config configs/lab.local.toml --vm-id sg-win10
python -m cloud_av_agent_lab guest-prepare-case --config configs/lab.local.toml --sample-id case-001 --vm-id sg-win10
python -m cloud_av_agent_lab guest-upload-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager --file C:\Temp\eicar.txt
python -m cloud_av_agent_lab guest-case-status --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
python -m cloud_av_agent_lab guest-case-report --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
python -m cloud_av_agent_lab guest-execute-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager
python -m cloud_av_agent_lab guest-check-security-product-readiness --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --product huorong
python -m cloud_av_agent_lab guest-collect-logs --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --product huorong
python -m cloud_av_agent_lab guest-check-security-product-readiness --config configs/lab.local.toml --vm-id win10-windows-defender --case-id case-001__windows-defender --product windows-defender
python -m cloud_av_agent_lab guest-collect-logs --config configs/lab.local.toml --vm-id win10-windows-defender --case-id case-001__windows-defender --product windows-defender
python -m cloud_av_agent_lab guest-case-summary --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong
python -m cloud_av_agent_lab guest-export-evidence --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --output .\artifacts\case_evidence_case-001__huorong.zip
```

最小端到端验证流程可以使用 EICAR 或无害命令 exe。以下命令只展示控制面顺序，真实值请替换为本地配置中的 `vm-id`、`sample-id` 和准备好的 `case-id`：

```powershell
$env:CLOUD_AV_GUEST_AGENT_TOKEN="replace-with-agent-token"
$env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN="replace-with-upload-token"

python -m cloud_av_agent_lab guest-prepare-case `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id>

python -m cloud_av_agent_lab guest-upload-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id> `
  --case-id <case-id> `
  --file C:\Temp\harmless-proof.exe

python -m cloud_av_agent_lab guest-case-report `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --case-id <case-id>

python -m cloud_av_agent_lab guest-execute-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id> `
  --case-id <case-id>

python -m cloud_av_agent_lab guest-execution-status `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --case-id <case-id>
```

`guest-upload-sample` 会在上传成功后自动等待 10 秒，并每 2 秒轮询一次状态，最多观察 30 秒。`guest-execute-sample` 默认是 dry-run，只验证 metadata、sha256 和路径归属，不启动进程。

如需在云端手动验证无害命令 exe 的真实触发，需要先用 `--enable-execution-actions` 启动云端 Guest Agent，并在云端和本地都设置 `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN`；本地配置也必须设置 `[guest_agent.execution].enabled = true`。满足这些条件后，才可以显式传入 `--real-action`：

```powershell
$env:CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN="replace-with-execution-token"
python -m cloud_av_agent_lab guest-execute-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id> `
  --case-id <case-id> `
  --real-action
```

该真实触发只会在云端 Guest Agent 中启动当前 case 已登记的上传文件，记录 root PID、启动时间、路径归属校验结果和 `execution_started` 事件；本地控制面仍不执行该文件。触发成功后 CLI 会自动轮询 `guest-execution-status` 的底层接口，默认每 2 秒查询一次，最多 60 秒，记录 `running`、`exited_cleanly`、`exited_with_error`、`terminated_or_disappeared` 或 `timeout_still_running` 等执行现象。

上传成功不等于文件最终保留。EICAR 可能被杀毒软件立即删除，这是预期的安全产品处理行为。Guest Agent 会记录 `stable`、`removed_after_save` 或 `locked_or_busy` 等状态，并写入 case 状态和事件日志；`removed_after_save` 和 `locked_or_busy` 不被视为传输失败。

上传接口只负责写盘并立即返回，耗时等待放在本地 CLI：`guest-upload-sample` 会在上传成功后先等待 10 秒，然后每 2 秒轮询一次状态，最多观察到 30 秒；一旦出现 `removed_after_save` 会立即报告拦截成功。需要继续观察时，可以手动重复运行 `guest-case-status`。

每个 case 会维护 `case_state.json`、`events.jsonl` 和 `case_report.json`。`guest-case-report` 汇总投送阶段 metadata 和 execution 区域，但不读取 Defender 或其他杀软日志，不读取样本内容。

安全产品就绪检查新增最小闭环：`security_product_readiness` 与 `collectors` 并列，运行在投送前、collection 前，只做低侵入只读检查，不解析拦截日志、不读取样本内容、不启动/停止/修复安全产品。Huorong MVP 检查 `C:\ProgramData\Huorong\sysdiag` 与 `log.db` 是否存在，并把 `log.db` best-effort 复制到当前 case 的 `security-product-readiness\huorong\` 目录后只读检查副本 metadata。Windows Defender readiness 使用 Windows Event Log reader 抽象检查 Defender Operational channel 的日志可观察性；本地测试仍使用 fake reader，不读取真实 Event Log。结果写入 `case_security_product_readiness.json`、`case_state.security_product_readiness`、`case_report.security_product_readiness` 和 `events.jsonl`。single-run 会在 `prepare-case` 后、`upload-sample` 前 warning-only 调用 readiness，所有状态都继续后续流程；evaluator 只在准备输出 `no_detection_observed` 时检查 readiness，`ready` 放行，其余状态转为 `inconclusive`。Evidence bundle 只纳入脱敏后的 `case_security_product_readiness.json`；`security-product-readiness\huorong\log.db*` readiness snapshot 仍按 raw product log snapshot 排除。`ready` 只代表日志可观察链路具备最低条件，`protection_state` 仍为 `unknown`，不证明实时防护已开启或一定会检出。CLI 为 `guest-check-security-product-readiness --product huorong|windows-defender`，详细模型见 [SECURITY_PRODUCT_READINESS.md](docs/SECURITY_PRODUCT_READINESS.md)。

日志收集、简易评测和证据导出阶段已经形成 MVP：杀毒软件日志收集框架定义 collector 插件和统一证据 schema；collector 通过 registry 按 `product_id` 接入，新增产品需遵循 [PRODUCT_ONBOARDING.md](docs/PRODUCT_ONBOARDING.md) 的 readiness、collector、证据归因和 artifact 策略；火绒 collector 是首个产品实现；Guest Agent 暴露日志收集接口、简易结论报告接口和证据包导出接口；本地 CLI 对应提供 `guest-collect-logs`、`guest-case-summary` 和 `guest-export-evidence`。

收集阶段新增 `guest-collect-logs --product huorong|windows-defender`，通过云端 Guest Agent 读取已选择产品的日志并输出 `case_collection.json`。火绒 collector 会先把 `C:\ProgramData\Huorong\sysdiag\log.db`、`log.db-shm`、`log.db-wal` 复制到当前 case 的 `collection\huorong\` artifact 目录，再只读打开 SQLite 并自动发现最新的 `HrLogV3_*` 表。Windows Defender collector 通过 reader 抽象查询 Defender Operational channel，pywin32 缺失时返回结构化 `failed/unknown`，不是 500。输出包含统一时间线、时间窗口、normalized product-log events 和保守 verdict：只有产品日志证据匹配当前 case 的 hash、sample path、root/child PID 或时间窗口时才判定 `intercepted`；单独的文件消失或进程不可观察不会被直接判定为杀软拦截。设计见 [COLLECTION_MODEL.md](docs/COLLECTION_MODEL.md)。

Evaluation / Evidence Export MVP 在 collection 之后运行。collector 只负责产品日志收集、产品语义归一化和 artifact 语义声明；`evaluation` 模块汇总投送、执行、readiness 和 collection 证据，生成保守的 `case_summary.json` / `case_summary.md`，CLI 命令为 `guest-case-summary`。CLI 默认输出类似 `case_summary.md` 的简洁结论；需要完整结构时显式加 `--json`。summary timeline 会剔除重复轮询事件，只保留 upload/execution 状态变化、collection 边界和产品日志证据；完整审计流水仍保留在 `events.jsonl`。`evidence` exporter 生成可归档的 redacted guest-reported `case_evidence_<case_id>.zip`，CLI 命令为 `guest-export-evidence`。证据包只包含脱敏后的 text-format artifacts：`manifest.json`、`case_state.json`、`case_report.json`、`case_collection.json`、`case_security_product_readiness.json`、`case_summary.json`、`case_summary.md`、`events.jsonl`、`sample/sample.json` 和 normalized evidence；不包含上传样本本体、token、环境变量、云密钥、真实云配置文件、`security-product-readiness/` readiness snapshot 或 raw copied log DB。manifest 会记录 `trust_model = dirty_instance_untrusted`、`forensic_grade = false`、`raw_binary_included = false`、redaction policy、redacted files 和被排除 raw artifact 的 guest-reported 元数据。verdict 仍保持保守：产品日志有明确证据才给出 `detected_or_blocked`，单独的文件消失只会被汇总为 `suspiciously_removed`，进程消失不会单独解释为杀软拦截；只有 readiness 为 `ready` 时才允许 `no_detection_observed`。

受控触发能力默认关闭。`guest-execute-sample` 默认请求 `dry_run_execute_uploaded_sample`，只校验当前 case 已登记上传样本的 metadata 和路径归属，不启动样本进程；显式加 `--real-action` 时会请求 `execute_uploaded_sample`，只有云端 Guest Agent 启用 execution、提供正确执行 token、Desktop Worker ready，且 Worker 校验短期单次 execution lease 后，才会启动当前 case 的已登记 `.exe`。真实启动发生在 Desktop Worker 内，使用 `subprocess.Popen([sample_path], cwd=sample_dir, shell=False)`、`DEVNULL` 标准流、`CREATE_NO_WINDOW`、`close_fds=True` 和最小化环境；不接受任意路径、命令、shell/cmd/PowerShell 或参数。执行观测是低侵入式只读元信息快照，只观测当前 case 的 root PID 及子进程，不缓存进程对象，不阻碍 Defender 或其他安全软件终止进程。下一步验证可以使用 EICAR 或无害命令 exe；依旧不引入有害样本，本地也仍不执行任何样本。proof 文件只作为早期联调辅助，长期评测需要结合投送状态、执行观测和只读安全产品日志证据。详细协议和单实例串行锁设计见 [GUEST_AGENT.md](docs/GUEST_AGENT.md)，受控触发模型见 [EXECUTION_MODEL.md](docs/EXECUTION_MODEL.md)，收集模型见 [COLLECTION_MODEL.md](docs/COLLECTION_MODEL.md)，Windows 免 Python 部署见 [GUEST_AGENT_DEPLOYMENT.md](docs/GUEST_AGENT_DEPLOYMENT.md)。

## 后续接入点

- 云厂商适配器：实现 `CloudVmAdapter`，对接 Lighthouse 快照恢复、开关机、截图、隔离网络。
- 客户机适配器：实现 `GuestAutomationAdapter`，在云端 VM 内完成从云对象拉取样本、启动测试、采集日志。
- 产品配置：为腾讯电脑管家、火绒、360 等维护独立日志路径、UI 标题和告警关键字。
- 报告增强：加入人工测试基线对比、误报/漏报复核字段和趋势统计。

## 本地验证

```powershell
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest discover -s tests
python -m compileall src tests
python -m cloud_av_agent_lab validate --config configs/lab.example.toml
python -m cloud_av_agent_lab cloud-status --config configs/lab.example.toml --vm-id win10-tencent-manager
python -m cloud_av_agent_lab cloud-reboot --config configs/lab.example.toml --vm-id win10-tencent-manager --confirm-instance lhins-replace-tencent-manager
python -m cloud_av_agent_lab cloud-restore-snapshot --config configs/lab.example.toml --vm-id win10-tencent-manager --confirm-instance lhins-replace-tencent-manager --confirm-snapshot snap-clean-tencent-manager
```
