---
title: pole-python 轻量 Monorepo 与兼容发行边界
tags: [architecture, python, monorepo, packaging]
links: [todo]
updated: 2026-08-12
sources: 8
---

# pole-python 轻量 Monorepo 与兼容发行边界

## 状态

Implemented；Q1、Q2 已进入 `develop`，Q3 自动增强延期。

## 背景

Python Thin SDK 已包含两个稳定职责：`TargetService`/`TrafficContext` 传播语义，以及通过
gRPC over UDS 维护 Sidecar Control Session。未来自动增强还会引入进程启动、import hook、
框架版本适配和 monkey patch 生命周期，不应让这些依赖反向污染兼容客户端。

## 决策

- GitHub 仓库由 `pole-client-python` 改名为 `pole-python`，表达完整 Python runtime 的长期边界。
- 仓库采用 `packages/` 轻量 monorepo；当前仅包含 `packages/pole-client-python/`，不为了目录
  对称创建空 module。
- PyPI distribution `pole-client-python`、import 路径 `pole_client`、公共接口、版本读取和
  契约语义保持兼容。
- `TargetService`、`TrafficContext` 和 `SidecarSession` 暂不拆成多个 wheel；当前一个兼容包的
  interface 更深，拆包只会增加安装和版本协调负担。
- 本次不实现 Q3：兼容 wheel 不包含 `sitecustomize.py`、`usercustomize.py`、`.pth`、
  `pole-instrument` 或框架 monkey patch。

## 发布边界

- CI、release check 和 PyPI workflow 从 `packages/pole-client-python` 构建，并校验 wheel/sdist
  中的许可证、`pole_client` 与 vendored 契约资产。
- GitHub 仓库重命名不等于 PyPI distribution 重命名。
- GitHub OIDC subject 会随仓库名变化；首次发布前必须在 PyPI 更新 Trusted Publisher 为
  `lattice-hub/pole-python`。

## 延期事项

自动 instrumentation 需要单独决策其 distribution、启动器、框架 Adapter、fail-open、
pre-fork 生命周期和诊断接口。在该决策完成前，核心客户端保持显式导入和显式调用。

## 证据

- `packages/pole-client-python/pyproject.toml`
- `packages/pole-client-python/src/pole_client/__init__.py`
- `packages/pole-client-python/src/pole_client/bootstrap.py`
- `packages/pole-client-python/src/pole_client/target_service.py`
- `packages/pole-client-python/src/pole_client/traffic_context.py`
- `.github/workflows/ci.yml`
- `.github/workflows/release-check.yml`
- `.github/workflows/release.yml`

## 相关页面

- [[todo]]
- [[pole-instrument]]
