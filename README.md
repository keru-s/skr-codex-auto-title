# SKR Codex Auto Title

为 Codex 新会话自动起一个清楚的中文标题。首次实质成果交付后命名一次，例如 `库存管理 | 分析预占释放规则`，后续任务不反复改名。

支持**全局安装**和**项目安装**。全局安装后可以单独关闭、开启某个项目。安装器保留已有 Hook 和设置；这是第三方项目，不是 OpenAI 官方插件。

## 使用前准备

- macOS 或 Linux，暂不支持 Windows。
- 已安装 UV；项目需要 Python 3.11+，`uv run` 会选择符合要求的 Python。没有额外 Python 库。
- 已安装并登录 Codex CLI。安装器会实际检查当前 CLI、账号可用模型和所需接口。
- 默认命名模型为 `gpt-6-luna / low`，调用会使用账号额度；不可用时会停止，不会自动切到其他模型。

下载项目：

```sh
git clone https://github.com/keru-s/skr-codex-auto-title.git
cd skr-codex-auto-title
```

## 方法一：通过 Skill 安装

把 `skills/skr-codex-auto-title/` **整个目录**复制到个人的 `~/.agents/skills/`，或目标项目的 `.agents/skills/`；也可以把技能的完整路径交给 Codex。该目录包含安装器、运行脚本和说明，可单独复制，不依赖仓库其他文件。

**全局安装**：在 Codex 中发送：

> 使用 $skr-codex-auto-title 全局安装会话自动命名，保留已有设置，并告诉我怎样单独关闭某个项目。

**项目安装**：在目标项目中发送：

> 使用 $skr-codex-auto-title 以项目级方式安装会话自动命名，按当前项目内容定制模块，并保留已有设置。

技能会根据指定范围调用安装器。把技能放进个人目录只提供入口，实际生效范围由安装时选择的全局或项目范围决定。安装完成后可移除技能，已经安装的功能仍独立运行。

## 方法二：在终端直接安装

以下命令在本仓库目录执行。`--trust-hooks` 表示已经审阅并授权安装本工具的两项命令。项目安装还需要该项目在 Codex 中受信任。

### 全局安装：装一次，各项目生效

```sh
uv run python install.py doctor --scope global
uv run python install.py install --scope global --trust-hooks
uv run python install.py verify --scope global
```

注册写入所选 `CODEX_HOME/hooks.json`；脚本、设置和私人记录保存在 `CODEX_HOME/skr-codex-auto-title/`。未指定 CODEX_HOME 时使用个人 Codex 目录，不向各个业务项目复制 Hook 文件。

单独管理一个项目，路径替换成自己的：

```sh
uv run python install.py project-disable --scope global --root /absolute/path/to/project
uv run python install.py project-enable --scope global --root /absolute/path/to/project
uv run python install.py project-status --scope global --root /absolute/path/to/project
```

子目录继承最近的规则，可为子项目另外开启。开关保存在个人目录；全局卸载后仍可用这些命令解除之前的项目关闭规则。`project-status` 的 enabled 只表示项目规则是否允许，不代表安装的总开关已经打开。

### 项目安装：只在指定项目生效

```sh
uv run python install.py doctor --scope project --root /absolute/path/to/project
uv run python install.py install --scope project --root /absolute/path/to/project --trust-hooks
uv run python install.py verify --scope project --root /absolute/path/to/project
```

省略 `--scope` 时默认为 project。项目注册和脚本放在 `.codex/`，私人记录放在项目的被忽略目录 `runtime/` 中。

### 指定 Codex、模型或项目术语

找不到 Codex 时，在 doctor、install、verify 命令后加 `--codex /absolute/path/to/codex`。使用非默认账号目录时，每次加相同的 `--codex-home /absolute/path/to/codex-home`，需与实际 Codex 使用的目录一致。

模型可用性由账号实际目录检查；需要指定其他模型时，在 doctor 和 install 后都加 `--model <available-model> --effort <supported-effort>`。

项目安装可加 `--modules-file examples/modules.txt`，先把示例文件改成自己的简短项目术语。模块参考最多发送 400 字符。

全局安装也可为某个项目单独配置术语，无需向项目复制 Hook：

```sh
uv run python install.py project-modules --scope global --root /absolute/path/to/project --modules-file examples/modules.txt
uv run python install.py project-status --scope global --root /absolute/path/to/project
uv run python install.py project-modules-clear --scope global --root /absolute/path/to/project
```

子目录使用最近的项目术语，其他项目不受影响。未配置时，根据交付内容概括模块；这个通用回退没有读取项目业务说明。全局 install 不接收 --modules-file，避免把一个项目的词汇用于所有项目。

如果从 Codex 对话内安装，技能会传入当前安装对话的真实 `--exclude-session-id`。纯终端安装可省略。

