---
title: Context-KG 变更日志
tags: [meta, changelog]
links: []
updated: 2026-08-12
sources: 0
---

# Context-KG 变更日志

## [2026-08-20] design | pole-instrument Technical Design

- 新增页面：`pole-instrument-technical-design`，并更新 `index`、`log`。
- 决策摘要：采用独立 distribution 与显式 CLI/API 激活；首期支持 HTTPX sync/async 和
  gRPC sync unary，Dubbo/Thrift 延期；复用 Thin SDK 公开边界并定义断流、代际、fork、诊断、
  安全、回滚和测试契约。
- 范围边界：本次只交付技术设计，不添加运行时代码、启动文件、import hook 或框架 monkey patch。

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
