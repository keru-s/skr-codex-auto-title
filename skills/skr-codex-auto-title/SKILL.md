---
name: skr-codex-auto-title
description: 全局或按项目安装、更新、检查、启停 Codex 首次成果交付后的轻量会话命名 Hook；也支持全局下单项目开关。
license: MIT
metadata:
  version: "1.0.0"
---

# SKR Codex 会话命名

安装器、运行脚本和引用均随本目录分发，技能可单独复制。以实际交付生成一次中文标题：`模块 | 动作＋对象`。

## 选择操作

- 安装或更新：阅读 [安装说明](references/install.md)，执行下列流程。
- 检查、启停、单项目开关、卸载或排障：阅读 [管理说明](references/manage.md)，执行用户指定的范围和操作。
- 用户要求真实验证：阅读 [验证说明](references/verify.md)。配置通过、模型改名、桌面自动触发是不同的验证结果。

## 安装或更新

1. **确定范围与环境。** 用户指定全局时使用 `--scope global`，不传 --root；指定项目或未指定范围时使用 `--scope project --root <实际项目根>`。读取相关 AGENTS.md 与现有配置，确认 CLI、CODEX_HOME 和命名模型。使用 UV 和 Python 3.11+ 执行本目录 scripts/install.py；独立目录可用 `uv run --no-project --python '>=3.11' python <installer>`。运行对应范围的 doctor。完成标准：实际协议和所选模型检查通过，不静默更换模型。
2. **选择模块。** 全局安装使用安装器通用模块，不套用当前项目术语。项目安装从实际项目说明归纳稳定模块，将简短术语与来源写入 UTF-8 文件，通过 --modules-file 传入。最多发送 400 字符，优先简短准确；更新保留原模块。完成标准：全局与项目分支使用正确来源。
3. **安装。** 全局使用 install --scope global；项目使用 install --scope project --root <project>。传 --trust-hooks 及安装对话的真实 --exclude-session-id；纯终端安装可省略排除 ID。已有受管理安装用 upgrade，不卸载重装。完成标准：两项命令 enabled/trusted、旧 Hook 和设置保留；出错核对备份，不清空用户配置。
4. **交付。** 执行相同范围 verify，报告安装范围、位置、模型、验证范围、备份与停用命令。全局安装另外提供 project-disable/project-enable/project-status --scope global --root <project>。完成标准：管理命令与安装范围一致；用户知道需在安装后新建会话。

每次失败不在当轮连续重试，等待下一轮答复结束；已有标题直接重试改名。两种范围共享已接手资格与成功记录，项目规则关闭约束两种范围。简短片段不能保证语义判断绝对准确，主对话正常交付业务成果即可。
