# Development Guide

本文档面向继续开发 Cloud AV Agent Lab 的同学，说明本地环境、配置约定、腾讯云适配器、dry-run 机制、代理开关和验证流程。

## 开发环境

项目使用独立 conda 环境承载 Python 运行时，但项目依赖不使用 `conda install` 管理，统一使用环境内的 `python -m pip` 或 `python -m uv`。

```powershell
conda create -y -n cloud-av-agent-lab python=3.11 pip
conda activate cloud-av-agent-lab
python -m pip install -e .
python -m pip install ruff
```

如果不激活环境，可以直接调用环境内的 Python：

```powershell
& "C:\Users\Parle\.conda\envs\cloud-av-agent-lab\python.exe" -m unittest discover -s tests
```

在当前 Windows 环境里，多个并行 `conda run` 偶尔会争用临时激活文件。遇到这种情况，优先使用环境内 `python.exe` 直接执行命令。

## 配置文件

示例配置在 `configs/lab.example.toml`。真实开发时建议复制成本地配置：

```powershell
Copy-Item configs/lab.example.toml configs/lab.local.toml
```

`.gitignore` 已忽略 `configs/*.local.toml`、`configs/lab.toml` 和包含 `secret` / `secrets` 的配置文件。不要把真实密钥写进可提交的配置文件。

### 单实例多快照配置模式

现有架构支持“一台 Lighthouse 实例，多个测试快照”的方案。`[[vms]]` 在代码里更准确地说是测试环境 profile，而不是必须一一对应不同云主机。一个 profile 由 `id`、`instance_id`、`baseline_snapshot`、`product_id` 和网络配置组成：

- `instance_id` 可以在多个 profile 中重复，表示它们复用同一台 Lighthouse 实例；
- `baseline_snapshot` 必须区分不同杀软基线，例如腾讯电脑管家基线、火绒基线、360 基线；
- `product_id` 指向当前快照中已安装、需要采集日志和判定结果的杀软产品；
- `vm.id` 只是本地编排 ID，用于 CLI `--vm-id` 和报告分组，不等同于真实实例 ID。

这种模式的前提是串行调度。因为同一台实例同一时间只能处于一个快照状态，后续自动化执行时必须按 profile 逐个恢复快照、启动、测试、采集、再恢复清理，不能对相同 `instance_id` 的多个 profile 并发执行。当前 `TestPipeline` 是顺序构建和顺序执行的；如果以后加入并发调度器，需要按 `instance_id` 加锁。

手动操作时，`--vm-id` 选择的是 profile，`--confirm-instance` 确认的是共用 Lighthouse 实例，`--confirm-snapshot` 必须匹配该 profile 的 `baseline_snapshot`。因此同一实例下切换不同杀软环境时，只需要切换 `--vm-id` 和对应的 `--confirm-snapshot`。

## 腾讯云凭据

腾讯云配置遵循“环境变量 > 配置文件”的优先级。配置文件中可以预留字段，但示例值保持为空：

```toml
[cloud]
mode = "mock"
dry_run = true
region = "ap-guangzhou"
secret_id = ""
secret_key = ""
```

推荐用环境变量提供凭据：

```powershell
$env:TENCENTCLOUD_SECRET_ID="AKIDxxxxxxxx"
$env:TENCENTCLOUD_SECRET_KEY="xxxxxxxx"
$env:TENCENTCLOUD_REGION="ap-guangzhou"
```

实例 ID 可写在 VM 配置里：

```toml
[[vms]]
id = "win10-tencent-manager"
instance_id = "lhins-xxxxxxxx"
```

也可以用环境变量覆盖：

```powershell
$env:TENCENTCLOUD_INSTANCE_ID="lhins-xxxxxxxx"
$env:TENCENTCLOUD_INSTANCE_ID_WIN10_TENCENT_MANAGER="lhins-yyyyyyyy"
```

带 VM ID 后缀的变量优先级最高。后缀规则是把 VM ID 转成大写，并把非字母数字字符替换为 `_`。

## 腾讯云适配器

腾讯云适配器位于 `src/cloud_av_agent_lab/adapters/tencent_cloud.py`。

当前已完成真实接入前的结构准备：

- `TencentCloudLighthouseAdapter`
- `TencentCloudAuth`
- `TencentCloudOperation`
- `VMOperationResponse`
- `CloudProviderError`
- `TencentCloudApiError`
- `TencentCloudConfigError`

已预留的 VM 操作：

- `start_vm`
- `stop_vm`
- `reboot_vm`
- `get_instance_status`
- `restore_snapshot`
- `capture_screenshot`

`mode = "mock"` 时不会访问网络，只返回统一的 `VMOperationResponse`。`mode = "real"` 时进入真实 API 路径，但只要 `dry_run = true`，所有云 API 调用都会被拦截。

