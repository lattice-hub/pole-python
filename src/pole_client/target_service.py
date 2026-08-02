from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple
import unicodedata


TARGET_NAMESPACE_KEY = "latticehub-target-namespace"
TARGET_SERVICE_KEY = "latticehub-target-service"
_TARGET_KEYS = frozenset((TARGET_NAMESPACE_KEY, TARGET_SERVICE_KEY))
_WHITE_SPACE = frozenset(
    (
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


class TargetServiceError(ValueError):
    def __init__(self, diagnostic: str, message: str) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


def _strip_white_space(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and ord(value[start]) in _WHITE_SPACE:
        start += 1
    while end > start and ord(value[end - 1]) in _WHITE_SPACE:
        end -= 1
    return value[start:end]


def _normalize(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise TargetServiceError(
            "INVALID_UNICODE_SCALAR",
            f"{field_name} must contain only Unicode scalar values",
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise TargetServiceError(
            "CONTROL_CHARACTER",
            f"{field_name} must not contain control characters",
        )
    normalized = _strip_white_space(value)
    if not normalized:
        raise TargetServiceError(
            f"EMPTY_{field_name.upper()}",
            f"{field_name} must not be empty",
        )
    return normalized


def _encode_metadata_value(value: str) -> str:
    encoded = []
    for byte in value.encode("utf-8"):
        if 0x20 <= byte <= 0x7E and byte not in (0x25, 0x2C):
            encoded.append(chr(byte))
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def _merge_metadata(
    items: Iterable[Tuple[str, str]], target: "TargetService"
) -> Tuple[Tuple[str, str], ...]:
    retained = []
    for name, value in items:
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("metadata names and values must be strings")
        if name.lower() not in _TARGET_KEYS:
            retained.append((name, value))
    retained.extend(
        (
            (TARGET_NAMESPACE_KEY, _encode_metadata_value(target.namespace)),
            (TARGET_SERVICE_KEY, _encode_metadata_value(target.service)),
        )
    )
    return tuple(retained)


@dataclass(frozen=True)
class TargetService:
    namespace: str
    service: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", _normalize(self.namespace, "namespace"))
        object.__setattr__(self, "service", _normalize(self.service, "service"))

    def to_metadata(
        self, metadata: Optional[Mapping[str, str]] = None
    ) -> Dict[str, str]:
        validated = TargetService(self.namespace, self.service)
        return dict(_merge_metadata((metadata or {}).items(), validated))

    def to_grpc_metadata(
        self, metadata: Iterable[Tuple[str, str]] = ()
    ) -> Tuple[Tuple[str, str], ...]:
        validated = TargetService(self.namespace, self.service)
        return _merge_metadata(metadata, validated)
