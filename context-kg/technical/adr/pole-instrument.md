---
title: pole-instrument 自动增强技术设计
tags: [architecture, python, instrumentation]
links: [pole-python-monorepo, todo]
updated: 2026-08-17
sources: 0
---

# pole-instrument 自动增强技术设计

## 状态

Proposal；本文只确定实现边界，不交付运行时代码或框架支持。

## 目标

- 提供显式启用、可诊断、fail-open 的 Python 自动增强入口。
- 复用 `pole-client-python` 已冻结的 Sidecar、TargetService 与 TrafficContext 公共接口。
- 隔离框架依赖，使未启用增强的 Thin SDK 用户不承担导入和启动副作用。

## 非目标

- 本设计不声明任何 HTTP、gRPC、Dubbo 或 Thrift 框架及版本已经受支持。
- 不改变 TargetService、TrafficContext 或 Sidecar Session wire contract。
- 不实现 telemetry exporter、Sidecar 功能、框架 patch 或新的传播格式。

## 假设

Sidecar 仍通过本机受信 UDS 暴露冻结的 control session；具体框架支持矩阵须由独立变更批准，
并为每个框架提供公共接口 E2E 证据后才能宣称支持。

## 包边界与兼容性

自动增强使用独立的 pole-instrument distribution（import package 为 `pole_instrument`），并依赖
`pole-client-python`。现有 distribution 名、`pole_client` imports、公共导出和显式调用行为保持
不变。Thin SDK wheel 不包含框架模块、增强依赖或启动文件；Python 3.9–3.13 均须通过独立安装
验证。框架依赖只能由 `pole-instrument` 的命名 extra 引入。新增或改变 adapter 支持范围遵循
语义化版本；移除已声明支持是 breaking change。

`pole-instrument` wheel/sdist 只包含 launcher、bootstrap、adapter contract、已批准 adapter 和
诊断设施；每个已批准 adapter 必须在发布说明中列出框架及精确支持版本。当前矩阵为空。

## 显式激活

唯一进程激活面是 `pole-instrument` console entry point：
`pole-instrument [instrument options] -- application [application arguments...]`。分隔符后的 argv 和
环境逐项转交 application；launcher 使用 `exec` 等价语义，使应用接收原有信号，并原样返回退出码。
bootstrap 失败按下述策略决定是否启动应用。

安装任一 distribution 都不激活增强。不使用 `sitecustomize.py`、`.pth` 或全局 `import hook`。
`POLE_INSTRUMENT_ENABLED=0` 是显式 disable 开关，launcher 仍直接执行 application。未来如需 late
import hook，必须另行设计并保持 launcher 内 opt-in；不得通过 Thin SDK 安装隐式启用。

## Adapter 协议与选择

`pole_instrument.adapters` entry-point group 是唯一 discover 机制。每个 adapter 提供稳定 id、
framework distribution 名、`supported_versions`、`install(runtime)` 与 `uninstall()`。bootstrap
只选择已安装且版本落入声明区间的 adapter；框架缺失或 unsupported 时跳过并产生有界诊断，
不尝试猜测兼容性。支持矩阵为空时不会安装任何 adapter。

`install` 和 `uninstall` 必须 idempotent。bootstrap 按 adapter id 保存进程级 ownership token，
wrapper 也携带 token，从而阻止重复 patch；安装中途失败须回滚已完成步骤。无法可靠 uninstall 的
框架不得进入支持矩阵。adapter 之间不能 patch 同一 ownership point，冲突时后者跳过。

## 入站请求与上下文

adapter 只从框架公开的 W3C `baggage` carrier 调用 `extract_traffic_context(...)`。有合法结果时调用
`attach_traffic_context(...)`，并在同步返回、异常、取消以及异步或 streaming completion 的最终路径
调用 `TrafficContextScope.close()`。无 carrier 时不安装上下文；格式错误、未知保留字段或不支持版本
沿用现有 `TrafficContextError` 契约，记录传播拒绝后 fail-open，且不把部分上下文交给应用。

scope 必须归属于单个请求；线程使用现有 contextvars 隔离，async task 继承 Python contextvars 语义。
复用 worker 的下一请求开始前必须已完成 close，异常处理不得保留前一请求状态。非 Pole baggage
不由 adapter 解析或改写。

## 出站目标与传播

目的 namespace/service 只能来自已验证的显式 adapter 配置或框架原生 destination mapping；缺失或
无效时跳过增强，不猜测目标。映射生成 `TargetService(namespace, service)`，HTTP 风格 carrier 只调用
`TargetService.to_metadata(existing_metadata)`，gRPC carrier 只调用
`TargetService.to_grpc_metadata(existing_metadata)`。由这些公共方法保留无关用户 metadata、替换大小写
不同的伪造目标键并注入当前 TrafficContext；adapter 不自行序列化 Baggage，不产生 `x-pole-*`。

metadata 必须先完整装配成功再替换请求 carrier；任何校验或装配错误都保留原请求，不允许部分注入。

## Sidecar 所有权与连接生命周期

每个 worker 的 bootstrap runtime 拥有一个共享 `SidecarSession`；adapter 不创建自己的 session。
runtime 安装 adapter 前启动 session，shutdown 时按相反顺序执行 `unregister_local_service`、adapter
uninstall 和 session close。入站服务 adapter 使用 `register_local_service`，并公开处理 registration
rejection；重复 shutdown 安全无副作用。

