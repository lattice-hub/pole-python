---
title: Context-KG 变更日志
tags: [meta, changelog]
links: []
updated: 2026-08-17
sources: 0
---

# Context-KG 变更日志

## [2026-08-17] proposal | pole-instrument 自动增强技术设计

- 新增页面：`pole-instrument`；更新页面：`index`、`log`、`todo`。
- 确定独立 distribution、显式 launcher、通用 Adapter 协议、公共传播 seam、Sidecar/pre-fork
  生命周期、fail-open、配置、诊断、安全与验证边界。
- 当前框架支持矩阵为空；本变更不包含运行时代码、隐式启动 hook 或 Thin SDK 行为变化。

## [2026-08-12] delivery | pole-python 迁移进入 develop

- 更新页面：`pole-python-monorepo`、`todo`、`log`。
- 变更摘要：GitHub/本地仓库完成改名，兼容 Python 客户端以 monorepo package 形式推送到
  `develop`；PyPI distribution/import 保持兼容，自动 instrumentation 未启用。
- 发布边界：PyPI Trusted Publisher 的 repository subject 仍需在首次发布前更新。
- 远端证据：Actions run `31605443604` 的 Python 3.9–3.13 五项 CI 全部通过。

## [2026-08-12] decision | pole-python 轻量 Monorepo

- 新增页面：`pole-python-monorepo`、`schema`、`index`、`log`。
- 更新页面：`todo`、`lessons`。
- 变更摘要：仓库改名为 `pole-python`，兼容发行包迁入 `packages/pole-client-python`；
  distribution/import 保持不变，自动 instrumentation 明确延期。

## 相关页面

无。
