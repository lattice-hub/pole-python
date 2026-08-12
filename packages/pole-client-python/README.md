# pole-client-python

`pole-client-python` 是 Pole Sidecar 的 Python Thin SDK 核心包。它不代理业务流量：
通过本机 Unix Domain Socket 上的官方 gRPC `OpenControlSession` 双向控制会话接收
Sidecar 下发的 listener 地址、登记本地服务，并为业务协议注入冻结的 `TargetService v1`
元信息。

本包由 [`pole-python`](https://github.com/lattice-hub/pole-python) monorepo 维护；PyPI
distribution 名称、`pole_client` import 路径与公共接口保持兼容。安装本包不会启用
`sitecustomize` 或任何自动 instrumentation。

## 契约来源

`contract/` vendoring specification `develop` 中的 Sidecar Session v1、
`TargetService v1` Schema、向量及校验和。`contract/VERSION` 固定不可变 tag 与
commit；正式端到端兼容组合仍以 specification 的 compatibility matrix 为准。

`bootstrap.proto` 来自正式定义的
`pole.sidecar.v1.SidecarSessionService/OpenControlSession`；包内 `_generated/` 是其通过官方
`grpcio-tools 1.71.0` 生成的 Python 代码。语言原生测试执行全部 TargetService SDK
向量，并验证 UDS server-streaming 的会话行为。

## 要求

- Python 3.9 至 3.13
- `grpcio >= 1.71, < 2`
- `protobuf >= 5.29, < 6`

## 安装

```bash
python -m pip install pole-client-python
```

## 使用

Sidecar 的 bootstrap socket 默认为 `/var/run/pole/sidecar/bootstrap.sock`，可仅通过
`POLE_SIDECAR_SOCKET` 覆盖。Thin SDK 不内置、配置或猜测业务 listener 端口。

```python
from pole_client import ListenerProtocol, SidecarSession, TargetService

session = SidecarSession().start()
try:
    registration_id = session.register_local_service(
        namespace="default",
        service="catalog",
        protocol=ListenerProtocol.GRPC,
        local_port=50051,
    )
    grpc_endpoint = session.endpoint(ListenerProtocol.GRPC)
    target = TargetService(namespace="default", service="orders")

    http_headers = target.to_metadata({"traceparent": "00-..."})
    grpc_metadata = target.to_grpc_metadata((("traceparent", "00-..."),))

    # 将业务客户端连接到 grpc_endpoint，并传入 grpc_metadata。
    # HTTP/Thrift-over-HTTP 使用 http_headers；Dubbo 使用同名 attachment。
finally:
    session.close()
```

`TargetService` 只有 `namespace` 与 `service` 两个必填字段，使用
`latticehub-target-namespace` 和 `latticehub-target-service` 写入元信息。它精确校验
Unicode scalar、Unicode 15.1 `White_Space`、控制字符，并以 canonical UTF-8 `%HH`
编码值；合并时会覆盖调用方伪造的同名字段。

`TrafficContext(campaign=None, lane=None, bucket=None)` 通过同一出站元信息装配点写入
W3C `baggage`。`attach_traffic_context(context)` 使用 `contextvars` 安装可嵌套 scope，
`current_traffic_context()` 在未安装时返回 `None`。安装 `.[otel]` 后可调用
`install_opentelemetry_context_adapter()` 切换到真实 OTel Context/Baggage，标准 W3C Baggage
Propagator 可直接发送 version、campaign、lane、bucket，领域值缺失时会从合法 OTel Baggage
恢复。不存在 OTel 时核心 SDK 仍可正常加载并使用 `contextvars`。
`TargetService.to_metadata(..., traffic_context=explicit)` 的显式参数优先，否则读取 current；
它覆盖 TargetService 内部键、保留外部 Baggage，并清理旧保留键。
`traceparent` 不是灰度关联键。自动 HTTP/gRPC/Dubbo/Thrift 框架入口 hook 不属于本次范围。

`SidecarSession.start()` 在有界时间内进行指数退避连接。第一个客户端事件固定为
`ClientHello`，首个服务端事件必须包含 HTTP、gRPC、
Dubbo、Thrift 四个不重复的合法端口；安装后 `endpoint(protocol)` 线程安全地返回
`127.0.0.1:{port}`。UDS stream 断开时快照立即失效，后续请求必须快速失败；后台重连
成功后原子安装新快照，并在 `ClientHello` 后重放全部 desired registrations。调用
`register_local_service(namespace, service, protocol, local_port, registration_id=None)` 会返回
稳定注册 ID；`unregister_local_service(registration_id)` 删除 desired registration。通过
`local_service_status(registration_id)` 可读取最新的 `registered`、`unregistered` 或
`rejected` 状态，断流时状态立即失效。核心包不持有框架连接池，adapter 应在
`listener_snapshot()` 的 generation 改变或 `SidecarUnavailableError` 时废弃自己的旧连接池。

Thrift v1 使用 Apache Thrift 官方 HTTP Transport 与标准 HTTP Header，不引入私有帧。

## 开发

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
python -m build
```

## 许可证

BSD 3-Clause License，详见 `LICENSE`。
