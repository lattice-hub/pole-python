from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from os import environ
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Union
import threading

import grpc

from ._generated import bootstrap_pb2, bootstrap_pb2_grpc


DEFAULT_SIDECAR_SOCKET = "/var/run/pole/sidecar/bootstrap.sock"
SIDECAR_SOCKET_ENVIRONMENT_VARIABLE = "POLE_SIDECAR_SOCKET"


class ListenerProtocol(str, Enum):
    HTTP = "http"
    GRPC = "grpc"
    DUBBO = "dubbo"
    THRIFT = "thrift"


class SidecarBootstrapError(RuntimeError):
    pass


class SidecarInitializationError(SidecarBootstrapError):
    pass


class SidecarUnavailableError(SidecarBootstrapError):
    pass


class InvalidListenerSnapshotError(SidecarBootstrapError):
    pass


@dataclass(frozen=True)
class ListenerSnapshot:
    generation: int
    ports: Mapping[ListenerProtocol, int]

    def endpoint(self, protocol: Union[ListenerProtocol, str]) -> str:
        listener_protocol = _coerce_protocol(protocol)
        return f"127.0.0.1:{self.ports[listener_protocol]}"


_PROTOCOL_TO_WIRE = {
    ListenerProtocol.HTTP: bootstrap_pb2.PROTOCOL_HTTP,
    ListenerProtocol.GRPC: bootstrap_pb2.PROTOCOL_GRPC,
    ListenerProtocol.DUBBO: bootstrap_pb2.PROTOCOL_DUBBO,
    ListenerProtocol.THRIFT: bootstrap_pb2.PROTOCOL_THRIFT,
}
_WIRE_TO_PROTOCOL = {value: key for key, value in _PROTOCOL_TO_WIRE.items()}


def resolve_sidecar_socket(socket_path: Optional[str] = None) -> str:
    resolved = socket_path or environ.get(
        SIDECAR_SOCKET_ENVIRONMENT_VARIABLE, DEFAULT_SIDECAR_SOCKET
    )
    if not resolved:
        raise ValueError("sidecar socket path must not be empty")
    return resolved


def _coerce_protocol(protocol: Union[ListenerProtocol, str]) -> ListenerProtocol:
    if isinstance(protocol, ListenerProtocol):
        return protocol
    if not isinstance(protocol, str):
        raise TypeError("protocol must be a ListenerProtocol or string")
    try:
        return ListenerProtocol(protocol.lower())
    except ValueError as error:
        raise ValueError(f"unsupported listener protocol: {protocol}") from error


def _sdk_version() -> str:
    try:
        return version("pole-client-python")
    except PackageNotFoundError:
        return "0.0.0+local"


