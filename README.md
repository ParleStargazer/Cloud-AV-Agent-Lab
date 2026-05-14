# Cloud AV Agent Lab

Cloud AV Agent Lab 是一个本地自动化编排框架，用于把 AI Agent、云端 Windows 虚拟机、杀软告警采集和结构化报告串成闭环。项目边界很明确：本地只保存代码、配置、任务状态和报告，不保存真实病毒样本，不下载真实病毒样本，不在本地执行任何样本。开发阶段只允许用户显式指定 EICAR 或无害测试文件，通过 HTTP 上传到云端 Guest Agent 做链路验证。

现有目录里的 `Spore` 可作为透明可控的 AI Agent 外壳，`vmware-mcp` 可作为本地 VMware 适配器参考。本项目新增的根目录框架优先面向云端虚拟机，后续也可以用同样接口接入 VMware。

## 安全边界

- 真实病毒样本只能以云端对象引用形式出现，例如 `cos://bucket/redacted/case-001.bin`；开发阶段可用用户显式指定路径的 EICAR 或无害测试文件验证上传链路。
- 本地 `.gitignore` 已忽略 `samples/`、`malware/` 和常见可执行样本后缀。
- 框架只提供编排、日志解析、结果判断和报告生成接口，不提供样本、不生成样本、不包含绕过或规避杀软的逻辑。
- 真实测试中的投递、运行、截图、日志采集必须由云端隔离虚拟机和 Guest Agent 完成，本地控制面不执行样本。
- 每个测试用例都应恢复到基线快照，避免样本之间互相污染。

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

## 快速开始

```powershell
cloud-av-agent-lab validate --config configs/lab.example.toml
cloud-av-agent-lab plan --config configs/lab.example.toml
cloud-av-agent-lab cloud-status --config configs/lab.example.toml --vm-id win10-tencent-manager
cloud-av-agent-lab report-template --config configs/lab.example.toml --out reports/template.md
```

如果不安装包，也可以临时设置 `PYTHONPATH=src` 后使用 `py -m cloud_av_agent_lab ...`。

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

`src/cloud_av_agent_lab/adapters/tencent_cloud.py` 提供 `TencentCloudLighthouseAdapter`，预留了 Lighthouse 实例开机、关机、重启、快照回滚、实例状态查询和截图产物引用接口。当前实现支持 `mock` / `real` mode，并已接入腾讯云 API 3.0 的 TC3-HMAC-SHA256 签名和统一请求路径。

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

真实 API 调用集中在 `TencentCloudLighthouseAdapter._call_api()`：

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

Guest Agent 运行在云端隔离 Windows 主机内，本地只通过统一 `NetworkClient` 发起 HTTP 控制面调用。MVP 支持 `/health`、`/system-info`、`/prepare-case`、`/cases/{case_id}/sample`、`/cases/{case_id}/status`、`/cases/{case_id}/report` 和受控 `/cases/{case_id}/actions`，用于连通性、系统信息、无害工作目录准备、EICAR/无害测试文件上传、上传后状态观测、投送阶段报告生成，以及默认关闭的受控触发。不接触真实病毒样本，不暴露任意命令执行接口；默认工作流不执行样本，真实触发必须显式启用 execution 并限定为当前 case 已登记的上传文件。

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

该真实触发只会在云端 Guest Agent 中启动当前 case 已登记的上传文件，记录 PID 和 `execution_started` 事件；本地控制面仍不执行该文件。

上传成功不等于文件最终保留。EICAR 可能被杀毒软件立即删除，这是预期的安全产品处理行为。Guest Agent 会记录 `stable`、`removed_after_save` 或 `locked_or_busy` 等状态，并写入 case 状态和事件日志；`removed_after_save` 和 `locked_or_busy` 不被视为传输失败。

上传接口只负责写盘并立即返回，耗时等待放在本地 CLI：`guest-upload-sample` 会在上传成功后先等待 10 秒，然后每 2 秒轮询一次状态，最多观察到 30 秒；一旦出现 `removed_after_save` 会立即报告拦截成功。需要继续观察时，可以手动重复运行 `guest-case-status`。

每个 case 会维护 `case_state.json`、`events.jsonl` 和 `case_report.json`。`guest-case-report` 只汇总投送阶段 metadata，不读取 Defender 或其他杀软日志，不读取样本内容。

受控触发能力默认关闭。`guest-execute-sample` 默认请求 `dry_run_execute_uploaded_sample`，只校验当前 case 已登记上传样本的 metadata 和路径归属，不启动样本进程；显式加 `--real-action` 时会请求 `execute_uploaded_sample`，只有云端 Guest Agent 启用 execution 且提供正确执行 token 时，才会直接启动当前 case 的已登记上传文件。该真实执行路径使用 `subprocess.Popen([sample_path], cwd=sample_dir, shell=False)`，不接受任意路径、命令、shell/cmd/PowerShell 或参数。下一步验证可以使用 EICAR 或无害命令 exe；依旧不引入有害样本，本地也仍不执行任何样本。详细协议和单实例串行锁设计见 [GUEST_AGENT.md](docs/GUEST_AGENT.md)，受控触发模型见 [EXECUTION_MODEL.md](docs/EXECUTION_MODEL.md)，Windows 免 Python 部署见 [GUEST_AGENT_DEPLOYMENT.md](docs/GUEST_AGENT_DEPLOYMENT.md)。

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
