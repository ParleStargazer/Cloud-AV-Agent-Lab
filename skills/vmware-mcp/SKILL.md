---
name: vmware-mcp
user-invocable: true
description: "VMware MCP 调用 skill，用于管理 VMware Workstation Pro 虚拟机。"

---

# VMware-MCP

用于管理 VMware Workstation Pro 虚拟机的 MCP skill。

## 功能

| 类别       | 功能                         |
| ---------- | ---------------------------- |
| 虚拟机管理 | 列表、获取、创建、删除、更新 |
| 电源管理   | 启动、停止、挂起、暂停、重置 |
| 快照管理   | 列表、创建、删除、恢复       |
| 客户机操作 | 文件、进程、屏幕截图         |
| 硬件配置   | CPU、内存、磁盘、网卡        |

## 环境要求

- VMware Workstation Pro 17+
- Python 3.10+

## 配置参数

所有参数通过环境变量或 MCP 配置传递。

### 必需参数

| 参数          | 说明                |
| ------------- | ------------------- |
| `VM_ID`       | 虚拟机 VMX 文件路径 |
| `VM_USER`     | 虚拟机管理员用户名  |
| `VM_PASSWORD` | 虚拟机管理员密码    |

### 可选参数

| 参数                   | 说明             |
| ---------------------- | ---------------- |
| `VMRUN_PATH`           | vmrun.exe 路径   |
| `VMCLI_PATH`           | vmcli.exe 路径   |
| `HOST_SCREENSHOTS_DIR` | 主机截图保存目录 |
| `GUEST_DESKTOP_PATH`   | 客户机桌面路径   |

## 配置方式

### 方式一：.env 文件（推荐）

在项目根目录 `.env` 文件中添加：

```ini
VM_ID=${PROJECT_ROOT}/your-vm/your-vm.vmx
VM_USER=your-username
VM_PASSWORD=your-password
VMRUN_PATH=/path/to/vmrun.exe
VMCLI_PATH=/path/to/vmcli.exe
HOST_SCREENSHOTS_DIR=${PROJECT_ROOT}/logs/screenshots
GUEST_DESKTOP_PATH=C:\Users\Administrator\Desktop
```

### 方式二：MCP 配置

在 `mcp_config.json` 中配置：

```json
{
  "mcpServers": {
    "vmware-mcp": {
      "command": "python",
      "args": ["-m", "vmware_mcp.server"],
      "cwd": "${workspaceFolder}/vmware-mcp/src",
      "env": {
        "VM_ID": "${VM_ID}",
        "VM_USER": "${VM_USER}",
        "VM_PASSWORD": "${VM_PASSWORD}",
        "VMRUN_PATH": "${VMRUN_PATH}",
        "VMCLI_PATH": "${VMCLI_PATH}",
        "HOST_SCREENSHOTS_DIR": "${HOST_SCREENSHOTS_DIR}",
        "GUEST_DESKTOP_PATH": "${GUEST_DESKTOP_PATH}"
      }
    }
  }
}
```

### 方式三：交互式配置

参数未设置时，系统会引导用户输入。

## 工具列表

### 虚拟机管理

| 工具            | 描述               | 必需参数             |
| --------------- | ------------------ | -------------------- |
| `vmrun_list`    | 列出运行中的虚拟机 | 无                   |
| `vmrun_start`   | 启动虚拟机         | `vm_id`              |
| `vmrun_stop`    | 停止虚拟机         | `vm_id`              |
| `vmrun_reset`   | 重置虚拟机         | `vm_id`              |
| `vmrun_suspend` | 挂起虚拟机         | `vm_id`              |
| `vmrun_pause`   | 暂停虚拟机         | `vm_id`              |
| `vmrun_unpause` | 恢复暂停           | `vm_id`              |
| `vmrun_clone`   | 克隆虚拟机         | `vm_id`, `dest_path` |

### 快照管理

| 工具                    | 描述             | 必需参数        |
| ----------------------- | ---------------- | --------------- |
| `vmrun_snapshot_list`   | 列出快照         | `vm_id`         |
| `vmrun_snapshot_take`   | 创建快照         | `vm_id`, `name` |
| `vmrun_snapshot_delete` | 删除快照         | `vm_id`, `name` |
| `vmrun_snapshot_revert` | 恢复快照（回滚） | `vm_id`, `name` |

**注意**：`vmrun_snapshot_revert` 支持在线回滚（无需先关闭虚拟机）。调用后虚拟机会立即恢复到指定快照的状态。如果回滚后需要继续使用虚拟机，请调用 `vmrun_start` 启动虚拟机。