class SidecarSession:
    def __init__(
        self,
        socket_path: Optional[str] = None,
        initialization_timeout_seconds: float = 10.0,
        reconnect_initial_backoff_seconds: float = 0.1,
        reconnect_max_backoff_seconds: float = 5.0,
        connection_timeout_seconds: float = 1.0,
    ) -> None:
        if initialization_timeout_seconds <= 0:
            raise ValueError("initialization_timeout_seconds must be positive")
        if reconnect_initial_backoff_seconds <= 0:
            raise ValueError("reconnect_initial_backoff_seconds must be positive")
        if reconnect_max_backoff_seconds < reconnect_initial_backoff_seconds:
            raise ValueError(
                "reconnect_max_backoff_seconds must not be smaller than initial backoff"
            )
        if connection_timeout_seconds <= 0:
            raise ValueError("connection_timeout_seconds must be positive")

        self._socket_path = resolve_sidecar_socket(socket_path)
        self._initialization_timeout_seconds = initialization_timeout_seconds
        self._reconnect_initial_backoff_seconds = reconnect_initial_backoff_seconds
        self._reconnect_max_backoff_seconds = reconnect_max_backoff_seconds
        self._connection_timeout_seconds = connection_timeout_seconds
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._channel: Optional[grpc.Channel] = None
        self._snapshot: Optional[ListenerSnapshot] = None
        self._generation = 0

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def start(self) -> "SidecarSession":
        with self._lock:
            if self._stop.is_set():
                raise SidecarBootstrapError("a closed SidecarSession cannot be restarted")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="pole-sidecar-bootstrap",
                    daemon=True,
                )
                self._thread.start()

        if not self._ready.wait(self._initialization_timeout_seconds):
            self.close()
            raise SidecarInitializationError(
                "Sidecar did not provide a valid listener snapshot before initialization timed out"
            )
        return self

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._invalidate_locked()
            channel = self._channel
        if channel is not None:
            channel.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._connection_timeout_seconds + 0.1)

    def is_available(self) -> bool:
        with self._lock:
            return self._snapshot is not None

    def listener_snapshot(self) -> ListenerSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise SidecarUnavailableError(
                    "Sidecar listener snapshot is unavailable; do not reuse cached endpoints"
                )
            return self._snapshot

    def endpoint(self, protocol: Union[ListenerProtocol, str]) -> str:
        return self.listener_snapshot().endpoint(protocol)

    def _run(self) -> None:
        delay = self._reconnect_initial_backoff_seconds
        while not self._stop.is_set():
            channel = grpc.insecure_channel(
                f"unix:{self._socket_path}",
                options=(("grpc.default_authority", "localhost"),),
            )
            with self._lock:
                self._channel = channel
            try:
                grpc.channel_ready_future(channel).result(
                    timeout=self._connection_timeout_seconds
                )
                stub = bootstrap_pb2_grpc.SidecarSessionServiceStub(channel)
                stream = stub.OpenSession(
                    bootstrap_pb2.ClientHello(
                        sdk_language="python",
                        sdk_version=_sdk_version(),
                        supported_protocols=list(_PROTOCOL_TO_WIRE.values()),
                    )
                )
                first_event = next(stream)
                self._install_snapshot(self._parse_initial_event(first_event))
                delay = self._reconnect_initial_backoff_seconds
                for _event in stream:
                    raise InvalidListenerSnapshotError(
                        "Sidecar Session v1 must not send another event after listener_snapshot"
                    )
                raise SidecarUnavailableError("Sidecar Session stream ended")
            except (
                grpc.RpcError,
                grpc.FutureTimeoutError,
                InvalidListenerSnapshotError,
                SidecarUnavailableError,
                StopIteration,
            ):
                pass
            finally:
                with self._lock:
                    self._invalidate_locked()
                    if self._channel is channel:
                        self._channel = None
                channel.close()

            if not self._stop.wait(delay):
                delay = min(delay * 2, self._reconnect_max_backoff_seconds)

    @staticmethod
    def _parse_initial_event(event) -> Dict[ListenerProtocol, int]:
        if event.WhichOneof("event") != "listener_snapshot":
            raise InvalidListenerSnapshotError(
                "the first Sidecar Session event must be listener_snapshot"
            )

        ports: Dict[ListenerProtocol, int] = {}
        for listener in event.listener_snapshot.listeners:
            protocol = _WIRE_TO_PROTOCOL.get(listener.protocol)
            if protocol is None:
                raise InvalidListenerSnapshotError("listener protocol is unsupported")
            if protocol in ports:
                raise InvalidListenerSnapshotError("listener protocol is duplicated")
            if not 1 <= listener.port <= 65535:
                raise InvalidListenerSnapshotError("listener port must be in 1..65535")
            ports[protocol] = listener.port

        if set(ports) != set(ListenerProtocol):
            raise InvalidListenerSnapshotError(
                "listener snapshot must contain HTTP, gRPC, Dubbo, and Thrift exactly once"
            )
        return ports

    def _install_snapshot(self, ports: Dict[ListenerProtocol, int]) -> None:
        with self._lock:
            self._generation += 1
            self._snapshot = ListenerSnapshot(
                generation=self._generation,
                ports=MappingProxyType(dict(ports)),
            )
            self._ready.set()

    def _invalidate_locked(self) -> None:
        self._snapshot = None
        self._ready.clear()
