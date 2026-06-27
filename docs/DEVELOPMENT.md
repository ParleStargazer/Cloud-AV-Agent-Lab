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

### 源码更新与 Editable Install

本项目在本地和云端调试时常用 editable install。editable install 必须指向当前源码 checkout，而不是固定某个 git commit。更新云端或平台机源码后，请在仓库根目录重新执行：

```powershell
git pull
python -m pip install -e ".[guest-agent,desktop-worker]"
```

如果要安装打包依赖，再显式加 build extras：

```powershell
python -m pip install -e ".[guest-agent,guest-agent-build,desktop-worker,desktop-worker-build]"
```

如果使用 `requirements.txt`，也要从仓库根目录执行，使其中的 `-e .[...]` 指向当前 checkout：

```powershell
python -m pip install -r requirements.txt
```

不要使用类似下面的固定提交安装：

```text
-e git+https://github.com/.../Cloud-AV-Agent-Lab.git@<commit>#egg=cloud_av_agent_lab
```

这种写法会让云端进程继续运行旧代码，即使工作区已经 `git pull` 到新版本，也可能出现本地源码看起来正确、但 Guest Agent / Desktop Worker / single-run artifact 仍是旧行为的情况。排查时先确认 Python 实际加载的模块路径：

```powershell
python -c "import cloud_av_agent_lab; print(cloud_av_agent_lab.__file__)"
python -c "import cloud_av_agent_lab.orchestration.single_run as s; print(s.__file__)"
```

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

腾讯云适配器对外入口仍是 `src/cloud_av_agent_lab/adapters/tencent_cloud.py`，但该文件现在只作为 facade/re-export，保证旧 import 不变：

```python
from cloud_av_agent_lab.adapters.tencent_cloud import TencentCloudLighthouseAdapter
from cloud_av_agent_lab.adapters.tencent_cloud import parse_lighthouse_instance_status
from cloud_av_agent_lab.adapters.tencent_cloud import build_tc3_headers
```

具体实现已拆到 `src/cloud_av_agent_lab/adapters/tencent_lighthouse/`：

- `errors.py`：腾讯云配置和 API 错误；
- `models.py`：鉴权、操作描述和 `LighthouseInstanceStatus`；
- `auth.py`：环境变量优先的鉴权加载；
- `signing.py`：TC3-HMAC-SHA256 签名；
- `parsing.py`：`DescribeInstances` 和 JSON 响应解析；
- `adapter.py`：`TencentCloudLighthouseAdapter` 主逻辑、轮询和写操作保护。

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

真实 API 调用集中在 `TencentCloudLighthouseAdapter._call_api()`，实现位于 `tencent_lighthouse/adapter.py`。当前已经完成：

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
python -m cloud_av_agent_lab guest-check-security-product-readiness --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --product huorong
python -m cloud_av_agent_lab guest-collect-logs --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --product huorong
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

