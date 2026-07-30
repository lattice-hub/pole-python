# 贡献指南

## 开发流程

1. 从最新开发分支创建短生命周期分支。
2. 保持修改聚焦，并为行为变化补充测试。
3. 运行单元测试、`compileall` 和 `python -m build`。
4. 提交 Pull Request，并说明契约兼容性和验证结果。

## 契约更新

`contract/` 只能 vendoring 自 `lattice-hub/specification` 的正式 Thin SDK
契约 tag。更新时必须同步 `schema.json`、`conformance.json`、`SHA256SUMS`
和 `VERSION`，并确保语言原生一致性测试全部通过。

不得在本仓自行修改契约语义；发现规范或向量冲突时，应先在 specification
仓库修复并按 SemVer 发布新契约。
