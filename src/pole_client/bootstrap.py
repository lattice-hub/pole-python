from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from os import environ
from queue import Queue
from types import MappingProxyType
from typing import Dict, Iterator, Mapping, Optional, Union
from uuid import uuid4
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


class LocalServiceState(str, Enum):
    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    REJECTED = "rejected"


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


@dataclass(frozen=True)
class LocalServiceRegistration:
    registration_id: str
    namespace: str
    service: str
    protocol: ListenerProtocol
    local_port: int


@dataclass(frozen=True)
class LocalServiceStatus:
    registration_id: str
    state: LocalServiceState
    message: str


_PROTOCOL_TO_WIRE = {
    ListenerProtocol.HTTP: bootstrap_pb2.PROTOCOL_HTTP,
    ListenerProtocol.GRPC: bootstrap_pb2.PROTOCOL_GRPC,
    ListenerProtocol.DUBBO: bootstrap_pb2.PROTOCOL_DUBBO,
    ListenerProtocol.THRIFT: bootstrap_pb2.PROTOCOL_THRIFT,
}
_WIRE_TO_PROTOCOL = {value: key for key, value in _PROTOCOL_TO_WIRE.items()}
_WIRE_TO_LOCAL_SERVICE_STATE = {
    bootstrap_pb2.LOCAL_SERVICE_STATE_REGISTERED: LocalServiceState.REGISTERED,
    bootstrap_pb2.LOCAL_SERVICE_STATE_UNREGISTERED: LocalServiceState.UNREGISTERED,
    bootstrap_pb2.LOCAL_SERVICE_STATE_REJECTED: LocalServiceState.REJECTED,
}


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


