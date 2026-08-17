---
title: pole-instrument 自动增强技术设计
status: Proposed
tags: [architecture, python, instrumentation, sidecar]
links: [pole-python-monorepo, todo]
updated: 2026-08-17
sources: 5
---

# pole-instrument 自动增强技术设计

## 状态与范围

Proposed。本页面向 Python runtime、框架 adapter 和发布维护者，定义 Q3
自动增强的实现边界；它不表示功能已发布。[[pole-python-monorepo]] 仍是已实现的
monorepo 与兼容发行决策，本提案只解决其延期的 instrumentation 设计。

## 产品与包边界

- 新建独立 PyPI distribution `pole-instrument`，拥有 `pole_instrument` module 和
  `pole-instrument` console launcher。它依赖、但不重新导出 `pole-client-python`。
- `pole-client-python` 继续拥有 `pole_client`、`SidecarSession`、`TargetService` 和
  `TrafficContext`。安装或导入它不启动会话、不安装 hook、不修改框架。
- framework adapter 作为 `pole-instrument` extras 发布，例如未来的
  `pole-instrument[httpx]`；框架依赖不得成为 `pole-client-python` 的必选依赖。
- 两个 distribution 独立版本化，`pole-instrument` 声明受测的
  `pole-client-python` 版本区间。每次发布覆盖 Python 3.9–3.13 的 wheel/sdist
  隔离安装和兼容矩阵。

## 启用与配置

自动增强默认关闭，只由 `pole-instrument [options] -- application ...` launcher
显式启用。首期不安装 `sitecustomize.py`、`.pth` 文件，也不通过导入
`pole_client` 触发。launcher 在应用入口执行前配置 runtime 并安装限定的
import hooks；已导入的框架由 adapter 的显式 late-install 能力决定，不支持时
发出诊断并跳过。

配置优先级从高到低是 launcher 参数、`POLE_INSTRUMENT_*` 环境变量、内置
安全默认值。`--disable`/`POLE_INSTRUMENT_ENABLED=false` 是显式 kill switch，
优先于其他设置。首期不读取隐式的工程配置文件。精确的 CLI 和环境
变量名属于 Phase 0 产品冻结项，在此之前不视为已发布接口。

## Runtime 与 adapter 所有权

`pole_instrument.runtime` 是进程级协调器，拥有配置、一个 `SidecarSession`、
adapter registry 和诊断状态。它的 `activate()` 和 `deactivate()` 是拟议公开接口；
连续 `activate()` 必须返回同一活跃 runtime，不重复包装调用点。`deactivate()`
停止新增强、按逆序调用 adapter cleanup、关闭会话；已无法安全解包的
第三方 monkey patch 必须在 adapter 能力中明示并诊断。

adapter 仅可修改其声明支持的框架版本，以 capability probe 而非宽松版本
猜测决定安装。每个 adapter 维护 install token、原始 callable 和 recursion guard；
缺少框架、不支持版本、重复安装或检测到竞争 patch 时不得阻止应用启动。

## 请求与 Sidecar 流程

```text
launcher -> runtime.activate -> adapter registry -> SidecarSession.start
application request -> adapter interceptor -> session.endpoint(protocol)
                    -> TargetService.to_*_metadata -> framework transport -> Sidecar listener
inbound request -> adapter extracts baggage -> attach_traffic_context -> handler -> scope reset
```

1. launcher 解析配置，安装 registry/import hooks，并在执行用户入口前创建 runtime。
2. runtime 启动 `SidecarSession`。启动期 Sidecar 不可用时默认 **fail-open**：
   应用继续启动，但 adapter 不改写请求、不猜测 endpoint，并在后台有界重连。
3. 出站 interceptor 从 adapter 的显式 route/target 配置构造 `TargetService`，
   每次调用 `session.endpoint(protocol)`。它通过 `TargetService.to_metadata()` 或
   `to_grpc_metadata()` 注入 canonical target 和当前 `TrafficContext`，因此调用方
   伪造的 `latticehub-target-*` 值会被覆盖。
4. stream 断开或 `SidecarUnavailableError` 会立即使该 generation 的 adapter 连接池
   失效。正在处理的增强请求快速失败；默认 fail-open 只允许尚未改写的
   请求使用原框架行为，不允许继续使用旧 listener。重连安装新
   generation 后 adapter 新建连接池。
5. 入站 adapter 仅从框架的请求 metadata 提取 `TrafficContext`，在 handler 周围
   使用 `attach_traffic_context()`。`finally` 中必须 reset，依赖 `contextvars` 保持
   thread/async-task 的请求隔离。无效 baggage 只记录脱敏诊断，不污染当前上下文。

