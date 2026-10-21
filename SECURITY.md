# 安全与隐私

本工具会读取会话输入/最终回复、调用所选 Codex 模型并修改对话标题。安装器的 --trust-hooks 只授权自己管理的命令，不改变项目整体信任、全局模型或其他 hook。

发现问题时先按安装范围停用：全局使用 disable --scope global，单独项目使用 disable --scope project --root <project>。全局下只关闭某项目可用 project-disable --scope global --root <project>。公开 issue 提供合成复现、版本和脱敏错误；不要附带 runtime、CODEX_HOME、会话 JSONL、access token 或安装备份。仓库正式发布后，建议通过 GitHub 私密漏洞报告提交敏感复现，需维护者先启用该功能。

语义完成判断可能误判，人工标题在首次自动命名前允许覆盖。锁保护本工具的合作进程，不防范恶意本地用户或同时编辑同一配置的其他程序。Windows 不受支持。
