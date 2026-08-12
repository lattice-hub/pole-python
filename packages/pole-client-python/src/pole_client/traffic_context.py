from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional, Tuple
import unicodedata


BAGGAGE_KEY = "baggage"
TRAFFIC_VERSION_KEY = "latticehub.traffic.version"
TRAFFIC_CAMPAIGN_KEY = "latticehub.traffic.campaign"
TRAFFIC_LANE_KEY = "latticehub.traffic.lane"
TRAFFIC_BUCKET_KEY = "latticehub.traffic.bucket"
_TRAFFIC_PREFIX = "latticehub.traffic."
_TRAFFIC_KEYS = frozenset(
    (TRAFFIC_VERSION_KEY, TRAFFIC_CAMPAIGN_KEY, TRAFFIC_LANE_KEY, TRAFFIC_BUCKET_KEY)
)
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_TOKEN = frozenset(b"!#$%&'*+-.^_`|~ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_WHITE_SPACE = frozenset(
    (
        *range(0x0009, 0x000E), 0x0020, 0x0085, 0x00A0, 0x1680,
        *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
    )
)
_native_context: ContextVar[Optional["TrafficContext"]] = ContextVar(
    "latticehub_traffic_context", default=None
)


class TrafficContextError(ValueError):
    def __init__(self, diagnostic: str, message: str) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class _BaggageMember:
    raw: str
    key: str
    value: str
    has_properties: bool


