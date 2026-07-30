# 经验记录

- Python Thin SDK 使用冻结 dataclass 表达不可变目标信封，并在构造时完成规范化。
- Unicode 控制字符使用 `unicodedata.category(value) == "Cc"` 判断。
- Sidecar 尚未消费 TargetEnvelope 前，不能把包构建成功表述为端到端接入完成。
