# 发布流程

`发布检查` workflow 只构建 `packages/pole-client-python` 的 wheel 和 sdist，并上传
Actions artifact，不发布到 PyPI。正式发布前必须确认版本、契约 tag、PyPI 项目所有权和
trusted publishing 配置，再通过独立审核的发布变更启用上传。

GitHub 仓库名为 `lattice-hub/pole-python`，PyPI distribution 仍为
`pole-client-python`。仓库重命名后，首次发布前必须在 PyPI 更新 Trusted Publisher 的
GitHub repository subject；不能用真实发布试探配置。
