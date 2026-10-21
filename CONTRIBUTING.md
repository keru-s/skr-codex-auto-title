# 贡献与发布

运行 Python 3.11+。核心仅依赖标准库；离线测试不需要 Codex 登录。

```sh
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run python tests/check_distribution.py
```

修改唯一来源 skills/skr-codex-auto-title/scripts/install.py 或 assets/first_completion.py，补充可观察行为测试。改变会话完成判定、并发、信任或读回时同时更新兼容性/隐私说明。私有运行证据放仓库外或忽略目录。

GitHub Actions 会在 Ubuntu/macOS、Python 3.11/3.14 运行离线测试和分发检查，不注入账号密钥。真实 CLI、模型与桌面测试由维护者按 docs/validation.md 手动执行并区分证据层级。

发布步骤：确认版本与 CHANGELOG，执行测试、检查分发内容和许可证；在自己 GitHub 账号创建公共仓库，添加真实 remote，提交并推送，按维护者明确要求决定是否创建tag/release；本次保持1.0.0且不创建release。发布前把实际安装地址加入 README。创建公开仓库与推送前，先取得独立审查通过结论，核对待发布文件；本地检查通过不代表已经托管发布，不提供尚不存在的下载链接。

发布后的用户可 clone 仓库使用根 install.py，或通过 Codex 的 skill-installer 从真实仓库的 skills/skr-codex-auto-title 路径安装。直接拷贝该技能目录也受支持。