### 客户机操作

| 工具              | 描述         | 必需参数                           |
| ----------------- | ------------ | ---------------------------------- |
| `vmrun_run`       | 运行程序     | `vm_id`, `program`                 |
| `vmrun_ps`        | 列出进程     | `vm_id`                            |
| `vmrun_ls`        | 列出目录     | `vm_id`, `path`                    |
| `vmrun_mkdir`     | 创建目录     | `vm_id`, `path`                    |
| `vmrun_rm`        | 删除文件     | `vm_id`, `path`                    |
| `vmrun_copy_to`   | 复制到客户机 | `vm_id`, `host_path`, `guest_path` |
| `vmrun_copy_from` | 从客户机复制 | `vm_id`, `guest_path`, `host_path` |
| `mks_screenshot`  | 截屏         | `vm_id`, `output_path`             |

## 使用示例

### 启动虚拟机

```
工具：mcp_vmware-server_vmrun_start
参数:
  - vm_id: ${VM_ID}
  - gui: true
```

### 创建快照

```
工具：mcp_vmware-server_vmrun_snapshot_take
参数:
  - vm_id: ${VM_ID}
  - name: 快照名称
```

### 恢复快照（回滚）

**回滚 = 恢复快照状态**。调用 `vmrun_snapshot_revert` 会将虚拟机立即恢复到指定快照的状态。

**支持在线回滚**（无需先关闭虚拟机）：

```
工具：mcp_vmware-server_vmrun_snapshot_revert
参数:
  - vm_id: ${VM_ID}
  - name: 快照名称
```

**回滚后启动虚拟机**：

```
工具：mcp_vmware-server_vmrun_start
参数:
  - vm_id: ${VM_ID}
  - gui: true
```

**完整回滚流程**（2 步）：

1. 调用 `vmrun_snapshot_revert` 回滚到目标快照
2. 调用 `vmrun_start` 启动虚拟机（如需使用）

### 运行程序

```
工具：mcp_vmware-server_vmrun_run
参数:
  - vm_id: ${VM_ID}
  - program: cmd.exe
  - args: /c 命令
  - user: ${VM_USER}
  - password: ${VM_PASSWORD}
  - no_wait: true
```

### 复制文件

```
工具：mcp_vmware-server_vmrun_copy_to
参数:
  - vm_id: ${VM_ID}
  - host_path: 主机路径
  - guest_path: 客户机路径
  - user: ${VM_USER}
  - password: ${VM_PASSWORD}
```

### 截屏

```
工具：mcp_vmware-server_mks_screenshot
参数:
  - vm_id: ${VM_ID}
  - output_path: ${HOST_SCREENSHOTS_DIR}/screenshot.png
```

## 安装配置

### 1. 安装模块

```bash
pip install -e <vmware-mcp路径> --force-reinstall --no-deps
```

### 2. 验证安装

```bash
pip show vmware-mcp
```

### 3. 验证 vmrun

```bash
vmrun -T ws list
```

### 4. 加载 MCP 配置

在 VS Code 中：

1. 按 `Ctrl+Shift+P`
2. 输入 `Cline: Reload MCP Servers`
3. 等待连接成功

## 注意事项

1. `vmrun_run` 需要 `no_wait: true` 参数
2. `mks_screenshot` 推荐使用，不需要 GUI 窗口
3. 客户机操作需要 VMware Tools 正常运行
4. 快照操作可能需要较长时间
5. Windows 路径使用 `\\` 或 `/`
6. **回滚快照** = 恢复快照状态，使用 `vmrun_snapshot_revert` 工具
7. `vmrun_snapshot_revert` 支持在线回滚（无需先关闭虚拟机）
8. 回滚后虚拟机处于关闭状态，需要调用 `vmrun_start` 启动

## 故障排除

### MCP 服务器未连接

1. 检查 vmware-mcp 包安装：`pip list | findstr vmware`
2. 重新安装：`pip install -e <路径> --force-reinstall --no-deps`
3. 检查 Cline MCP 设置文件中的 `cwd` 路径
4. 重新加载 MCP 配置

### 虚拟机路径无效

1. 确认 VMX 文件路径正确
2. 检查文件是否存在
3. 检查路径格式

### 客户机操作失败

1. 确保 VMware Tools 已安装并运行
2. 确认用户名和密码正确
3. 检查客户机操作系统支持