## 进程生命周期

- thread 共享 runtime/session；请求值使用 `contextvars`。adapter 自有状态必须加锁或
  保持不可变。
- async task 在创建时继承 context；adapter 不得把 request context 放入进程全局。
- launcher 不为 subprocess 隐式注入 instrumentation；子进程只在被 launcher 显式
  启动时增强。
- pre-fork master 可安装无 I/O import hooks，但不可把已连接的 session、线程、
  channel 或连接池传入 worker。runtime 通过 `register_at_fork` 将 child 标记为
  uninitialized，worker 的 post-fork hook 新建 session 和 adapter 资源。

## 诊断与安全不变式

runtime 通过标准 `logging` 和只读状态快照暴露 enabled、adapter 名/版本/状态、
Sidecar available/unavailable、snapshot generation、重连次数和最后一个分类错误。
日志不记录 baggage 值、target 值、headers、credentials、payload、UDS 响应内容或
应用参数；自定义 socket path 只输出脱敏后的 basename/hash。

必须持续满足：默认 opt-in；激活幂等；缺少/不支持框架不阻止启动；
不猜测或复用失效 endpoint；连接池与 snapshot generation 绑定；仅使用
`TargetService` 生成内部 metadata；`TrafficContext` 不跨请求泄漏；关闭或
安装失败时保留普通框架行为；interceptor 必须防止对 Sidecar/control/
diagnostic 流量自增强造成递归。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| monkey-patch 顺序或竞争 | 记录原 callable 标识，检测替换，安全时拒绝覆盖并诊断 |
| import 太早/太晚 | launcher 先安装 hook；adapter 声明 late-install 能力 |
| fork 后复用 channel/线程 | at-fork 失效，worker post-fork 重建 |
| context 泄漏 | handler `finally` reset，线程/async 并发测试 |
| self-instrumentation/递归 | control socket、diagnostic exporter 和 adapter 内部调用 bypass token |
| 诊断泄密 | allowlist 字段和值脱敏测试 |
| 框架/依赖版本偏移 | extras 隔离、受测窗口、capability probe、独立 release |
| OpenTelemetry 上下文冲突 | 仅显式启用 OTel adapter，不重复注入，以 W3C baggage 为交换边界 |

## 分阶段实现与验收

- **Phase 0：公共契约冻结。** 产品负责人决定首个 framework/protocol adapter，
  同时冻结 CLI/环境变量名和 fail-open 是否可被 fail-closed 覆盖。出口：审核的
  compatibility matrix 和公共接口文档。
- **Phase 1：runtime 骨架，无框架 patch。** 实现 launcher、优先级、幂等、
  disable、诊断脱敏、Sidecar generation/fork 生命周期。出口：隔离安装证明
  core 无自动产物；启用/重复启用/关闭、Sidecar 不可用/断流/新 generation、
  pre/post-fork 和 redaction 公共 seam 测试通过。
- **Phase 2：一个经决策的 adapter。** 当前不预设 HTTPX、Requests、gRPC、
  ASGI 或其他框架。出口：真实本地 framework request + fake UDS Sidecar E2E，
  canonical target metadata，inbound TrafficContext 的嵌套/async/thread 隔离，不支持版本、
  recursion、普通行为回退和 pool invalidation 测试通过。
- **Phase 3：扩展 adapters。** 每个框架单独决策、extra 和 E2E 矩阵；不得将
  HTTP/gRPC/Dubbo/Thrift 一并声称为已支持。

## 开放决策

- **首个 adapter：** 未决定；由 Python runtime 与产品负责人在 Phase 0
  根据用户需求和可测 E2E 冻结。
- **精确 CLI/环境变量：** 未决定；由 runtime maintainer 在开发 Phase 1 前提案。
- **fail-closed override：** 首期默认 fail-open 已决定；是否允许运营方显式
  fail-closed 由产品/安全负责人在 Phase 0 决定。
- **编程式 `activate()` 的稳定等级：** 拟议公开，但是否与 launcher 同时进入
  1.0 稳定契约由 runtime maintainer 在 Phase 0 决定。

## 证据

- `context-kg/technical/adr/pole-python-monorepo.md`
- `packages/pole-client-python/src/pole_client/bootstrap.py`
- `packages/pole-client-python/src/pole_client/target_service.py`
- `packages/pole-client-python/src/pole_client/traffic_context.py`
- `packages/pole-client-python/tests/test_thin_sdk.py`

## 相关页面

- [[pole-python-monorepo]]
- [[todo]]