def _strip_white_space(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and ord(value[start]) in _WHITE_SPACE:
        start += 1
    while end > start and ord(value[end - 1]) in _WHITE_SPACE:
        end -= 1
    return value[start:end]


def _normalize_label(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise TrafficContextError("INVALID_UTF8", f"{field} contains an invalid Unicode scalar")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise TrafficContextError("CONTROL_CHARACTER", f"{field} contains a control character")
    if not value:
        raise TrafficContextError(f"EMPTY_{field.upper()}", f"{field} must not be empty")
    if _strip_white_space(value) != value:
        raise TrafficContextError("SURROUNDING_WHITESPACE", f"{field} must not have surrounding whitespace")
    if len(value.encode("utf-8")) > 128:
        raise TrafficContextError("LABEL_TOO_LARGE", f"{field} exceeds 128 UTF-8 bytes")
    return value


@dataclass(frozen=True)
class TrafficContext:
    campaign: Optional[str] = None
    lane: Optional[str] = None
    bucket: Optional[int] = None
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise TrafficContextError("UNSUPPORTED_VERSION", "TrafficContext version must be 1")
        if self.campaign is not None:
            object.__setattr__(self, "campaign", _normalize_label(self.campaign, "campaign"))
        if self.lane is not None:
            object.__setattr__(self, "lane", _normalize_label(self.lane, "lane"))
        if self.bucket is not None and (not isinstance(self.bucket, int) or isinstance(self.bucket, bool) or not 0 <= self.bucket <= 9999):
            raise TrafficContextError("INVALID_BUCKET", "bucket must be an integer from 0 through 9999")

    def has_labels(self) -> bool:
        return self.campaign is not None or self.lane is not None or self.bucket is not None


class TrafficContextScope:
    def __init__(self, reset: Callable[[], None]) -> None:
        self._reset = reset
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._reset()

    def __enter__(self) -> "TrafficContextScope":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


class _NativeStorage:
    def current(self) -> Optional[TrafficContext]:
        return _native_context.get()

    def attach(self, context: Optional[TrafficContext]) -> TrafficContextScope:
        token: Token[Optional[TrafficContext]] = _native_context.set(context)
        return TrafficContextScope(lambda: _native_context.reset(token))


class _OpenTelemetryStorage:
    def __init__(self, context_module: object, baggage_module: object) -> None:
        self._context = context_module
        self._baggage = baggage_module
        self._key = context_module.create_key("latticehub.traffic.context")

    def current(self) -> Optional[TrafficContext]:
        value = self._context.get_value(self._key)
        if isinstance(value, TrafficContext):
            return value
        return self._restore_baggage()

    def attach(self, context: Optional[TrafficContext]) -> TrafficContextScope:
        attached_context = self._context.get_current()
        for key in self._baggage.get_all(context=attached_context):
            if key.startswith(_TRAFFIC_PREFIX):
                attached_context = self._baggage.remove_baggage(key, context=attached_context)
        attached_context = self._context.set_value(self._key, context, context=attached_context)
        if context is not None and context.has_labels():
            attached_context = self._baggage.set_baggage(
                TRAFFIC_VERSION_KEY, str(context.version), context=attached_context
            )
            if context.campaign is not None:
                attached_context = self._baggage.set_baggage(
                    TRAFFIC_CAMPAIGN_KEY, context.campaign, context=attached_context
                )
            if context.lane is not None:
                attached_context = self._baggage.set_baggage(
                    TRAFFIC_LANE_KEY, context.lane, context=attached_context
                )
            if context.bucket is not None:
                attached_context = self._baggage.set_baggage(
                    TRAFFIC_BUCKET_KEY, str(context.bucket), context=attached_context
                )
        token = self._context.attach(attached_context)
        return TrafficContextScope(lambda: self._context.detach(token))

    def _restore_baggage(self) -> Optional[TrafficContext]:
        entries = self._baggage.get_all()
        if any(key.startswith(_TRAFFIC_PREFIX) and key not in _TRAFFIC_KEYS for key in entries):
            return None
        version = entries.get(TRAFFIC_VERSION_KEY)
        campaign = entries.get(TRAFFIC_CAMPAIGN_KEY)
        lane = entries.get(TRAFFIC_LANE_KEY)
        raw_bucket = entries.get(TRAFFIC_BUCKET_KEY)
        if all(value is None for value in (version, campaign, lane, raw_bucket)):
            return None
        if version != "1":
            return None
        if raw_bucket is not None and (
            not isinstance(raw_bucket, str)
            or not raw_bucket.isascii()
            or not raw_bucket.isdecimal()
            or (len(raw_bucket) > 1 and raw_bucket.startswith("0"))
        ):
            return None
        try:
            return TrafficContext(
                campaign=campaign,
                lane=lane,
                bucket=int(raw_bucket) if raw_bucket is not None else None,
            )
        except (TrafficContextError, TypeError, ValueError):
            return None


_storage: object = _NativeStorage()


def install_opentelemetry_context_adapter() -> bool:
    """Install a real OpenTelemetry Context bridge when the optional package is present."""
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import baggage as otel_baggage
    except ImportError:
        return False
    global _storage
    _storage = _OpenTelemetryStorage(otel_context, otel_baggage)
    return True


def use_native_context_storage() -> None:
    global _storage
    _storage = _NativeStorage()


def current_traffic_context() -> Optional[TrafficContext]:
    return _storage.current()  # type: ignore[union-attr]


def attach_traffic_context(context: Optional[TrafficContext]) -> TrafficContextScope:
    if context is not None and not isinstance(context, TrafficContext):
        raise TypeError("context must be a TrafficContext or None")
    return _storage.attach(context)  # type: ignore[union-attr]


def _encode_label(value: str) -> str:
    return "".join(
        chr(byte) if byte in _UNRESERVED else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )


def _decode_label(value: str, field: str) -> str:
    decoded = bytearray()
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%":
            if index + 2 >= len(value) or any(char not in "0123456789ABCDEF" for char in value[index + 1:index + 3]):
                raise TrafficContextError("NON_CANONICAL_VALUE", f"{field} has an invalid percent escape")
            decoded.append(int(value[index + 1:index + 3], 16))
            index += 3
        else:
            if ord(character) > 0x7F or ord(character) not in _UNRESERVED:
                raise TrafficContextError("NON_CANONICAL_VALUE", f"{field} is not canonical")
            decoded.append(ord(character))
            index += 1
    try:
        decoded_value = decoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TrafficContextError("INVALID_UTF8", f"{field} is not UTF-8") from error
    normalized = _normalize_label(decoded_value, field)
    if _encode_label(normalized) != value:
        raise TrafficContextError("NON_CANONICAL_VALUE", f"{field} is not canonical")
    return normalized


def _trim_ows(value: str) -> str:
    return value.strip(" \t")


def _is_token(value: str) -> bool:
    return bool(value) and all(ord(character) in _TOKEN for character in value)


def _is_baggage_value(value: str) -> bool:
    return all(
        ord(character) == 0x21
        or 0x23 <= ord(character) <= 0x2B
        or 0x2D <= ord(character) <= 0x3A
        or 0x3C <= ord(character) <= 0x5B
        or 0x5D <= ord(character) <= 0x7E
        for character in value
    )


def _validate_property(value: str) -> None:
    property_value = _trim_ows(value)
    if not property_value:
        raise TrafficContextError("INVALID_BAGGAGE", "empty baggage property")
    key, separator, property_data = property_value.partition("=")
    if not _is_token(_trim_ows(key)):
        raise TrafficContextError("INVALID_BAGGAGE", "invalid baggage property key")
    if separator and not _is_baggage_value(_trim_ows(property_data)):
        raise TrafficContextError("INVALID_BAGGAGE", "invalid baggage property value")


def _parse_members(values: Iterable[str]) -> List[_BaggageMember]:
    members: List[_BaggageMember] = []
    total_bytes = 0
    for header_count, header in enumerate(values):
        if not isinstance(header, str):
            raise TypeError("baggage values must be strings")
        try:
            encoded_header = header.encode("ascii")
        except UnicodeEncodeError as error:
            raise TrafficContextError("INVALID_UTF8", "baggage header must be ASCII") from error
        total_bytes += len(encoded_header) + (1 if header_count else 0)
        if total_bytes > 8192:
            raise TrafficContextError("BAGGAGE_TOO_LARGE", "baggage exceeds 8192 bytes")
        for raw_member in header.split(","):
            raw = _trim_ows(raw_member)
            if not raw or "=" not in raw:
                raise TrafficContextError("INVALID_BAGGAGE", "invalid baggage member")
            key, remainder = raw.split("=", 1)
            key = _trim_ows(key)
            if not _is_token(key):
                raise TrafficContextError("INVALID_BAGGAGE", "invalid baggage member")
            value, *properties = remainder.split(";")
            value = _trim_ows(value)
            if not _is_baggage_value(value):
                raise TrafficContextError("INVALID_BAGGAGE", "invalid baggage member value")
            for property_value in properties:
                _validate_property(property_value)
            members.append(_BaggageMember(raw, key, value, bool(properties)))
    if len(members) > 180:
        raise TrafficContextError("TOO_MANY_MEMBERS", "baggage exceeds 180 members")
    return members


def extract_traffic_context(values: Iterable[str]) -> Optional[TrafficContext]:
    decoded = {}
    for member in _parse_members(values):
        if not member.key.startswith(_TRAFFIC_PREFIX):
            continue
        if member.key not in _TRAFFIC_KEYS:
            raise TrafficContextError("UNKNOWN_RESERVED_KEY", f"unknown TrafficContext member {member.key}")
        if member.key in decoded:
            raise TrafficContextError(f"DUPLICATE_{member.key.rsplit('.', 1)[1].upper()}", f"duplicate {member.key}")
        if member.has_properties:
            raise TrafficContextError("RESERVED_PROPERTY", f"{member.key} must not have baggage properties")
        decoded[member.key] = member.value
    if not decoded:
        return None
    if TRAFFIC_VERSION_KEY not in decoded:
        raise TrafficContextError("MISSING_VERSION", "TrafficContext labels require version")
    if decoded[TRAFFIC_VERSION_KEY] != "1":
        raise TrafficContextError("UNSUPPORTED_VERSION", "unsupported TrafficContext version")
    bucket = None
    if TRAFFIC_BUCKET_KEY in decoded:
        raw_bucket = decoded[TRAFFIC_BUCKET_KEY]
        if not raw_bucket.isascii() or not raw_bucket.isdecimal() or (len(raw_bucket) > 1 and raw_bucket.startswith("0")):
            raise TrafficContextError("INVALID_BUCKET", "bucket is not canonical")
        bucket = int(raw_bucket)
    return TrafficContext(
        campaign=_decode_label(decoded[TRAFFIC_CAMPAIGN_KEY], "campaign") if TRAFFIC_CAMPAIGN_KEY in decoded else None,
        lane=_decode_label(decoded[TRAFFIC_LANE_KEY], "lane") if TRAFFIC_LANE_KEY in decoded else None,
        bucket=bucket,
    )


def inject_baggage(values: Iterable[str], context: Optional[TrafficContext] = None) -> Optional[str]:
    active = context if context is not None else current_traffic_context()
    if active is not None and not isinstance(active, TrafficContext):
        raise TypeError("context must be a TrafficContext or None")
    retained = [
        member.raw
        for member in _parse_members(values)
        if not member.key.startswith(_TRAFFIC_PREFIX)
    ]
    if active is not None and active.has_labels():
        retained.append(f"{TRAFFIC_VERSION_KEY}=1")
        if active.campaign is not None:
            retained.append(f"{TRAFFIC_CAMPAIGN_KEY}={_encode_label(active.campaign)}")
        if active.lane is not None:
            retained.append(f"{TRAFFIC_LANE_KEY}={_encode_label(active.lane)}")
        if active.bucket is not None:
            retained.append(f"{TRAFFIC_BUCKET_KEY}={active.bucket}")
    if len(retained) > 180:
        raise TrafficContextError("TOO_MANY_MEMBERS", "baggage exceeds 180 members")
    encoded = ",".join(retained)
    if len(encoded.encode("ascii")) > 8192:
        raise TrafficContextError("BAGGAGE_TOO_LARGE", "baggage exceeds 8192 bytes")
    return encoded or None


def inject_metadata(
    metadata: Iterable[Tuple[str, str]], context: Optional[TrafficContext] = None
) -> Tuple[Tuple[str, str], ...]:
    retained: List[Tuple[str, str]] = []
    existing: List[str] = []
    for name, value in metadata:
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("metadata names and values must be strings")
        if name.lower() == BAGGAGE_KEY:
            existing.append(value)
        else:
            retained.append((name, value))
    baggage = inject_baggage(existing, context)
    if baggage is not None:
        retained.append((BAGGAGE_KEY, baggage))
    return tuple(retained)
