# pole-client-python

`pole-client-python` 是 Pole Sidecar 的 Python Thin SDK 核心包。它负责构造、
校验并编码正式 `TargetEnvelope v1`，不绑定任何 HTTP 或 RPC 框架。

## 契约来源

仓库 `contract/` vendoring 自
[`lattice-hub/specification`](https://github.com/lattice-hub/specification)
的正式 tag `thin-sdk-contract-v1.0.0`，对应完整 commit
`f45b0396b4680fe588a93086ceb2934d3e157d04`。`contract/VERSION` 记录来源，
`contract/SHA256SUMS` 用于验证 Schema 和一致性向量未发生漂移。

语言原生测试会读取并执行全部 SDK valid、invalid 与
language-specific-invalid 向量；`sidecar_receive` 向量仅验证 vendored
资产结构，不在 SDK 中实现 Sidecar 接收端。

Pole Sidecar 尚未完成对应 listener 的端到端验证，因此当前版本只声明契约核心
兼容，不声明 Thin SDK 到 Sidecar 已经生产就绪。

## 要求

- Python 3.9 至 3.13
- 零运行时依赖

## 安装

```bash
python -m pip install .
```

## 使用

```python
from pole_client import DEFAULT_SIDECAR_ENDPOINT, TargetEnvelope

target = TargetEnvelope(
    namespace="default",
    service="orders",
    protocol="grpc",
    method="GetOrder",
    original_endpoint="orders.internal:8080",
)

headers = target.to_headers({"x-request-id": "request-1"})
```

`TargetEnvelope` 按契约精确处理 Unicode scalar、Unicode 15.1
`White_Space`、控制字符和 `original_endpoint`。`to_headers()` 会重新校验
对象，按输入顺序保留非内部 base Header，大小写不敏感地替换 v1 内部 Header，
并采用 canonical UTF-8 `%HH` 线路编码。

应用仍需自行选择网络客户端并连接 Sidecar；框架 adapter 不属于当前核心包。

## 开发

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
python -m build
```

## 许可证

BSD 3-Clause License，详见 `LICENSE`。
