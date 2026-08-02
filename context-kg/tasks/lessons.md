# 经验记录

- Python Thin SDK 使用冻结 dataclass 表达不可变目标信封，并在构造时完成规范化。
- Unicode 控制字符使用 `unicodedata.category(value) == "Cc"` 判断。
- Sidecar 尚未消费 TargetEnvelope 前，不能把包构建成功表述为端到端接入完成。
- 用户纠正旧设计后，Python Thin SDK 不能再把默认 HTTP 端点或旧 `TargetEnvelope` 当作稳定 API；业务地址必须只来自 Sidecar 通过 UDS gRPC `OpenSession` 首帧主动下发的协议端口表，UDS 断开后立即失效。
