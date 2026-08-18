# pole-python

`pole-python` 是 Pole Python 运行时的轻量 monorepo。当前只包含保持兼容的 Python Thin SDK
发行包；自动 instrumentation、`sitecustomize`、启动器和框架 monkey patch 尚未实现。

## Packages

- [`packages/pole-client-python`](packages/pole-client-python) — PyPI distribution
  `pole-client-python`，import 路径继续使用 `pole_client`。

## 开发

```bash
python -m pip install -e 'packages/pole-client-python[dev]'
PYTHONPATH=packages/pole-client-python/src \
  python -m unittest discover -s packages/pole-client-python/tests -v
python -m compileall -q \
  packages/pole-client-python/src packages/pole-client-python/tests
python -m build \
  --outdir packages/pole-client-python/dist \
  packages/pole-client-python
```

## 范围边界

仓库结构为后续独立 Python runtime modules 预留位置，但不会为了目录对称创建空包。
安装 `pole-client-python` 不会自动导入 `sitecustomize`，也不会修改用户进程中的框架行为。

后续 `pole-instrument` 启动器的 Proposed 设计见
[`context-kg/technical/adr/pole-instrument.md`](context-kg/technical/adr/pole-instrument.md)；当前没有
已获批准的框架适配器或生产实现。
