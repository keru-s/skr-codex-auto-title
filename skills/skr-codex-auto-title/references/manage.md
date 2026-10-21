# 管理与故障恢复

以下命令指定实际安装器路径与项目根：

```sh
uv run --no-project --python '>=3.11' python <installer> verify --root <project> --codex <codex-binary>
uv run --no-project --python '>=3.11' python <installer> disable --root <project>
uv run --no-project --python '>=3.11' python <installer> enable --root <project>
uv run --no-project --python '>=3.11' python <installer> uninstall --root <project>
```

关闭/启用保留启用时间和成功 ID。卸载删除自己管理的 handler、运行脚本、backend 和信任段，保留状态、队列、证据和备份；卸载后的历史安装目录会阻止无意重装，临时停用用 disable。

安装 skill 可以单独移除：已安装 hook 只依赖项目内运行脚本和 runtime 配置。仅移除 skill 时不要调用 uninstall，因为它卸载的是 hook。集中保留安装器即可维护多个项目。

状态位于 runtime/skr-codex-auto-title/：settings.json 配置，按稳定会话 ID 保存的 JSON 记录，jobs 队列、evidence 判定/读回、logs 错误。证据可能包含用户请求和最终回复，应保持本地私有并使用项目忽略规则；按组织的数据保留要求清理已不需要的证据。

失败不写 done，API 改名失败保留 pending，后续 Stop 重试相同标题并先读回。已有相同标题时可以恢复成功状态而不重复写名。每次触发失败后直接保留任务，等待后续 Stop，不在当轮连续重试；结构无效的输入/结果也直接停止。没有定时后台服务；会话结束可能取消正在运行的异步 hook。

长内容会精简为首条需求、最新补充、答复开头结尾及最多三段未完成提醒，正文连同模块参考最多 3500 字符。首次生成时读不到用户请求仍跳过；已有待处理标题的改名不依赖新内容。新一轮优先处理最新答复，已有标题保持不变。

排查顺序：verify 配置与信任 → settings 的启用/排除/激活时间 → 当前 ID 的状态/日志 → backend 指向的 CLI 与 CODEX_HOME → doctor 的模型/协议结果。恢复备份前先 disable，核对 files.json，仅恢复自己管理的文件与信任段，保留成功状态；不要整份覆盖用户配置。

排障先查 runtime/skr-codex-auto-title/logs：phase 表示本地失败阶段，rpc_phase 表示最后一次 RPC 操作，attempt/max_attempts 表示尝试次数，elapsed_s 是本次尝试耗时，max_attempts 为 1、retry_in_s 为 null，next_trigger 为 next_stop，表示等待下轮触发。超时 detail 额外记录 RPC 等待阶段及耗时。命名评估和改名阶段可独立定位；done 仍以官方读回为准。

## 今后的版本更新

首次公开发布无需迁移。以后下载新版安装器，对同一项目运行：

```sh
uv run --no-project --python '>=3.11' python <installer> upgrade --root <project> --codex <codex-binary> --trust-hooks
uv run --no-project --python '>=3.11' python <installer> verify --root <project> --codex <codex-binary>
```

更新保留设置、启用时间、模块和已命名记录；失败时恢复自己管理的改动，并保留备份。新版本若改变状态格式，应先提供对应迁移与测试，再发布，不能要求用户删除记录重装。

## 全局管理与项目开关

管理全局安装时，将项目命令中的 --root <project> 替换为 --scope global。全局项目规则使用：

```sh
uv run --no-project --python '>=3.11' python <installer> project-disable --scope global --root <project>
uv run --no-project --python '>=3.11' python <installer> project-enable --scope global --root <project>
uv run --no-project --python '>=3.11' python <installer> project-status --scope global --root <project>
```

规则保留在 CODEX_HOME/skr-codex-auto-title/coordination/projects.json，不向业务项目写文件。子目录继承最近的规则。项目自己的安装优先于全局；全局项目关闭规则同时约束项目安装。全局停用后独立项目仍可工作。

两种范围共享 coordination 下的会话锁、成功凭据和待重试标题；更新与卸载都保留它们。全局私人日志和状态在 CODEX_HOME/skr-codex-auto-title/runtime 下；项目安装的日志与状态仍在业务项目 runtime 下。切换范围后已经完成命名的会话不重复推理。