连接池记录其来源 snapshot `generation`。每次使用前通过公开 snapshot 核对 generation；变化时丢弃
旧池。`SidecarUnavailableError` 或断连会立即标记全部池不可用，直到公开 session 提供新 generation。
不能缓存或猜测业务 listener，不能回退到默认端口，也不能在不可用期间复用 stale endpoint。

## pre-fork、并发与关闭

pre-fork parent 只解析配置、discover adapter 和验证静态兼容性，不启动 `SidecarSession`、gRPC channel、
后台 thread 或安装 request patch。每个 worker 必须在 after fork hook 中独立完成 runtime install；若运行器
没有可靠 after fork hook，launcher 要求应用在 worker factory 中调用一次显式 bootstrap，否则跳过增强并
诊断。父子进程绝不共享 live channel/thread。

worker 内安装和关闭由锁保护且 idempotent；每请求 contextvars 隔离线程与 task。shutdown 先停止接收新
增强工作，再等待已进入的 scope 完成，最后释放注册与 session；超时后仍关闭资源并让应用退出流程继续。

## failure policy 与配置

默认 fail-open：Sidecar 缺失/重连、adapter 缺失/unsupported/安装错误、registration rejection 和传播拒绝
都不改变应用业务调用、异常类型或响应，只跳过对应增强。fail-open 不允许 stale listener、默认 listener、
partial metadata 或半安装 patch。唯一启动失败是 launcher 自身无法定位/执行 application，或用户显式设置
`POLE_INSTRUMENT_STRICT_CONFIG=1` 时出现配置错误；其他配置错误产生 `CONFIG_INVALID` 并禁用增强。

配置优先级为 launcher argument > `POLE_INSTRUMENT_*` environment > 内置默认。Sidecar socket 不新增
别名，仍由 `POLE_SIDECAR_SOCKET`/`resolve_sidecar_socket()` 决定；显式 launcher socket argument 作为
`SidecarSession(socket_path=...)` 参数优先。未知选项产生 `CONFIG_UNKNOWN`；空值、非法布尔值和越界数值产生
`CONFIG_INVALID`。配置只选择 adapter、诊断和生命周期策略，不定义新的 TargetService/TrafficContext wire。

## 诊断

稳定类别为 `ACTIVATION_STARTED|DISABLED|FAILED`、`ADAPTER_INSTALLED|SKIPPED|FAILED`、
`SIDECAR_AVAILABLE|UNAVAILABLE|RECOVERED`、`REGISTRATION_REJECTED`、
`PROPAGATION_REJECTED`、`CONFIG_UNKNOWN|INVALID`。默认写 stderr 的结构化 warning；
`POLE_INSTRUMENT_LOG_LEVEL` 控制 verbosity，应用也可提供 diagnostic callback。

相同 category、adapter 和原因按进程做 token-bucket rate limit，并对 Sidecar 状态只记录转换，避免
per-request log storm。默认诊断不得包含 baggage/target values、header、argv 中的凭证、UDS payload、
authorization 或其他 secret；只输出 adapter id、框架版本、稳定 reason code 和必要计数。

## 安全

入站 carrier 是不可信输入，只交给现有有长度和字符限制的 `extract_traffic_context`，拒绝内容不进入 scope
或日志。patch 范围限于支持矩阵列出的公开调用点，禁止 broad module mutation 和 eval；entry-point provider
必须来自锁定/审核的依赖。目标 spoof replacement 完全委托 TargetService 公共 seam。

本地 UDS 的信任前提是部署平台限制 socket 文件权限；增强不认证远端网络 Sidecar。诊断执行字段级 allowlist
和换行转义。extras 必须固定框架版本区间并接受依赖扫描；unsupported 版本安全跳过，不能扩大 patch 探测面。

## 验证策略

- 单元/契约：adapter 选择、版本区间、幂等安装回滚、配置优先级、diagnostic rate limit；逐项复用
  TargetService 与 TrafficContext vendored conformance，并验证所有 cleanup 分支。
- 生命周期 integration：用真实 UDS fake 驱动公开 `SidecarSession`，覆盖 registration、断连、generation
  变化、stale pool 丢弃、并发、pre-fork/after fork 与幂等 shutdown。
- clean process 激活：在新虚拟环境分别安装 Thin SDK、未激活的 instrument 包和 launcher 激活场景，验证
  imports、argv/environment、signal、exit code，以及 wheel/sdist 不含隐式 startup hook。
- 每个未来声明支持的框架/版本必须有公共接口 E2E：未修改样例应用仅经 launcher 启动，证明入站 scope、
  出站 metadata、Sidecar 重连和 fail-open；adapter internal mock 不能替代该门禁。

这些层分别覆盖包/激活（AC2/3/12）、adapter/failure（AC4/8）、传播（AC5/6）、生命周期（AC7/9）、
诊断配置安全（AC10/11/13）。现有 Thin SDK unittest、compileall、构建与包成员检查保持为回归门禁。

## 未决问题

- 首个框架、版本区间、公开 patch point 与 destination mapping 尚待独立批准和威胁审查。
- pre-fork server 的具体 after fork integration 随首个支持矩阵一起决定；在此之前不得宣称兼容。
- launcher 是否提供配置文件属于后续提案；当前只接受 arguments 和 environment。
