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
```

这些命令默认仍以安全门禁为先。真实执行必须同时满足：

- 配置为 `mode = "real"`；
- 配置为 `dry_run = false`；
- 命令行 `--confirm-instance` 与最终解析出的 Lighthouse 实例 ID 完全一致。

任一条件不满足时，CLI 会强制构造 dry-run 适配器，只打印对应的 `StartInstances`、`StopInstances` 或 `RebootInstances` 计划。真实写操作成功提交后，适配器会调用 `wait_instance_status` 轮询 `DescribeInstances`，默认每 5 秒一次、最多 600 秒；如果轮询中发现 `LatestOperationState = "FAILED"`，会立即抛出 `CloudProviderError` 并停止任务。

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

云端 Guest Agent 的 HTTP 客户端占位在 `src/cloud_av_agent_lab/adapters/guest_agent_client.py`。后续实现 Guest Agent 时也应走 `NetworkClient`，这样腾讯云 API、Guest Agent 和临时代理都使用同一网络出口配置。

Guest Agent 的职责应限制在云端隔离 VM 内：

- 从云对象存储拉取样本；
- 在受控目录和超时内执行测试；
- 采集杀软日志、UI 截图、行为观测；
- 上传结构化结果到云端 artifact bucket。

不要把样本下载到本地主机。

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
```

当前重点测试文件：

- `tests/test_config.py`：配置解析与安全配置。
- `tests/test_network.py`：代理开启/关闭时的网络客户端行为。
- `tests/test_tencent_cloud_adapter.py`：腾讯云适配器初始化和响应结构。
- `tests/test_adapter.py`：环境变量注入、实例 ID 覆盖和 real+dry-run 拦截。
- `tests/test_cli.py`：生命周期命令写操作确认门禁。
