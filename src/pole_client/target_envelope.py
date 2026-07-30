from dataclasses import dataclass, fields
from ipaddress import IPv6Address
from typing import Dict, Mapping, Optional
import unicodedata

DEFAULT_SIDECAR_ENDPOINT = "http://127.0.0.1:15001"
ENVELOPE_VERSION = "1"

_FIELD_HEADERS = (
    ("namespace", "x-pole-target-namespace"),
    ("service", "x-pole-target-service"),
    ("protocol", "x-pole-target-protocol"),
    ("group", "x-pole-target-group"),
    ("service_version", "x-pole-target-service-version"),
    ("method", "x-pole-target-method"),
    ("original_endpoint", "x-pole-original-endpoint"),
)
_VERSION_HEADER = "x-pole-target-envelope-version"
_FORBIDDEN_HOST_CHARACTERS = frozenset("%/\\[]@?#")
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
_INTERNAL_HEADERS = frozenset(
    (_VERSION_HEADER, *(header for _, header in _FIELD_HEADERS))
)


class TargetEnvelopeError(ValueError):
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


def _normalize(value: Optional[str], field_name: str, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise TargetEnvelopeError(
            "INVALID_UNICODE_SCALAR",
            f"{field_name} must contain only Unicode scalar values",
        )
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise TargetEnvelopeError(
            "CONTROL_CHARACTER",
            f"{field_name} must not contain control characters",
        )
    normalized = _strip_white_space(value)
    if required and not normalized:
        raise TargetEnvelopeError(
            "REQUIRED_FIELD_EMPTY",
            f"{field_name} must not be empty",
        )
    return normalized or None


def _validate_original_endpoint(endpoint: str) -> None:
    bracketed_host = endpoint.startswith("[")
    if bracketed_host:
        closing_bracket = endpoint.find("]")
        if closing_bracket <= 1 or endpoint[closing_bracket + 1 : closing_bracket + 2] != ":":
            raise TargetEnvelopeError(
                "INVALID_ORIGINAL_ENDPOINT",
                "original_endpoint must be host:port or [ipv6]:port",
            )
        if "]" in endpoint[closing_bracket + 1 :]:
            raise TargetEnvelopeError(
                "INVALID_ORIGINAL_ENDPOINT",
                "original_endpoint must be host:port or [ipv6]:port",
            )
        host = endpoint[1:closing_bracket]
        port = endpoint[closing_bracket + 2 :]
    else:
        if endpoint.count(":") != 1:
            raise TargetEnvelopeError(
                "INVALID_ORIGINAL_ENDPOINT",
                "original_endpoint must be host:port or [ipv6]:port",
            )
        host, port = endpoint.rsplit(":", 1)
        if not host:
            raise TargetEnvelopeError(
                "INVALID_ORIGINAL_ENDPOINT",
                "original_endpoint host must not be empty",
            )

    if any(ord(character) in _WHITE_SPACE for character in host):
        raise TargetEnvelopeError(
            "INVALID_ORIGINAL_ENDPOINT",
            "original_endpoint host must not contain whitespace",
        )
    if any(character in _FORBIDDEN_HOST_CHARACTERS for character in host):
        raise TargetEnvelopeError(
            "INVALID_ORIGINAL_ENDPOINT",
            "original_endpoint host contains a forbidden character",
        )

    if bracketed_host:
        try:
            IPv6Address(host)
        except ValueError as error:
            raise TargetEnvelopeError(
                "INVALID_ORIGINAL_ENDPOINT",
                "original_endpoint bracketed host must be a valid IPv6 address"
            ) from error

    if not port or any(character not in "0123456789" for character in port):
        raise TargetEnvelopeError(
            "INVALID_ORIGINAL_ENDPOINT",
            "original_endpoint port must be a decimal integer",
        )
    if len(port) > 1 and port.startswith("0"):
        raise TargetEnvelopeError(
            "INVALID_ORIGINAL_ENDPOINT",
            "original_endpoint port must not contain leading zeroes",
        )
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        raise TargetEnvelopeError(
            "INVALID_ORIGINAL_ENDPOINT",
            "original_endpoint port must be between 1 and 65535",
        )


def _encode_header_value(value: str) -> str:
    encoded = []
    for byte in value.encode("utf-8"):
        if 0x20 <= byte <= 0x7E and byte not in (0x25, 0x2C):
            encoded.append(chr(byte))
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


@dataclass(frozen=True)
class TargetEnvelope:
    namespace: str
    service: str
    protocol: Optional[str] = None
    group: Optional[str] = None
    service_version: Optional[str] = None
    method: Optional[str] = None
    original_endpoint: Optional[str] = None

    def __post_init__(self) -> None:
        required_fields = {"namespace", "service"}
        for field in fields(self):
            normalized = _normalize(
                getattr(self, field.name),
                field.name,
                field.name in required_fields,
            )
            object.__setattr__(self, field.name, normalized)

        if self.original_endpoint is not None:
            _validate_original_endpoint(self.original_endpoint)

    def to_headers(
        self, headers: Optional[Mapping[str, str]] = None
    ) -> Dict[str, str]:
        validated = TargetEnvelope(
            namespace=self.namespace,
            service=self.service,
            protocol=self.protocol,
            group=self.group,
            service_version=self.service_version,
            method=self.method,
            original_endpoint=self.original_endpoint,
        )
        retained_headers = (
            (name, value)
            for name, value in (headers or {}).items()
            if name.lower() not in _INTERNAL_HEADERS
        )
        encoded = dict(retained_headers)
        encoded[_VERSION_HEADER] = ENVELOPE_VERSION
        for field_name, header_name in _FIELD_HEADERS:
            value = getattr(validated, field_name)
            if value is not None:
                encoded[header_name] = _encode_header_value(value)
        return encoded