## 安装后怎样生效

安装完成后**新建会话**，正常提出任务即可。首次实际成果交付后，在后台判断并生成标题，再改名和读回确认。

- 确认收到、追问、未交付计划、失败、中断、待审批不会视为完成；交付用户要求的分析、方案或文档也算成果。
- 首次成功命名后不再随后续任务改名。首次成功前的人工标题可能被覆盖。
- 已被本工具接手的会话，切换全局/项目范围后仍能继续处理。此前从未接手的旧会话不补命名。
- 新回复到来时，未成功评估的旧答复让位给最新答复；已经生成的待重试标题保持不变。
- 两种安装并存时由最近的项目安装负责，全局跳过；项目安装停用后全局不会接管，切回全局需卸载项目安装。
- 全局单项目关闭规则约束两种安装；各自的总开关只控制自己的安装范围。

## 轻量命名与失败重试

不发送完整历史、工具输出或思考过程。选中的正文上限为：

| 内容 | 最多字符 |
| --- | ---: |
| 首条用户需求 | 800 |
| 最新一条不重复的补充 | 400 |
| 本轮答复开头和结尾 | 1600 |
| 从答复提取的未完成/待确认等提醒片段 | 300 |
| 项目模块参考 | 400 |

以上合计最多 **3500 字符**，外加少量字段和命名规则；字符数不等于 token 数。短内容保持原文，长内容保留开头、结尾并标明省略。提醒片段供模型理解上下文，不直接当作失败结论。片段仍可能遗漏信息或导致误判。

遇到超时或临时服务故障，会分别等待 **2 秒、5 秒**再试，最多尝试三次。结构无效的输入或结果直接停止。三次仍失败时保留任务，**同一会话下一轮答复结束时继续**；没有定时后台服务：

- 还没生成标题：下一轮用最新的精简内容重新判断。
- 标题已生成、改名失败：直接沿用原标题继续改名，不重新调用命名模型；新一轮内容过长或无法提取请求也不阻断它。
- 单次模型结果最多等待 60 秒，单次连接最多 120 秒，整轮后台工作最多 400 秒，Hook 外层上限 420 秒。这些是故障时的上限，正常完成后立即结束，不会等待到上限；主对话无需等待命名。
- 关闭会话可能取消尚未完成的后台工作。主对话不会收到命名指令或被自动续跑。

## 停用、开启、更新和卸载

全局范围：

```sh
uv run python install.py disable --scope global
uv run python install.py enable --scope global
uv run python install.py upgrade --scope global --trust-hooks
uv run python install.py uninstall --scope global
```

项目范围：

```sh
uv run python install.py disable --scope project --root /absolute/path/to/project
uv run python install.py enable --scope project --root /absolute/path/to/project
uv run python install.py upgrade --scope project --root /absolute/path/to/project --trust-hooks
uv run python install.py uninstall --scope project --root /absolute/path/to/project
```

以后下载新版，对同一范围运行 upgrade；保留模型、模块、启用时间、项目开关和命名记录。已发布的 1.0.0 安装会把旧队列转入共享目录，保留成功记录与待处理标题；未知安装格式会停止并保留原文件。更新失败会尝试恢复并保留备份。临时停用用 disable，不通过卸载重装更新。卸载保留私人历史，阻止无意重装。

下载目录可改名或删除；移动业务项目、CODEX_HOME 或 Python/CLI 后需重新核对配置。分享时复制本仓库或技能目录，不复制已安装文件和私人记录。

## 隐私与排查

精选的请求和答复会发给所选 Codex 模型，并保存在本地私人记录中。全局记录在 `CODEX_HOME/skr-codex-auto-title/runtime/`，项目记录在项目 `runtime/`；共享开关、项目术语、任务队列、处理资格、待处理标题和成功记录在个人目录的 coordination 下。不要公开提交这些目录、会话文件或安装备份。

无法命名时先执行对应范围的 verify，再检查本地 logs。下一轮重试只对仍启用、未被项目规则关闭、未被排除的主会话生效。详见 [管理与恢复](skills/skr-codex-auto-title/references/manage.md)。

## 开发和验证

```sh
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run python tests/check_distribution.py
uv run python tests/test_real_install.py /absolute/path/to/codex
uv run python tests/test_real_scopes.py /absolute/path/to/codex
```

前两条无需登录或密钥；后两条只在临时项目和隔离账号目录检查配置，不调用模型。真实模型与桌面检查需要另行运行，范围见 [验证说明](docs/validation.md)。

- [运行设计](docs/architecture.md)
- [发布检查与限制](docs/review.md)
- [贡献与发布](CONTRIBUTING.md)
- [安全说明](SECURITY.md)

技能目录内的安装器和运行脚本是唯一实现来源，根 install.py 仅转交调用。MIT License。