真实 API 调用集中在 `TencentCloudLighthouseAdapter._call_api()`，当前已经完成：

- 腾讯云 TC3-HMAC-SHA256 请求签名；
- Lighthouse API action 到请求参数的映射，快照回滚使用 `ApplyInstanceSnapshot`；
- 通过 `NetworkClient` 发起请求；
- 腾讯云错误码到 `CloudProviderError` 的统一转换；
- 真实返回值到 `VMOperationResponse` 的统一转换。

`DescribeInstances` 已增加只读解析层。真实响应会被解析成 `LighthouseInstanceStatus`，并写入 `response.data["InstanceStatus"]`。当前结构化字段包括实例 ID、状态、限制状态、最近操作状态、地域可用区、公私网地址和创建/到期时间；同时会给出 `guest_access`、`start`、`stop`、`reboot`、`restore_snapshot` 的保守允许矩阵。新增写操作或轮询逻辑前，应优先读取这个状态对象，而不是在业务代码里重复解析腾讯云原始 JSON。

2026-05-10 已基于真实查询结果确认 Lighthouse `DescribeInstances` 响应形状，并将脱敏响应保存为 `tests/fixtures/tencent_lighthouse_describe_instances.json`。当前测试覆盖：

- 正常 `RUNNING` 实例可解析为 `guest_access = true`；
- 未知实例状态不会被自动放行；
- 查询不到期望实例 ID 时抛出 `CloudProviderError`；
- real mode 下的 `DescribeInstances` 会保留原始响应，并额外注入结构化 `InstanceStatus`。

只读连通性验证命令：

```powershell
python -m cloud_av_agent_lab cloud-status --config configs/lab.local.toml --vm-id win10-tencent-manager
```

保持 `dry_run = true` 时只会输出 `DescribeInstances` 调用计划。确认凭据、地域、实例 ID 和网络代理无误后，再在本地配置中切换为 `mode = "real"`、`dry_run = false` 进行真实只读请求。

安全生命周期命令：

```powershell
python -m cloud_av_agent_lab cloud-start --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
python -m cloud_av_agent_lab cloud-stop --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
python -m cloud_av_agent_lab cloud-reboot --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx
python -m cloud_av_agent_lab cloud-restore-snapshot --config configs/real.toml --vm-id sg-win10 --confirm-instance lhins-xxxxxxxx --confirm-snapshot snap-xxxxxxxx
```

这些命令默认仍以安全门禁为先。真实执行必须同时满足：

- 配置为 `mode = "real"`；
- 配置为 `dry_run = false`；
- 命令行 `--confirm-instance` 与最终解析出的 Lighthouse 实例 ID 完全一致。

任一条件不满足时，CLI 会强制构造 dry-run 适配器，只打印对应的 `StartInstances`、`StopInstances` 或 `RebootInstances` 计划。真实写操作成功提交后，适配器会调用 `wait_instance_status` 轮询 `DescribeInstances`，默认每 5 秒一次、最多 600 秒；如果轮询中发现 `LatestOperationState = "FAILED"`，会立即抛出 `CloudProviderError` 并停止任务。

`cloud-restore-snapshot` 使用 `ApplyInstanceSnapshot`，并额外要求 `--confirm-snapshot` 与 VM 的 `baseline_snapshot` 完全一致。真实执行前会先查询 `DescribeInstances`：只有状态为 `STOPPED` 且 `LatestOperationState` 稳定时才允许回滚；如果状态为 `RUNNING`，会提示先执行 `cloud-stop`。回滚完成后会继续轮询，如果实例仍为 `STOPPED`，适配器会自动调用 `StartInstances`，直到最终稳定状态为 `RUNNING`。

2026-05-12 已完成以下真实链路验证，验证记录不包含真实实例 ID、快照 ID 或密钥：

- `cloud-stop` 可通过真实 API 关闭 Lighthouse 实例；
- `cloud-restore-snapshot` 可通过真实 API 回滚配置中的基线快照；
- 当 `--confirm-snapshot` 与配置中的 `baseline_snapshot` 不一致时，CLI 会拒绝执行真实回滚。

生命周期命令会把关键进度直接打印到终端。写操作 API 被腾讯云接受后，先输出：

