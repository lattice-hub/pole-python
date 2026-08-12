# Python Thin SDK

## 2026-08-12 TrafficContext 发布边界审查

- [x] 读取完整未提交 diff、`pyproject.toml` 与现有任务记录
- [x] 比对 Sidecar/Go vendored TrafficContext v1 资产和来源状态
- [x] 补齐版本定位、根校验和、打包资产与回归断言
- [x] 运行单元、编译、构建、差异与知识库检查
- [x] 记录验证结果与剩余发布风险

### Review

- `contract/VERSION` 现在同时记录 Sidecar Session、TargetService、TrafficContext 的
  wire version；根据 specification `thin-sdk/README.md`、冻结 `bootstrap.proto` 及 Go/
  Sidecar 版本记录，Sidecar Session 的正确 wire version 为 `1`，不是此前遗留的 `2`。
  TrafficContext 资产已逐字节比对 specification commit
  `67b101bb6e3906b4337affefd33ef778cec692b3` 的 `thin-sdk/traffic-context/v1/`。
- 根 `contract/SHA256SUMS` 覆盖四份 TrafficContext 资产；测试同时校验根与子目录
  校验和。`pyproject.toml` 把它们安装到保留嵌套路径
  `pole_client_contract/traffic-context/v1/`，避免同名 JSON 与 TargetService 资产冲突。
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`（24 项）、
  `python3 -m compileall -q src tests`、`git diff --check` 通过；`uv build` 通过，并实际
  检查 wheel 包含四份 TrafficContext 资产与根 VERSION/SHA256SUMS。
- `context_kg_lint.py ./context-kg` 仍失败：仓库仅有旧式 `tasks/todo.md`、`lessons.md`，
  缺少 frontmatter 和 `_meta/index.md`。这是既有知识库结构与通用 lint 的不兼容，未在本次
  SDK 交付中重构。

## 2026-08-06 Baggage Parser Conformance Repair

- [x] 核对 W3C OWS 与 baggage ABNF 边界
- [x] 添加外部 member、OWS 与长度合并测试
- [x] 修复 parser、保留 raw 与注入语义
- [x] 运行全量测试、编译与差异检查

### Review

- parser 现在按 W3C Baggage ABNF 处理 member、key、value 和 property 的 SP/HTAB OWS；
  外层 OWS 不进入保留 raw，外部空 value 与合法 properties 保持原格式后继续注入。
- 所有外部成员都校验 RFC token 与 baggage-octet；多 header 输入按逗号合并计算并统一
  限制 8192 bytes，extract 与 inject 共享实现。
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`（22 项）、
  `python3 -m compileall -q src tests` 与 `git diff --check` 均通过。

## 2026-08-06 TrafficContext v1

- [x] 核对当前 TrafficContext v1 契约与 TargetService 装配点
- [x] 添加 Baggage、上下文 scope 与元信息接线测试
- [x] 实现 contextvars 与可选 OpenTelemetry storage adapter
- [x] 实现规范 Baggage 编解码并接入 TargetService
- [x] vendor 契约资产、更新 README 并完成全量验证

### Review

- `TrafficContext` 默认通过 `contextvars` scope 存储；可选 OTel adapter 只在显式安装时导入，
  同时读写 OTel Context/Baggage，领域值缺失时从合法 Baggage 恢复，不影响核心包缺少 OTel 时加载。
  TargetService 通过统一元信息路径同时写入内部 TargetService 键和可继续下游传播的 `baggage`。
- 已逐字节 vendor 当前 specification 的 TrafficContext v1 四份资产，并覆盖 canonical
  编解码、保留成员清理、外部 Baggage 保留、scope reset 和显式上下文优先级。
- 已同步新增 `empty_context_cleans_reserved_prefix` 的 conformance 与更新后的 SHA；新增真实
  OTel W3C Baggage Propagator inject/recover 以及无 OTel core loading 回归，并提供 `.[otel]` extra。
- OTel attach 现在枚举并删除全部精确小写 `latticehub.traffic.*`，恢复时遇到未知保留键返回空；
  原生测试逐项执行 vendored `valid`、`sidecar_receive.valid` 与 `sidecar_receive.invalid`，
  invalid 向量逐项校验 diagnostic。
- 通过 `PYTHONPATH=src python3 -m unittest discover -s tests -v`（24 项）、
  `python3 -m compileall -q src tests`、四项资产逐字比对与 `git diff --check`。环境没有可执行的
  `python3 -m build` 模块，构建包验证未执行。

## 2026-08-06 Sidecar Service Session v2

- [x] 核对 `OpenControlSession` 双向流契约与现有会话实现
- [x] vendor v2 proto 和生成代码
- [x] 迁移会话、注册重放与状态处理
- [x] 更新公开 API、README 和真实 UDS 测试
- [x] 运行单元、编译与差异审查

### Review

- `bootstrap.proto`、官方生成的 Python protobuf/gRPC 代码、校验和与版本定位均固定到
  specification `develop` 的合并提交
  `2642bc29c0a512f4da84ec4eb862b1e1ceee9833`。
