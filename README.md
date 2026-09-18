# ZCode 快照上传防护工具集

针对 ZCode 桌面端"整仓工作区快照静默上传"行为的四层防护。
背景与验证过程：登录态下每次发 Prompt 前会触发工作区捕获（含 `.git` 历史，
在约 1GiB 预算内），经 AES-256-CTR + RSA-OAEP 信封加密后直传云端，
UI 开关不拦截该链路。

## 四层防线

| 层 | 机制 | 状态 |
|---|---|---|
| L1 | `patch_asar.py`：对 `app.asar` 中 3 处 `/api/v1/snapshot/upload-credential` 端点做等长字节替换（404），捕获在打包前中止 | 主防线 |
| L2 | `icacls /deny` 锁死 `%USERPROFILE%\.zcode\v2\checkpoints`（拒绝写入/新建/删除） | 路径锁 |
| L3 | `snapshot_guard.py` 常驻进程（pythonw 无窗口，0.5s 增量扫描）：按后缀 + 内容签名（encryptedDataKey/keyWrapAlgorithm）+ 高熵启发式三层检测；确认目标先冻结全部 ZCode 进程再删除；每小时三态校验 asar 补丁与 ACL | 实时兜底 |
| L4 | `watchdog.ps1` 计划任务（15 分钟）：checkpoints 路径下 `.enc`/`.envelope.json` 清理 + 审计日志 | 善后审计 |

## 使用

| 文件 | 用途 |
|---|---|
| `zcode-kill-snapshot-upload.bat` | 一键部署全部四层（自动 UAC 提权） |
| `zcode-restore-original.bat` | 完全回滚（恢复原版 asar、解除全部防线） |
| `zcode-guard-resume.bat` | 解冻被 L3 冻结的 ZCode 进程 |

## 安装（其他机器）

1. 需要：Windows + ZCode 桌面版 + Python 3（官网安装时勾选 "Add to PATH"；`psutil` 会自动安装）。
2. 克隆本仓库，双击 `zcode-kill-snapshot-upload.bat`，授权 UAC。
3. 脚本自动：探测 Python（跳过微软商店占位符）→ 在三个标准位置定位 `app.asar`（找不到可用 `--asar` 指定）→ 安装全部工具到 `%USERPROFILE%\.zcode-tools` → 打补丁 + ACL 锁 + 设置加固 + 注册两个计划任务。
4. 所有路径均为运行时探测（`%USERPROFILE%` / 安装位置），无硬编码机器名。

## 注意

- `tools/` 内为仓库的版本快照；**实际运行的是** `%USERPROFILE%\.zcode-tools\` 下的副本与计划任务，改动请同步两处。
- ZCode 升级会整体替换 `app.asar` 使 L1 失效：L3 每小时校验并弹窗提醒，重跑 `%USERPROFILE%\.zcode-tools\zcode-kill-snapshot-upload.bat` 即可（端点被改名时脚本会明确拒绝并告警，需要重新分析）。
- 代价：失去"检查点/时间线回滚"功能，对话/补全不受影响。