```text
API Request Accepted, RequestId: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

之后每次状态查询都会输出当前实例状态和已等待时间：

```text
Polling instance lhins-xxxxxxxx: state=RUNNING, latest_operation=RebootInstances, latest_operation_state=SUCCESS, waited=5.1s
```

## Dry-run 机制

`dry_run = true` 是当前默认安全设置。开启后，适配器不会调用 `_call_api()`，而是返回并打印清晰的计划信息：

```text
[DRY-RUN] Would call: StartInstances with Params: {'InstanceIds': ['lhins-xxxx']}
```

测试中通过 mock `_call_api()` 验证了即使 `mode = "real"`，dry-run 也不会触发真实逻辑。

后续新增任何会改变云资源状态的接口，都必须先经过 dry-run 分支，再进入真实调用。

## 临时代理

开发期代理配置位于 `[network.proxy]`：

```toml
[network.proxy]
enabled = false
type = "socks5"
host = "127.0.0.1"
port = 7890
```

代理只用于本地控制面跨网络访问云 API 或云端 Guest Agent，默认关闭。业务代码不应直接读取代理配置，应统一依赖 `NetworkClient`。

正式交付时可以保持 `enabled = false`，或者删除 `[network.proxy]` 配置和 `src/cloud_av_agent_lab/network/proxy.py`。代理不改变样本安全边界，本地仍不保存、不下载、不执行病毒样本。

## Guest Agent 接入方向

云端 Guest Agent 的 HTTP 客户端在 `src/cloud_av_agent_lab/adapters/guest_agent_client.py`，服务端 MVP 在 `src/cloud_av_agent_lab/guest_agent_server/`。所有本地到云端 Guest Agent 的请求仍统一走 `NetworkClient`，这样腾讯云 API、Guest Agent 和临时代理都使用同一网络出口配置。

当前 MVP 已实现客户端、CLI 和 Guest Agent Server：

```powershell
python -m cloud_av_agent_lab guest-health --config configs/lab.local.toml --vm-id sg-win10
python -m cloud_av_agent_lab guest-prepare-case --config configs/lab.local.toml --sample-id case-001 --vm-id sg-win10
python -m cloud_av_agent_lab guest-upload-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager --file C:\Temp\eicar.txt
python -m cloud_av_agent_lab guest-case-status --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
python -m cloud_av_agent_lab guest-case-report --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
python -m cloud_av_agent_lab guest-execute-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager
```

`[guest_agent]` 默认 `enabled = false`。启用后，`GuestAgentClient` 会从 `token_env` 指定的环境变量读取 token，并发送 `Authorization: Bearer ...`。如果启用但 token 缺失，会抛出清晰配置错误。`prepare-case` 只发送 case、VM、产品和样本云端 URI/metadata，不发送本地样本路径，也不触发样本下载或执行。

上传链路只面向 EICAR 或无害测试文件。`guest-upload-sample` 额外要求本地环境变量 `CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN`，并通过 `X-Upload-Token` 发送给云端 Guest Agent。该命令只读取用户显式传入的 `--file`，不运行、不打开、不解压、不扫描、不解析文件内容；服务端只保存文件和 `sample.json` metadata，不执行样本。

`POST /cases/{case_id}/sample` 只负责 HTTP 传输、写盘和 metadata 保存。只要写入成功，它会立即返回 `upload_state = "uploaded"`，不会在后端 sleep，也不会同步轮询文件状态。这样可以避免请求超时，也避免 Guest Agent 的观测行为影响 Defender 删除文件。

`GET /cases/{case_id}/status` 才负责做一次实时 `Path.exists` / `Path.stat` 元信息检查，并动态更新 `case_state.json` 和 `sample.json`。`guest-upload-sample` 在收到上传成功后，会在本地 CLI 进程中先等待 10 秒，然后每 2 秒自动调用一次 `guest-case-status` 的底层逻辑，最多观察到 30 秒；如果期间状态变成 `removed_after_save`，会立即停止轮询并报告拦截成功。后续如果杀软处理较慢，可以手动重复运行 `guest-case-status`。状态语义如下：

- `stable`：状态查询时文件仍存在且 size 与上传 metadata 一致；
- `removed_after_save`：文件曾保存成功，但状态查询时已经消失，EICAR 被杀软删除时通常会出现这种状态；
- `locked_or_busy`：文件存在，但短时间内无法读取元信息，可能正在被安全软件处理。

`removed_after_save` 和 `locked_or_busy` 都不是 HTTP 上传失败。CLI 会给出 warning，并建议需要时使用 `guest-case-status` 查询 `case_state.json`、`sample.json` 和最近事件。只有鉴权失败、case 不存在、路径非法或磁盘写入失败等基础设施问题才会让命令失败退出。

`guest-case-report` 会生成并读取云端 case 工作目录下的 `case_report.json`，汇总投送阶段 metadata、execution 观测摘要和最近事件：case/sample/vm/product 标识、上传状态、saved/removed/locked/stable 标记、文件名、哈希、大小、root PID、退出码、子进程摘要和时间戳。它不读取样本内容，不读取 Defender 或其他杀软日志；杀软日志采集和检测判定属于后续评测阶段。

2026-05-13 实测结论：Microsoft Defender 在云端环境下对 EICAR 的处理时间存在波动，单次状态查询容易得到“处决前”的假 `stable`。因此不要把耗时观测放回 `POST /sample`，也不要把单次 `guest-case-status` 当作最终判定。当前推荐策略是 `guest-upload-sample` 自动执行动态轮询：先等待 10 秒，再每 2 秒查询一次，最多 30 秒；期间一旦出现 `removed_after_save` 即判定拦截成功，只有完整窗口结束后仍为 `stable` 才判定样本存活。

受控触发阶段目前只实现默认关闭的 action 骨架：

```toml
[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

`POST /cases/{case_id}/actions` 只接受白名单 action，不接受任意路径、命令、shell/cmd/PowerShell 或参数。`guest-execute-sample` 默认发送 `dry_run_execute_uploaded_sample`，只校验当前 case 已登记上传样本的 metadata、路径归属和可选 sha256，不启动进程；显式传入 `--real-action` 时请求 `execute_uploaded_sample`。真实启动只有在云端 Guest Agent 启用 execution 且请求携带正确 `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN` 时才会发生，服务端会再次 `os.path.exists` 确认文件仍存在，并使用 `subprocess.Popen([sample_path], cwd=sample_dir, shell=False)` 直接启动当前 case 已登记文件。标准输入/输出/错误会重定向到 `DEVNULL`，Windows 下使用 `CREATE_NO_WINDOW` 并关闭继承句柄。root PID、启动时间和路径归属校验结果会写入 `case_state.json`，并记录 `execution_started` 事件。

`guest-execute-sample --real-action` 启动成功后会改为执行状态轮询，不再检查 proof 文件。CLI 默认每 2 秒查询一次 `GET /cases/{case_id}/execution-status`，最多 60 秒；每次输出 root PID、状态和子进程数量。Guest Agent 只做低侵入式只读元信息快照：只观测当前 case 记录的 `root_pid` 及其子进程，不接受任意 PID，不缓存 `psutil.Process` 对象，不读取样本内容或杀软日志，也不能阻碍 Defender 或其他安全软件终止进程。`terminated_or_disappeared` 只表示进程已退出或不可观察，不能单独当作杀软拦截结论。完整设计见 `docs/EXECUTION_MODEL.md`。

Guest Agent CLI 报错按来源归因：`[Local Check]` 表示本地配置或环境变量问题，`[Network]` 表示无法连接云端 Guest Agent，`[Remote Agent]` 表示云端鉴权或业务拒绝，例如 execution 未启用。

下一步可以使用 EICAR 或无害命令 exe 验证触发链路，例如只在云端 case 的 sample 目录内写入 `execution_proof.txt` 的测试程序。该 proof 文件只适合早期联调，不再作为长期评测模型；后续评测应结合投送状态、执行观测和只读安全产品日志证据。仍然不引入有害样本，本地也不能执行、打开、解压、扫描或解析任何样本文件。

Guest Agent 的职责应限制在云端隔离 VM 内：

- 从云对象存储拉取样本；
- 在受控目录和超时内执行测试；
- 采集杀软日志、UI 截图、行为观测；
- 上传结构化结果到云端 artifact bucket。

不要把样本下载到本地主机。

协议和单实例串行锁设计见 `docs/GUEST_AGENT.md`。Windows 免 Python 打包和 Lighthouse 手动部署流程见 `docs/GUEST_AGENT_DEPLOYMENT.md`。

## 测试与校验

推荐在提交前运行：

```powershell
python -m ruff format --check --no-cache src tests
python -m ruff check --no-cache src tests
python -m unittest discover -s tests
python -m compileall src tests
python -m cloud_av_agent_lab validate --config configs/lab.example.toml
python -m cloud_av_agent_lab plan --config configs/lab.example.toml
python -m cloud_av_agent_lab cloud-status --config configs/lab.example.toml --vm-id win10-tencent-manager
python -m cloud_av_agent_lab cloud-reboot --config configs/lab.example.toml --vm-id win10-tencent-manager --confirm-instance lhins-replace-tencent-manager
python -m cloud_av_agent_lab cloud-restore-snapshot --config configs/lab.example.toml --vm-id win10-tencent-manager --confirm-instance lhins-replace-tencent-manager --confirm-snapshot snap-clean-tencent-manager
```

当前重点测试文件：

- `tests/test_config.py`：配置解析与安全配置。
- `tests/test_network.py`：代理开启/关闭时的网络客户端行为。
- `tests/test_tencent_cloud_adapter.py`：腾讯云适配器初始化和响应结构。
- `tests/test_adapter.py`：环境变量注入、实例 ID 覆盖、real+dry-run 拦截、状态轮询、快照回滚前置校验与回滚后启动闭环。
- `tests/test_cli.py`：生命周期命令写操作确认门禁、快照确认门禁。
