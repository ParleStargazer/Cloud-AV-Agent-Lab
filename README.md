# Cloud AV Agent Lab

Cloud AV Agent Lab 是一个本地自动化编排框架，用于把 AI Agent、云端 Windows 虚拟机、杀软告警采集和结构化报告串成闭环。项目边界很明确：本地只保存代码、配置、任务状态和报告，不保存样本，不下载样本，不在本地执行样本。

现有目录里的 `Spore` 可作为透明可控的 AI Agent 外壳，`vmware-mcp` 可作为本地 VMware 适配器参考。本项目新增的根目录框架优先面向云端虚拟机，后续也可以用同样接口接入 VMware。

## 安全边界

- 样本只能以云端对象引用形式出现，例如 `cos://bucket/redacted/case-001.bin`。
- 本地 `.gitignore` 已忽略 `samples/`、`malware/` 和常见可执行样本后缀。
- 框架只提供编排、日志解析、结果判断和报告生成接口，不提供样本、不生成样本、不包含绕过或规避杀软的逻辑。
- 真正的投递、运行、截图、日志采集必须由云端隔离虚拟机适配器完成。
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
- `adapters/`：云厂商和客户机自动化接口，已包含腾讯云 Lighthouse 适配器骨架、Guest Agent 客户端占位和只读计划适配器。
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
```
