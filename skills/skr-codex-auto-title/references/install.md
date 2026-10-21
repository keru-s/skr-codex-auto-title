# 安装与兼容性

要求 Python 3.11+、macOS 或 Linux、已登录且支持相应协议的 Codex CLI。桌面和 CLI 共用所选 CODEX_HOME；对某个桌面安装的可操作性要实测，不能由独立 app-server 启动成功推断。

从本 skill 的 scripts/install.py 执行；以下 `<installer>` 指该绝对路径，`<project>` 指真实项目根。

```sh
uv run --no-project --python '>=3.11' python <installer> doctor --root <project> --codex <codex-binary>
uv run --no-project --python '>=3.11' python <installer> install --root <project> --codex <codex-binary> --modules-file <utf8-module-file> --exclude-session-id <installation-chat-id> --trust-hooks
uv run --no-project --python '>=3.11' python <installer> upgrade --root <project> --codex <codex-binary> --trust-hooks
uv run --no-project --python '>=3.11' python <installer> verify --root <project> --codex <codex-binary>
```

doctor 生成实际 CLI 的实验协议 schema，检查 thread/start、turn/start、thread/name/set、thread/read、hooks/list 所需字段，并查询真实账号 model/list。它不进行模型推理，不证明桌面改名成功。模型不在目录或 effort 不支持时安装失败；可由用户明确提供 `--model`、`--effort`，没有静默回退。

省略 modules-file 的直接安装使用通用模块提示，不声称已经分析项目。Skill 安装提供项目定制文件。省略 exclude-session-id 表示终端安装；在 Codex 对话内安装时必须提供当前安装对话 ID。

`--trust-hooks` 明确授权安装器写入自己的 trust 段。安装器使用 hooks/list 返回的真实 key/hash，保留其他信任设置；未知协议或本地改动会停止安装。项目本身需要由用户在 Codex 中信任，安装器不替用户信任整个项目。

安装产生 `.codex/hooks.json`、`.codex/hooks/first_completion.py` 和两项 runtime 私有目录。脚本和命令的绝对路径按本机生成，项目移动后需重新核对。共享或克隆业务项目时，每台机器分别运行安装器，不复制 runtime、backend、信任哈希或别人的会话状态。

当前实现会覆盖首次完成前的人工标题，这是公开行为；后续任务和已记为成功的会话不再自动覆盖。标题主要为中文。Windows 暂不支持。

## 全局安装

全局命令不传 --root：

```sh
uv run --no-project --python '>=3.11' python <installer> doctor --scope global --codex <codex-binary>
uv run --no-project --python '>=3.11' python <installer> install --scope global --codex <codex-binary> --exclude-session-id <installation-chat-id> --trust-hooks
uv run --no-project --python '>=3.11' python <installer> verify --scope global --codex <codex-binary>
```

全局命名使用通用模块，不把安装所在项目的术语用于所有项目。脚本及私有状态位于所选 CODEX_HOME/skr-codex-auto-title/，注册写入 CODEX_HOME/hooks.json。全局安装不替用户信任整个业务项目。两种范围并存时项目安装优先；项目安装停用后全局也不接管。

全局 project-disable/project-enable 规则保存在个人目录，对两种范围都生效，按最长匹配路径选择。安装过全局 Hook 后即可管理这些规则，卸载后仍可重新开启项目。项目级 disable 只影响该项目安装；全局 disable 只影响全局安装。