- `OpenControlSession` 首发 `ClientHello`，首帧原子安装 listener snapshot；线程安全的
  desired registrations 会在重连后重放，并公开注册、注销和状态查询 API。
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`（15/15）、
  `python3 -m compileall -q src tests` 与 `git diff --check` 均通过。当前环境缺少
  `python -m build` 的 CLI 模块，未将其视为可用验证命令。

## 2026-08-02 Sidecar Session v1 与 TargetService v1 迁移

- [x] 审计旧 TargetEnvelope 与正式 v2 契约资产
- [x] vendoring bootstrap.proto 与 TargetService 契约
- [x] 实现 UDS OpenSession、快照与重连
- [x] 迁移 TargetService 元信息 API
- [x] 更新 README、依赖、类型信息和测试
- [x] 运行单元、编译、构建和隔离安装验证

### Review

- 已删除旧 `TargetEnvelope` v1 公共 API 和 `x-pole-*` 运行时实现，改为冻结的 `TargetService(namespace, service)` 与 `latticehub-target-*` 元信息。
- 已 vendor `bootstrap.proto`、Schema、向量和校验和；`contract/VERSION` 已固定
  specification `develop` 的不可变 commit `776f590d1474c51847af75b44522953874097e55`。
- 已通过真实 gRPC UDS server-streaming 覆盖首帧完整性、重复/未知协议、端口校验、断流失效、重连恢复、启动超时及环境变量覆盖。
- Python 3.9 运行 `unittest` 13 项、`compileall`、隔离 sdist/wheel 构建、wheel 资产检查与隔离安装导入均通过。

- [x] 核对 Thin SDK 契约与一致性向量
- [x] 创建 Python src-layout 工程
- [x] 实现冻结的 `TargetEnvelope`
- [x] 实现校验与确定性 Header 编码
- [x] 补充类型标记、测试、README 和许可证
- [x] 运行 unittest、compile 和构建验证

## 2026-07-31 Endpoint 与发布元数据修复

- [x] 拒绝 host 中的 Unicode whitespace
- [x] 拒绝 host 中的 `/\[]@?#`
- [x] 补充 endpoint 负向测试
- [x] 消除 `__version__` 重复维护
- [x] 补充 `project.urls` 发布元数据
- [x] 运行 unittest、compile 和构建验证

## Review

- Python 3.9 的 11 个 unittest 全部通过，sdist、wheel 和隔离导入验证通过。
- 包没有运行时依赖，并通过 `py.typed` 声明类型信息。
- 当前只实现契约核心，不包含 HTTP client 或 Web/RPC 框架 adapter。
- Sidecar 本地 listener 尚未实现，因此没有端到端接入证据。
- 2026-07-31：Python 3.9 的 12 个 unittest、`compileall`、sdist、wheel 和隔离安装冒烟全部通过。
- 2026-07-31：wheel 元数据包含 Homepage、Repository、Issues 和 `Requires-Python: >=3.9`，且没有 `Requires-Dist`。
- 2026-07-31：移除源码 `__version__`，发布版本仅由 `pyproject.toml` 维护。

## 2026-07-31 正式 Thin SDK 契约接入

- [x] 核对正式 tag 与 specification commit
- [x] vendoring Schema、向量、校验和与版本定位
- [x] 实现 Unicode scalar 与精确 White_Space
- [x] 实现 canonical endpoint 与 Header 编码
- [x] 以语言原生测试执行全部 SDK 向量
- [x] 校验 Sidecar receive 资产结构
- [x] 更新 lattice-hub URL 与契约来源说明
- [x] 增加 CI、Dependabot、CODEOWNERS 与治理文档
- [x] 运行测试、编译检查和发布包构建

### Review

- 契约来源固定为 `thin-sdk-contract-v1.0.0` 和完整 commit `f45b0396b4680fe588a93086ceb2934d3e157d04`，vendored JSON 与 specification 源文件逐字节一致，`SHA256SUMS` 校验通过。
- 实现拒绝孤立 surrogate，精确使用 Unicode 15.1 `White_Space`，拒绝 endpoint 端口前导零、zone identifier 和非法 host。
- Header 值按 UTF-8 字节执行 canonical `%HH` 编码，保留 base Header 输入顺序，并大小写不敏感地替换 v1 内部 Header。
- Python 3.9 的 7 个测试方法全部通过，完整执行 9 个 valid、20 个 invalid、2 个 language-specific-invalid 向量，并验证 10 个 Sidecar receive 向量结构。
- `compileall`、sdist 与 wheel 构建通过；项目保持零运行时依赖。
- CI 覆盖 Python 3.9 至 3.13；未初始化 Git、提交或推送。
- 最终审查后 wheel 与 sdist 均包含 VERSION、SHA256SUMS、Schema 和一致性向量；
  解压后的 sdist 测试通过，手动 release-check 会逐项验证四份资产。

## 2026-08-03 PyPI Trusted Publishing

- [x] 配置 PyPI OIDC 与 GitHub Release 工作流
- [x] 验证版本 gate、测试与发布包
- [x] 提交并推送发布配置

### Review

- GitHub Release 标签必须与 `pyproject.toml` 版本一致；构建产物通过 artifact
  传递给独立的 PyPI OIDC publish job，不保存长期 PyPI token。
- 13 个 unittest 全部通过，隔离构建成功生成 sdist 与 wheel，`git diff --check`
  通过。
- PyPI 当前尚无 `pole-client-python`，可用 Pending Trusted Publisher 完成首次发布。
- 修复原 CI 只安装 `build`、未安装 `grpcio` 等项目依赖导致的五版本矩阵失败；
  CI 与 release build 统一安装 `.[dev]`。