def _normalize_required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _validate_local_port(local_port: object) -> int:
    if isinstance(local_port, bool) or not isinstance(local_port, int):
        raise TypeError("local_port must be an integer in 1..65535")
    if not 1 <= local_port <= 65535:
        raise ValueError("local_port must be in 1..65535")
    return local_port


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
        self._active_requests: Optional[Queue] = None
        self._snapshot: Optional[ListenerSnapshot] = None
        self._desired_registrations: Dict[str, LocalServiceRegistration] = {}
        self._local_service_statuses: Dict[str, LocalServiceStatus] = {}
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
                    name="pole-sidecar-control-session",
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
            self._local_service_statuses.clear()
            request_queue = self._active_requests
            self._active_requests = None
            channel = self._channel
        if request_queue is not None:
            request_queue.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._connection_timeout_seconds + 0.1)
        if channel is not None:
            channel.close()

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

    def register_local_service(
        self,
        namespace: str,
        service: str,
        protocol: Union[ListenerProtocol, str],
        local_port: int,
        registration_id: Optional[str] = None,
    ) -> str:
        registration = LocalServiceRegistration(
            registration_id=_normalize_required_text(
                registration_id if registration_id is not None else uuid4().hex,
                "registration_id",
            ),
            namespace=_normalize_required_text(namespace, "namespace"),
            service=_normalize_required_text(service, "service"),
            protocol=_coerce_protocol(protocol),
            local_port=_validate_local_port(local_port),
        )
        with self._lock:
            if self._stop.is_set():
                raise SidecarBootstrapError(
                    "a closed SidecarSession cannot register local services"
                )
            existing = self._desired_registrations.get(registration.registration_id)
            if existing is not None:
                if existing != registration:
                    raise ValueError(
                        "registration_id is already used by another local service"
                    )
                return registration.registration_id
            self._desired_registrations[registration.registration_id] = registration
            self._local_service_statuses.pop(registration.registration_id, None)
            self._enqueue_locked(self._registration_event(registration))
        return registration.registration_id

    def unregister_local_service(self, registration_id: str) -> bool:
        normalized_registration_id = _normalize_required_text(
            registration_id, "registration_id"
        )
        with self._lock:
            registration = self._desired_registrations.pop(
                normalized_registration_id, None
            )
            if registration is None:
                return False
            self._local_service_statuses.pop(normalized_registration_id, None)
            self._enqueue_locked(
                bootstrap_pb2.ClientEvent(
                    unregister_local_service=bootstrap_pb2.LocalServiceUnregistration(
                        registration_id=normalized_registration_id
                    )
                )
            )
        return True

    def local_service_status(
        self, registration_id: str
    ) -> Optional[LocalServiceStatus]:
        normalized_registration_id = _normalize_required_text(
            registration_id, "registration_id"
        )
        with self._lock:
            return self._local_service_statuses.get(normalized_registration_id)

    def _run(self) -> None:
        delay = self._reconnect_initial_backoff_seconds
        while not self._stop.is_set():
            channel = grpc.insecure_channel(
                f"unix:{self._socket_path}",
                options=(("grpc.default_authority", "localhost"),),
            )
            request_queue: Queue = Queue()
            with self._lock:
                self._channel = channel
            try:
                grpc.channel_ready_future(channel).result(
                    timeout=self._connection_timeout_seconds
                )
                stub = bootstrap_pb2_grpc.SidecarSessionServiceStub(channel)
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._active_requests = request_queue
                    self._enqueue_locked(
                        bootstrap_pb2.ClientEvent(
                            hello=bootstrap_pb2.ClientHello(
                                sdk_language="python",
                                sdk_version=_sdk_version(),
                                supported_protocols=list(_PROTOCOL_TO_WIRE.values()),
                            )
                        )
                    )
                    for registration in self._desired_registrations.values():
                        self._enqueue_locked(self._registration_event(registration))
                stream = stub.OpenControlSession(self._request_events(request_queue))
                first_event = next(stream)
                self._install_snapshot(self._parse_initial_event(first_event))
                delay = self._reconnect_initial_backoff_seconds
                for event in stream:
                    self._handle_event(event)
                raise SidecarUnavailableError("Sidecar control session stream ended")
            except (
                grpc.RpcError,
                grpc.FutureTimeoutError,
                InvalidListenerSnapshotError,
                SidecarUnavailableError,
                StopIteration,
                TypeError,
                ValueError,
            ):
                pass
            finally:
                with self._lock:
                    if self._active_requests is request_queue:
                        self._active_requests = None
                        request_queue.put(None)
                    self._invalidate_locked()
                    self._local_service_statuses.clear()
                    if self._channel is channel:
                        self._channel = None
                channel.close()

            if not self._stop.wait(delay):
                delay = min(delay * 2, self._reconnect_max_backoff_seconds)

    @staticmethod
    def _request_events(request_queue: Queue) -> Iterator:
        while True:
            event = request_queue.get()
            if event is None:
                return
            yield event

    @staticmethod
    def _registration_event(
        registration: LocalServiceRegistration,
    ) -> bootstrap_pb2.ClientEvent:
        return bootstrap_pb2.ClientEvent(
            register_local_service=bootstrap_pb2.LocalServiceRegistration(
                registration_id=registration.registration_id,
                namespace=registration.namespace,
                service=registration.service,
                protocol=_PROTOCOL_TO_WIRE[registration.protocol],
                local_port=registration.local_port,
            )
        )

    def _enqueue_locked(self, event) -> None:
        if self._active_requests is not None:
            self._active_requests.put(event)

    def _handle_event(self, event) -> None:
        if event.WhichOneof("event") != "local_service_status":
            raise InvalidListenerSnapshotError(
                "events after the listener snapshot must be local_service_status"
            )
        registration_id = _normalize_required_text(
            event.local_service_status.registration_id, "registration_id"
        )
        state = _WIRE_TO_LOCAL_SERVICE_STATE.get(event.local_service_status.state)
        if state is None:
            raise InvalidListenerSnapshotError("local service status has an invalid state")
        with self._lock:
            self._local_service_statuses[registration_id] = LocalServiceStatus(
                registration_id=registration_id,
                state=state,
                message=event.local_service_status.message,
            )

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