2026-05-21 阶段新增了安全产品就绪检查 MVP。该能力位于 `src/cloud_av_agent_lab/guest_agent_server/security_product_readiness/`，与 collection collector 并列，不复用 collector verdict。Huorong probe 只做测试前只读检查：确认 `C:\ProgramData\Huorong\sysdiag` 和 `log.db` 存在，把 live `log.db` best-effort 复制到当前 case 的 `security-product-readiness\huorong\` 快照目录，再对复制后的文件做 metadata 检查。Windows Defender probe 通过 Windows Event Log reader 抽象检查 Defender Operational channel 日志可观察性；本地单测使用 fake reader，不读取真实 Event Log。Qihoo 360 probe 只检查 `360safe.Summary.dat` SQLite 日志可观察性，不判断实时防护开关。Tencent PC Manager probe 使用 `quarantine_metadata_observability` scope，只检查 QQPCMgr/TAV 隔离区 metadata 路径和 `TAVCacheFullEx.db` baseline。结果写入 `case_security_product_readiness.json`，同步到 `case_state.security_product_readiness`、`case_report.security_product_readiness` 和 `events.jsonl`。`ready` 只代表观察链路具备最低条件，`protection_state=unknown`，不能当作实时防护已开启或一定会检出的证明。当前 CLI 是 `guest-check-security-product-readiness --product huorong|windows-defender|qihoo-360|tencent-pc-manager`；evidence bundle 只纳入脱敏后的 `case_security_product_readiness.json`，并继续排除 `security-product-readiness/` 下的 raw readiness snapshot。single-run 现在会在 `prepare-case` 后、`upload-sample` 前 warning-only 调用 readiness：`ready` 记为 ok，其余状态或 API failure 记为 warning 并继续流程。evaluator 只把 readiness 用作 `no_detection_observed` 的保守闸门：只有 `ready` 放行，其他状态或缺失 readiness 会把未检出结论降为 `inconclusive`；明确产品日志检出不受影响。strict mode 仍未接入。

2026-05-16 阶段完成了日志收集、简易评测和证据导出的 MVP：杀毒软件日志收集框架负责 product collector 插件与统一证据 schema；火绒 collector 作为首个实现负责读取和归一化火绒拦截日志；Guest Agent 提供 collection、summary 和 evidence-bundle 接口；本地 CLI 对应提供 `guest-collect-logs`、`guest-case-summary` 和 `guest-export-evidence`。

收集阶段已新增 `docs/COLLECTION_MODEL.md` 和 Guest Agent collector 插件模型。统一 schema 位于 `src/cloud_av_agent_lab/guest_agent_server/collectors/base.py`，collector 注册入口位于 `collectors/registry.py`，新增产品接入清单位于 `docs/PRODUCT_ONBOARDING.md`。当前产品实现包括 `collectors/huorong/`、`collectors/windows_defender/`、`collectors/qihoo_360/` 和 `collectors/tencent_pc_manager/`。`guest-collect-logs --product huorong|windows-defender|qihoo-360|tencent-pc-manager` 会调用云端对应 collection endpoint。Huorong collector 在 Guest Agent 内复制火绒 `C:\ProgramData\Huorong\sysdiag\log.db*` 到当前 case 的 `collection/huorong/` artifact 目录，再只读打开 SQLite 并自动发现最新的 `HrLogV3_*` 表。JSON payload 列会优先按 `detail`、`raw_json`、`payload`、`data`、`event_json` 等名称识别；真实火绒表中通常是 DB 列 `detail`，且该列 JSON 里还有嵌套 `detail` 对象。Windows Defender collector 通过 reader 抽象读取 Defender Operational channel；pywin32 缺失时返回结构化 `failed/unknown`，不抛 500。Qihoo 360 collector 复制 `360safe.Summary.dat` 快照后只读解析 `FI/FQ`，按 case path、baseline delta、时间窗口、hash 和威胁字段保守归因；FILETIME-like 时间字段会按 UTC+8 本地墙钟时间归一化为 UTC，并保留 warning。Tencent PC Manager collector 只观察 QQPCMgr/TAV 隔离 metadata：`Quarantine\<md5>` 容器、`<md5>.ico` sidecar 和 `TAVCacheFullEx.db` baseline/current stat；它不解密、不复制 raw TAV 容器进 evidence bundle，也不会把单独 TAV cache 活动直接当成检出。2026-05-29 的云端隔离 `single-run` smoke 已确认管理员账号 Desktop Worker 能触发当前 case 样本、360 写入 `Summary.dat` 隔离记录、collector 读取到 `av_quarantined` normalized evidence，summary 生成 `detected_or_blocked / high` 结论。2026-05-30 的 Tencent PC Manager 云端隔离 `single-run` smoke 已确认 `tencent-pc-manager` readiness 为 `ready`、collection 读取到 TAV 隔离容器 metadata、strong attribution 包含 `md5_quarantine_filename`、`time_window` 和 `size_delta`，summary 生成 `detected_or_blocked / high` 结论，cleanup restore 正常完成。raw SQLite、union metadata、q3q、TAV raw artifact 和样本本体默认都不进入 redacted evidence bundle。输出写入 `case_collection.json`，包含 normalized product events、统一时间线、时间窗口、collection_state、保守 verdict 和 collector artifact policy。只有产品日志或产品 metadata 中的 hash/path/pid/time-window 证据才会产生 `intercepted`；单独的 `removed_after_save`、TAV cache 活动或进程消失不会被直接判定为拦截。SQLite 读取失败会返回安全诊断信息，例如可用表名，不返回样本内容或 token。Raw product log 文件会被声明为 `raw_product_log` / `raw_blocked`，默认不进入 redacted evidence bundle。

Evaluation / Evidence Export MVP 新增两条 Guest Agent CLI：`guest-case-summary` 和 `guest-export-evidence`。`guest-case-summary` 调用 `GET /cases/{case_id}/summary`，由 `cloud_av_agent_lab.evaluation` 汇总投送、执行、readiness 和 collection 证据，生成 `case_summary.json` / `case_summary.md`。CLI 默认输出简洁结论，显式加 `--json` 时才打印完整结构；summary timeline 会折叠重复轮询事件，只保留关键状态变化、collection 边界和产品日志证据，完整审计仍在 `events.jsonl`。结论采用保守 verdict：产品日志证据优先给出 `detected_or_blocked`；只有文件消失但缺少日志证据时给出 `suspiciously_removed`；执行未发生或不可观察时不会武断判定未检出；只有 `security_product_readiness.state == ready` 时才允许 `no_detection_observed`。`guest-export-evidence` 调用 `GET /cases/{case_id}/evidence-bundle`，保存 redacted guest-reported `case_evidence_<case_id>.zip`。证据包包含 manifest、脱敏后的 case metadata、`case_security_product_readiness.json`、summary、events、`sample/sample.json`、Worker state 和 normalized evidence，不包含上传样本本体、token、环境变量、云密钥、真实云配置文件、`security-product-readiness/` readiness snapshot 或 raw copied log DB。manifest 会记录 `trust_model=dirty_instance_untrusted`、`forensic_grade=false`、`raw_binary_included=false`、redaction policy、redacted files、excluded raw artifacts 和 bundle 内每个文件的 SHA-256，便于后续归档核验。

2026-05-13 实测结论：Microsoft Defender 在云端环境下对 EICAR 的处理时间存在波动，单次状态查询容易得到“处决前”的假 `stable`。因此不要把耗时观测放回 `POST /sample`，也不要把单次 `guest-case-status` 当作最终判定。当前推荐策略是 `guest-upload-sample` 自动执行动态轮询：先等待 10 秒，再每 2 秒查询一次，最多 30 秒；期间一旦出现 `removed_after_save` 即判定拦截成功，只有完整窗口结束后仍为 `stable` 才判定样本存活。

受控触发阶段目前只实现默认关闭的 action 骨架：

```toml
[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

`POST /cases/{case_id}/actions` 只接受白名单 action，不接受任意路径、命令、shell/cmd/PowerShell 或参数。`guest-execute-sample` 默认发送 `dry_run_execute_uploaded_sample`，只校验当前 case 已登记上传样本的 metadata、路径归属和可选 sha256，不启动进程；显式传入 `--real-action` 时请求 `execute_uploaded_sample`。真实启动只有在云端 Guest Agent 启用 execution、请求携带正确 `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN`、Desktop Worker ready，且 Worker 校验短期单次 execution lease 后才会发生。Control Agent 不再直接 `Popen`；它签发绑定 `case_id` / `sample_id` / `run_id` / `expected_sha256` 的 lease 并转发给 Worker。Worker 只执行当前 case 已登记样本，并根据 `sample.json` 的 `stored_filename` 解析受控 handler：`.exe` 为 `pe_executable`，`.bat`/`.cmd` 为 `batch_script`，`.ps1` 被识别为 `powershell_script` 但默认禁用，未知后缀为 `unsupported_file_type`。`.exe` 使用 `subprocess.Popen([sample_path], cwd=sample_dir, shell=False)`；`.bat`/`.cmd` 使用固定 `C:\Windows\System32\cmd.exe /d /c call <sample_path>` 模板且仍为 `shell=False`。客户端不能传入 path、cmd、args、interpreter 或 shell。root PID、启动时间、handler、Worker session 和路径归属校验结果会写入状态文件，并记录 `execution_started` 事件。

`guest-execute-sample --real-action` 启动成功后会改为执行状态轮询，不再检查 proof 文件。CLI 默认每 2 秒查询一次 `GET /cases/{case_id}/execution-status`，最多 60 秒；每次输出 root PID、状态和子进程数量。Control Agent 会把状态查询转发给 Desktop Worker；Worker 只做低侵入式只读元信息快照：只观测当前 case 记录的 `root_pid` 及其子进程，不接受任意 PID，不缓存 `psutil.Process` 对象，不读取样本内容或杀软日志，也不能阻碍 Defender 或其他安全软件终止进程。`terminated_or_disappeared` 只表示进程已退出或不可观察，不能单独当作杀软拦截结论。完整设计见 `docs/EXECUTION_MODEL.md`。

Guest Agent CLI 报错按来源归因：`[Local Check]` 表示本地配置或环境变量问题，`[Network]` 表示无法连接云端 Guest Agent，`[Remote Agent]` 表示云端鉴权或业务拒绝，例如 execution 未启用。

开发联调可以继续使用 EICAR 或无害命令 exe 验证触发链路，例如只在云端 case 的 sample 目录内写入 `execution_proof.txt` 的测试程序。该 proof 文件只适合早期联调，不再作为长期评测模型；后续评测应结合投送状态、执行观测和只读安全产品日志证据。进入真实样本验证时必须限定在云端隔离环境中，本地仍不能执行、打开、解压、扫描或解析任何样本文件。

Guest Agent 的职责应限制在云端隔离 VM 内：

- 从云对象存储拉取样本；
- 在受控目录和超时内执行测试；
- 采集杀软日志、UI 截图、行为观测；
- 上传结构化结果到云端 artifact bucket。

不要把样本下载到本地主机。

协议和单实例串行锁设计见 `docs/GUEST_AGENT.md`。Windows 免 Python 打包和 Lighthouse 手动部署流程见 `docs/GUEST_AGENT_DEPLOYMENT.md`。

## Single-Run Orchestration

`single-run` 是第一版面向普通用户的单轮编排入口，核心实现位于 `src/cloud_av_agent_lab/orchestration/`：

- `single_run.py`：串联 cloud lifecycle、Guest Agent ready/cooldown、prepare、upload、execute/dry-run、collection、summary、evidence、cleanup；
- `run_state.py`：每一步即时写入 `run_state.json`，便于崩溃后判断进度；
- `locks.py`：按 `instance_id` 写 `runs/.locks/<instance_id>.lock`，支持过期锁、心跳陈旧锁和 `--force-unlock`；
- `logging_context.py`：使用 `contextvars + logging Filter` 给 `cloud_av_agent_lab` 日志自动加 `[instance_id][run_id]`；
- `timeout.py`：定义 health、普通请求、evidence 下载和 fast-fail salvage 的 timeout profile；
- `prompts.py`：CLI 缺少参数时的交互式输入。

默认运行：

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

single-run 会自动计算样本 SHA-256、生成 `case_id` 和 `run_id`，并在 `runs/<run_id>/lab.generated.toml` 写入非敏感临时配置。腾讯云密钥、Guest Agent token、upload token 和 execution token 仍然只从环境变量读取，不写进生成配置、日志或证据包。生成配置中的 sample 仍是云对象占位 URI，真实本地文件路径只作为用户显式上传输入保存在 `run_state.json`。

single-run 面向普通用户，默认就是完整真实流程：生成配置为 `mode = "real"`、`dry_run = false`，并把用户输入的 `--instance-id` 和 `--snapshot-id` 作为内部适配器确认值，不再要求重复输入 `--confirm-instance` / `--confirm-snapshot`。启动前 CLI 会打印 instance、snapshot、region、Guest Agent URL 和 Desktop Worker gate 状态，并要求用户确认“此操作会进行实例真实操作，请务必检查实例id和快照id是否正确，并了解此操作的风险，是否确认？”。只有用户确认后才继续。需要演练时显式传 `--dry-run`，此时生成配置为 `mock + dry_run`，云写操作、Desktop Worker gate 和受控 action 都不会真实执行。

为解决 Windows Session 0 与交互式桌面 session 隔离问题，真实 single-run 现在会默认生成 `[guest_agent.desktop_worker] enabled = true`，并在 Guest Agent health 之后等待 Control Agent `/worker/status` 确认 Desktop Worker ready。真实执行已下放到 Worker：Control Agent 只负责签发 execution lease、转发请求和同步状态；Worker 负责在交互式桌面 session 中启动当前 case 已登记样本并就近观测。第一版已支持 `.exe` 与受控 `.bat/.cmd`；PowerShell 脚本只识别不启用。若需要在旧环境诊断，可以加 `--disable-desktop-worker-gate` 临时跳过门禁，但这样不能作为可靠评测环境。Desktop Worker 的部署与安全边界见 `docs/DESKTOP_WORKER.md`。

2026-06-27 新增了可选的产品 warm-up 动作，用于 360 批量测试前的环境初始化辅助。该动作由 Desktop Worker 在交互式桌面 session 中执行，不复用样本执行接口；当前只允许 `product_id=qihoo-360`，Worker 内部固定打开 `C:\Program Files (x86)\360\360Safe\360Safe.exe`，客户端不能传 path、cmd、args、shell、interpreter 或 PowerShell 参数。`single-run` / `multi-run` 可通过 `--enable-product-warmup` 开启，交互式引导仅在产品支持 warm-up 时提示，并可设置 `--product-warmup-cooldown-seconds`。warm-up 发生在 Desktop Worker ready 后、`prepare-case` 前；它只是让产品主界面和相关组件有机会初始化，不代表实时防护已开启，也不能作为检出或未检出的证据。

上传后执行不再无条件触发。single-run 会读取上传状态轮询的最终结果：`stable` 才解析 execution handler 并调用 `execute_uploaded_sample` 或 dry-run action；`removed_after_save` 表示文件已被安全产品处理，`locked_or_busy` 表示文件可能正在被安全产品占用，二者都会记录 `execution_action_status = "skipped"` 并继续进入日志收集、summary 和 evidence 导出。若 handler 默认禁用或后缀未知，也会在本地编排层记录 `execution_handler_disabled` / `unsupported_file_type`，不把请求发到 Worker。即使执行接口返回“样本已不存在”或“启动失败”这类业务失败，也会记录为 `not_started` / `launch_failed` 等观察结果，并继续收集证据；只有网络、鉴权、本地配置和云生命周期等基础设施错误才会让 single-run 进入失败分支。真实执行被观测到退出或不可观察终态后，single-run 默认再等待 45 秒才进入 collection，用于给安全产品弹窗默认处理、隔离动作和日志落库留出时间；这个等待发生在执行退出之后，不是在执行启动瞬间。

推荐单轮流程已经串联：运行锁、生成配置、实例状态查询、快照回滚/启动、Guest Agent 连续 health OK、Desktop Worker ready gate、settling cooldown、prepare-case、security product readiness warning-only check、upload-sample 与动态状态轮询、受控 action、execution-status 轮询、执行退出后的 collection 延迟、按 `product_id` 路由的 collection、case summary、evidence bundle、结尾回滚和 emergency stop 兜底。证据导出在清理前执行；异常分支会短超时尝试 evidence salvage，失败只记录，不阻塞清理。

`run_state.json` 现在同时保留旧的扁平字段和新的结构化 `stages`：
`environment`、`delivery`、`security_product_readiness`、`execution`、
`collection`、`summary`、`evidence` 和 `cleanup`。readiness stage 会记录
`status`、`product_id`、`state`、`confidence`、`scope`、
`protection_state`、`checked_at_utc`、`warnings` 和 `errors`。可预期的测试对象状态，例如 `removed_after_save`、
`locked_or_busy`、`worker_busy`、`sample_missing_before_execution`、
`sha256_mismatch` 或 collector 远端失败，会写入 `warnings` 与对应 stage，
并继续 summary/evidence；网络不可达、鉴权、本地配置和云生命周期错误仍会
进入失败路径。`status` 表示流程完成情况，`test_verdict` 表示评测结论，
后续 multi-run 可以直接聚合这两个字段。

证据包已升级为 `evidence-bundle.v2` + Redaction MVP。Exporter 只打包明确允许的 case
workspace 根，并且只把 JSON / JSONL / Markdown / TXT 这类 text-format artifact 的脱敏副本写入 zip：case 元数据、`case_security_product_readiness.json`、`sample/sample.json`、`worker-state/` 里的文本状态、summary、events
以及派生的 `collector/normalized_evidence.json`。上传样本本体、旧 evidence
zip、真实云配置、token/credential/key 命名文件、symlink/junction、未知根、`security-product-readiness/` readiness snapshot、raw SQLite/WAL/SHM、可执行文件和其他未脱敏二进制产品日志
都会被排除并写入 manifest。安全产品原始日志副本必须由对应 collector 先声明 artifact policy，single-run 和 exporter 都不写死产品安装目录；默认 redacted bundle 不搬运 raw binary。

新增安全原则：样本投送或触发后，测试实例内的静态文件、日志、工具、解释器、Guest Agent、Desktop Worker 和临时目录都视为不可信。Redaction MVP 只提供 `guest-reported` 的脱敏观察结果，适合开发、EICAR/无害样本验证和课程交付，不宣称 forensic-grade。若后续需要 raw 证据，应停止实例后通过云快照/克隆盘进入干净取证环境只读挂载，再用可信 collector/redactor 提取和脱敏，不应让脏实例自我打包 raw 日志后回传本地。

Manifest 中的 `redaction_policy` 是结构化自描述对象：当前默认开启文本脱敏、关闭二进制脱敏、保留 hash 关联字段，并记录 bundle 文件数、单文件大小、总未压缩大小和文本脱敏大小上限。产品语义级脱敏由 collector 声明，exporter 只做全局兜底脱敏和打包安全裁决；这些值先保持代码常量，不开放绕过 redaction 或包含 raw binary 的配置开关。

## Multi-Run 批量编排

`multi-run` 第一版 MVP 已完成。它不重写执行流程，而是把现有
`single-run` 作为单 case 原语进行调度：

- 交互式引导或参数化收集 product、instance、snapshot、region、Guest Agent、
  Desktop Worker 和样本输入；
- 云端平台机可通过 `--platform-sample-dir` 扫描受控样本目录，生成
  `sample_manifest.jsonl` 和 batch-local indexed mirror；
- 开发机继续使用预生成 `--manifest`，不能扫描真实样本目录；
- 生成 `batch_plan.json`、`multi_run.generated.toml`、`preflight_report.json`、
  `multi_run_state.json`、`multi_run_events.jsonl`、`aggregate_summary.json` 和
  `aggregate_summary.md`；
- 按同一 Lighthouse `instance_id` 串行调度，未来只允许不同 instance id
  之间并行；
- 聚合层只读取每个 run 的 `run_state.json`、`case_summary.json` 和 evidence
  metadata，不读取样本本体、raw product logs、token、云密钥或
  `configs/real.toml`。

`multi-run` MVP 与三轮性能优化已经完成云端验证。当前实现支持 timing
aggregation、indexed 样本“测后即焚”、fastmode / deferred cleanup、upload
即时状态轮询、产品侧 observation probe、execution-stage probe、adaptive
post-execution delay、runtime 参数记录和 Windows lock 写入鲁棒性收口。最新
封口批次 `runs/batch_20260601-162301_tencent-pc-manager` 验证 94 个样本全部
完成，summary / evidence 全部导出，indexed mirror 全部 burned，最终
`completed_with_warnings` 且 `batch_cleanup_status = restored`。详细设计与历史
记录见 `docs/MULTI_RUN_PLAN.md` 和 `reference-doc/current/multi-run/archive/`。

## 结构拆分记录

2026-05-15 已完成两个 facade 包拆分，用于降低大文件维护成本，同时保持外部 import 兼容：

- Guest Agent Server：`guest_agent_server/workspace.py` 拆为 `guest_agent_server/workspace/` 包，`__init__.py` 继续 re-export `prepare_case_workspace`、`save_uploaded_sample`、`read_case_status`、`run_case_action`、`ExecutionRegistry` 等稳定 API。
- 腾讯云 Lighthouse：`adapters/tencent_cloud.py` 改为 facade，内部实现拆到 `adapters/tencent_lighthouse/`，分别承载 errors、models、auth、signing、parsing 和 adapter。

下一步计划拆分 `cli.py`，但仍保持 `cloud_av_agent_lab.cli:main` 入口不变。建议分阶段推进：

- 先拆 parser 构建、输出格式化和错误归因 helper，避免改变命令行为；
- 再拆 Guest Agent 命令处理，如 health、prepare、upload、status、report、execute；
- 最后拆 cloud lifecycle 命令处理，如 status、start、stop、reboot、restore snapshot；
- 每个阶段至少运行 `tests.test_cli`，涉及云适配器或 Guest Agent 客户端时同步运行 `tests.test_adapter`、`tests.test_tencent_cloud_adapter`、`tests.test_guest_agent_client`。

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

### Codex Windows 沙箱 tempfile 兼容说明

在 Codex Windows 沙箱内运行全量单元测试时，Python 3.11 的
`tempfile.mkdtemp()` 会通过 `os.mkdir(path, 0o700)` 创建临时目录。当前
Codex 受限 token 与 Windows ACL 组合下，这类目录可能创建成功但随后不可写，
导致 `tempfile.TemporaryDirectory()` 相关测试大量报 `PermissionError`。

当前开发机的 conda 环境中保留了一个本地兼容文件：

```text
C:\Users\Parle\.conda\envs\cloud-av-agent-lab\Lib\site-packages\sitecustomize.py
```

该文件只在检测到 Codex 沙箱环境变量时生效，用于让临时目录继承父目录 ACL；
普通 PowerShell / conda 运行不依赖它。它不是项目源码，也不是交付物，不应提交到
Git。仓库 `.gitignore` 已排除误生成在仓库根目录的 `sitecustomize.py`。如果需要临时
禁用该兼容逻辑，可设置：

```powershell
$env:CLOUD_AV_DISABLE_CODEX_TEMPFILE_PATCH = "1"
```

当前重点测试文件：

- `tests/test_config.py`：配置解析与安全配置。
- `tests/test_network.py`：代理开启/关闭时的网络客户端行为。
- `tests/test_tencent_cloud_adapter.py`：腾讯云适配器初始化和响应结构。
- `tests/test_adapter.py`：环境变量注入、实例 ID 覆盖、real+dry-run 拦截、状态轮询、快照回滚前置校验与回滚后启动闭环。
- `tests/test_cli.py`：生命周期命令写操作确认门禁、快照确认门禁。
