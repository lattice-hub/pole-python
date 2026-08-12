import hashlib
import sys
import json
from concurrent import futures
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import grpc

from pole_client import (
    DEFAULT_SIDECAR_SOCKET,
    TARGET_NAMESPACE_KEY,
    TARGET_SERVICE_KEY,
    InvalidListenerSnapshotError,
    LocalServiceState,
    ListenerProtocol,
    SidecarInitializationError,
    SidecarSession,
    SidecarUnavailableError,
    TargetService,
    TargetServiceError,
    TrafficContext,
    TrafficContextError,
    attach_traffic_context,
    current_traffic_context,
    extract_traffic_context,
    inject_baggage,
    install_opentelemetry_context_adapter,
    resolve_sidecar_socket,
    use_native_context_storage,
)
from pole_client._generated import bootstrap_pb2, bootstrap_pb2_grpc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "contract"
CONFORMANCE = json.loads(
    (CONTRACT_ROOT / "conformance.json").read_text(encoding="utf-8")
)


def wait_until(predicate, timeout_seconds=2.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class BootstrapServicer(bootstrap_pb2_grpc.SidecarSessionServiceServicer):
    def __init__(self, event, on_client_event=None):
        self.event = event
        self.on_client_event = on_client_event
        self.opened = False
        self.events = []
        self.stream_closed = False

    def OpenControlSession(self, request_iterator, context):
        try:
            first_event = next(request_iterator)
            self.opened = True
            self.events.append(first_event)
            yield self.event
            for event in request_iterator:
                self.events.append(event)
                if self.on_client_event is not None:
                    response = self.on_client_event(event)
                    if response is not None:
                        yield response
        finally:
            self.stream_closed = True


def listener_snapshot(ports):
    wire_protocols = {
        ListenerProtocol.HTTP: bootstrap_pb2.PROTOCOL_HTTP,
        ListenerProtocol.GRPC: bootstrap_pb2.PROTOCOL_GRPC,
        ListenerProtocol.DUBBO: bootstrap_pb2.PROTOCOL_DUBBO,
        ListenerProtocol.THRIFT: bootstrap_pb2.PROTOCOL_THRIFT,
    }
    return bootstrap_pb2.SidecarEvent(
        listener_snapshot=bootstrap_pb2.ListenerSnapshot(
            listeners=[
                bootstrap_pb2.Listener(protocol=wire_protocols[protocol], port=port)
                for protocol, port in ports.items()
            ]
        )
    )


class ContractAssetTest(unittest.TestCase):
    def test_contract_assets_and_checksums(self):
        self.assertEqual("latticehub-target-service", CONFORMANCE["contract"])
        self.assertEqual("1.0.0", CONFORMANCE["contract_version"])
        self.assertIn(
            "contract=latticehub-thin-sdk-sidecar\n",
            (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "target_service_wire_version=1\n",
            (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "sidecar_session_wire_version=1\n",
            (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "traffic_context_wire_version=1\n",
            (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "traffic_context_contract_version=1.0.0\n",
            (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8"),
        )

        checksums = (CONTRACT_ROOT / "SHA256SUMS").read_text(encoding="utf-8")
        for line in checksums.strip().splitlines():
            expected, file_name = line.split()
            actual = hashlib.sha256((CONTRACT_ROOT / file_name).read_bytes()).hexdigest()
            self.assertEqual(expected, actual)

    def test_vendored_proto_defines_open_control_session(self):
        proto = (CONTRACT_ROOT / "bootstrap.proto").read_text(encoding="utf-8")
        self.assertIn(
            "rpc OpenControlSession(stream ClientEvent) returns (stream SidecarEvent);",
            proto,
        )
        self.assertIn("service SidecarSessionService", proto)


class TargetServiceConformanceTest(unittest.TestCase):
    def test_valid_vectors_and_immutability(self):
        for vector in CONFORMANCE["valid"]:
            with self.subTest(vector=vector["name"]):
                target = TargetService(**vector["input"])
                self.assertEqual(vector["normalized"]["namespace"], target.namespace)
                self.assertEqual(vector["normalized"]["service"], target.service)
                self.assertEqual(
                    [tuple(item) for item in vector["expected_metadata"]],
                    list(target.to_metadata().items()),
                )
                with self.assertRaises(FrozenInstanceError):
                    target.service = "payments"  # type: ignore[misc]

    def test_invalid_vectors(self):
        for vector in CONFORMANCE["invalid"]:
            with self.subTest(vector=vector["name"]):
                with self.assertRaises(TargetServiceError) as caught:
                    TargetService(**vector["input"])
                self.assertEqual(vector["diagnostic"], caught.exception.diagnostic)

    def test_metadata_replaces_forged_target_values(self):
        target = TargetService(namespace="default", service="orders")
        self.assertEqual(
            {
                "Authorization": "Bearer token",
                TARGET_NAMESPACE_KEY: "default",
                TARGET_SERVICE_KEY: "orders",
            },
            target.to_metadata(
                {
                    "Authorization": "Bearer token",
                    "LatticeHub-Target-Namespace": "forged",
                    TARGET_SERVICE_KEY: "forged",
                }
            ),
        )
        self.assertEqual(
            (
                ("traceparent", "00-abc"),
                (TARGET_NAMESPACE_KEY, "default"),
                (TARGET_SERVICE_KEY, "orders"),
            ),
            target.to_grpc_metadata(
                (
                    ("traceparent", "00-abc"),
                    (TARGET_NAMESPACE_KEY, "forged"),
                )
            ),
        )

    def test_revalidates_modified_frozen_object(self):
        target = TargetService(namespace="default", service="orders")
        object.__setattr__(target, "service", "\ud800")
        with self.assertRaises(TargetServiceError) as caught:
            target.to_metadata()
        self.assertEqual("INVALID_UNICODE_SCALAR", caught.exception.diagnostic)


class TrafficContextTest(unittest.TestCase):
    def test_contract_assets_and_checksums(self):
        root = CONTRACT_ROOT / "traffic-context" / "v1"
        conformance = json.loads((root / "conformance.json").read_text(encoding="utf-8"))
        self.assertEqual("latticehub-traffic-context", conformance["contract"])
        for line in (root / "SHA256SUMS").read_text(encoding="utf-8").strip().splitlines():
            expected, file_name = line.split()
            self.assertEqual(expected, hashlib.sha256((root / file_name).read_bytes()).hexdigest())
        root_checksums = dict(
            reversed(line.split(maxsplit=1))
            for line in (CONTRACT_ROOT / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        )
        for file_name in ("README.md", "schema.json", "conformance.json", "SHA256SUMS"):
            path = root / file_name
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                root_checksums[f"traffic-context/v1/{file_name}"],
            )

    def test_scope_restores_context_and_target_service_uses_current_context(self):
        self.assertIsNone(current_traffic_context())
        current = TrafficContext(campaign="checkout-v2", lane="gray", bucket=42)
        with attach_traffic_context(current):
            self.assertEqual(current, current_traffic_context())
            metadata = TargetService("default", "orders").to_grpc_metadata(
                (("baggage", "vendor=value"),)
            )
        self.assertIsNone(current_traffic_context())
        self.assertEqual(
            "vendor=value,latticehub.traffic.version=1,latticehub.traffic.campaign=checkout-v2,"
            "latticehub.traffic.lane=gray,latticehub.traffic.bucket=42",
            dict(metadata)["baggage"],
        )

    def test_optional_opentelemetry_adapter_never_blocks_native_storage(self):
        native = TrafficContext(lane="native")
        use_native_context_storage()
        with attach_traffic_context(native):
            self.assertEqual(native, current_traffic_context())
        if install_opentelemetry_context_adapter():
            otel = TrafficContext(lane="otel")
            with attach_traffic_context(otel):
                self.assertEqual(otel, current_traffic_context())
        use_native_context_storage()

    def test_optional_opentelemetry_adapter_writes_and_recovers_baggage(self):
        from opentelemetry import baggage, context as otel_context
        from opentelemetry.baggage.propagation import W3CBaggagePropagator

        traffic_context = TrafficContext(campaign="checkout-v2", lane="gray", bucket=42)
        self.assertTrue(install_opentelemetry_context_adapter())
        try:
            seeded = baggage.set_baggage("vendor.key", "preserved")
            seeded = baggage.set_baggage("latticehub.traffic.future", "stale", context=seeded)
            seeded = baggage.set_baggage("latticehub.traffic.lane", "stale", context=seeded)
            seed_token = otel_context.attach(seeded)
            try:
                with attach_traffic_context(traffic_context):
                    self.assertEqual(traffic_context, current_traffic_context())
                    self.assertEqual("preserved", baggage.get_baggage("vendor.key"))
                    self.assertIsNone(baggage.get_baggage("latticehub.traffic.future"))
                    self.assertEqual("1", baggage.get_baggage("latticehub.traffic.version"))
                    self.assertEqual("checkout-v2", baggage.get_baggage("latticehub.traffic.campaign"))
                    self.assertEqual("gray", baggage.get_baggage("latticehub.traffic.lane"))
                    self.assertEqual("42", baggage.get_baggage("latticehub.traffic.bucket"))
                    carrier = {}
                    W3CBaggagePropagator().inject(carrier, context=otel_context.get_current())
                    self.assertEqual(
                        traffic_context,
                        extract_traffic_context([carrier["baggage"]]),
                    )
            finally:
                otel_context.detach(seed_token)

            recovered = baggage.set_baggage("latticehub.traffic.version", "1")
            recovered = baggage.set_baggage("latticehub.traffic.lane", "recovered", context=recovered)
            token = otel_context.attach(recovered)
            try:
                self.assertEqual(TrafficContext(lane="recovered"), current_traffic_context())
            finally:
                otel_context.detach(token)

            invalid = baggage.set_baggage("latticehub.traffic.lane", "missing-version")
            token = otel_context.attach(invalid)
            try:
                self.assertIsNone(current_traffic_context())
            finally:
                otel_context.detach(token)

            unknown_reserved = baggage.set_baggage("latticehub.traffic.version", "1")
            unknown_reserved = baggage.set_baggage(
                "latticehub.traffic.lane", "gray", context=unknown_reserved
            )
            unknown_reserved = baggage.set_baggage(
                "latticehub.traffic.future", "unknown", context=unknown_reserved
            )
            token = otel_context.attach(unknown_reserved)
            try:
                self.assertIsNone(current_traffic_context())
            finally:
                otel_context.detach(token)
        finally:
            use_native_context_storage()

    def test_missing_opentelemetry_does_not_block_native_context_storage(self):
        use_native_context_storage()
        with patch.dict(sys.modules, {"opentelemetry": None}):
            self.assertFalse(install_opentelemetry_context_adapter())
        with attach_traffic_context(TrafficContext(lane="native")):
            self.assertEqual(TrafficContext(lane="native"), current_traffic_context())

    def test_explicit_context_overrides_current_and_preserves_foreign_baggage(self):
        current = TrafficContext(lane="gray")
        explicit = TrafficContext(campaign="checkout-v3")
        with attach_traffic_context(current):
            metadata = TargetService("default", "orders").to_metadata(
                {
                    "Baggage": "vendor=value;property=one,latticehub.traffic.lane=forged",
                    TARGET_SERVICE_KEY: "forged",
                },
                traffic_context=explicit,
            )
        self.assertEqual(
            "vendor=value;property=one,latticehub.traffic.version=1,"
            "latticehub.traffic.campaign=checkout-v3",
            metadata["baggage"],
        )
        self.assertEqual("orders", metadata[TARGET_SERVICE_KEY])

    def test_baggage_vectors_and_rejection_rules(self):
        root = CONTRACT_ROOT / "traffic-context" / "v1"
        conformance = json.loads((root / "conformance.json").read_text(encoding="utf-8"))
        for vector in conformance["valid"]:
            with self.subTest(vector=vector["name"]):
                context = TrafficContext(**vector["input"]["labels"])
                self.assertEqual(
                    vector["expected_baggage"],
                    inject_baggage(vector["existing_baggage"], context),
                )
        for vector in conformance["sidecar_receive"]["valid"]:
            with self.subTest(vector=vector["name"]):
                expected = TrafficContext(**vector["expected"]["labels"])
                self.assertEqual(expected, extract_traffic_context(vector["baggage"]))
        for vector in conformance["sidecar_receive"]["invalid"]:
            with self.subTest(vector=vector["name"]):
                with self.assertRaises(TrafficContextError) as caught:
                    extract_traffic_context(vector["baggage"])
                self.assertEqual(vector["diagnostic"], caught.exception.diagnostic)

        self.assertIsNone(inject_baggage(["latticehub.traffic.lane=stale"], None))
        with self.assertRaises(TrafficContextError):
            extract_traffic_context(["latticehub.traffic.version=1;l=1,latticehub.traffic.lane=gray"])
        with self.assertRaises(TrafficContextError):
            extract_traffic_context(["latticehub.traffic.version=1;,latticehub.traffic.lane=gray"])
        with self.assertRaises(TrafficContextError):
            extract_traffic_context(["latticehub.traffic.version=1,latticehub.traffic.unknown=x"])
        with self.assertRaises(TrafficContextError):
            extract_traffic_context(["latticehub.traffic.version=1,latticehub.traffic.lane=%e7%81%b0"])
        with self.assertRaises(TrafficContextError):
            TrafficContext(lane=" gray")

    def test_w3c_ows_empty_external_value_and_properties_are_preserved(self):
        extracted = extract_traffic_context(
            [
                " \tlatticehub.traffic.version \t=\t 1\t ,\t"
                "latticehub.traffic.lane\t=\tgray \t",
            ]
        )
        self.assertEqual(TrafficContext(lane="gray"), extracted)
        baggage = inject_baggage(
            [" \tvendor \t=\t \t;\tflag\t;\tproperty \t=\tvalue\t ", "\tempty=\t"],
            TrafficContext(lane="gray"),
        )
        self.assertEqual(
            "vendor \t=\t \t;\tflag\t;\tproperty \t=\tvalue,empty=,"
            "latticehub.traffic.version=1,latticehub.traffic.lane=gray",
            baggage,
        )

    def test_external_members_are_validated_and_input_limit_is_shared(self):
        invalid = (
            "bad key=value",
            'vendor="quoted"',
            "vendor=has space",
            "vendor=value;bad key=x",
            "vendor=value;property=has space",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(TrafficContextError):
                    extract_traffic_context([value])
                with self.assertRaises(TrafficContextError):
                    inject_baggage([value], TrafficContext())

        values = ["a=" + "x" * 4094, "b=" + "x" * 4093]
        self.assertEqual(8192, len(",".join(values).encode("ascii")))
        self.assertIsNone(extract_traffic_context(values))
        self.assertEqual(",".join(values), inject_baggage(values, TrafficContext()))
        too_large = [values[0], values[1] + "x"]
        with self.assertRaises(TrafficContextError):
            extract_traffic_context(too_large)
        with self.assertRaises(TrafficContextError):
            inject_baggage(too_large, TrafficContext())


class SidecarSessionTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.socket_path = str(Path(self.temporary_directory.name) / "bootstrap.sock")
        self.servers = []
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        for server in self.servers:
            server.stop(0).wait()
        self.temporary_directory.cleanup()

    def start_server(self, event, on_client_event=None):
        servicer = BootstrapServicer(event, on_client_event)
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        bootstrap_pb2_grpc.add_SidecarSessionServiceServicer_to_server(servicer, server)
        self.assertEqual(1, server.add_insecure_port(f"unix://{self.socket_path}"))
        server.start()
        self.servers.append(server)
        return server, servicer

    def new_session(self):
        session = SidecarSession(
            socket_path=self.socket_path,
            initialization_timeout_seconds=1.0,
            reconnect_initial_backoff_seconds=0.01,
            reconnect_max_backoff_seconds=0.05,
            connection_timeout_seconds=0.1,
        )
        self.sessions.append(session)
        return session

    def test_open_control_session_sends_hello_then_installs_all_protocol_endpoints(self):
        _, servicer = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21001,
                    ListenerProtocol.GRPC: 21002,
                    ListenerProtocol.DUBBO: 21003,
                    ListenerProtocol.THRIFT: 21004,
                }
            )
        )
        session = self.new_session().start()

        wait_until(lambda: servicer.opened)
        self.assertEqual("hello", servicer.events[0].WhichOneof("event"))
        self.assertEqual("python", servicer.events[0].hello.sdk_language)
        self.assertEqual(
            [
                bootstrap_pb2.PROTOCOL_HTTP,
                bootstrap_pb2.PROTOCOL_GRPC,
                bootstrap_pb2.PROTOCOL_DUBBO,
                bootstrap_pb2.PROTOCOL_THRIFT,
            ],
            list(servicer.events[0].hello.supported_protocols),
        )
        self.assertEqual("127.0.0.1:21001", session.endpoint("HTTP"))
        self.assertEqual("127.0.0.1:21002", session.endpoint(ListenerProtocol.GRPC))
        self.assertEqual(1, session.listener_snapshot().generation)

    def test_stream_disconnect_invalidates_then_reconnects(self):
        first_server, _ = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21101,
                    ListenerProtocol.GRPC: 21102,
                    ListenerProtocol.DUBBO: 21103,
                    ListenerProtocol.THRIFT: 21104,
                }
            )
        )
        session = self.new_session().start()
        self.assertEqual("127.0.0.1:21101", session.endpoint("http"))

        first_server.stop(0).wait()
        wait_until(lambda: not session.is_available())
        with self.assertRaises(SidecarUnavailableError):
            session.endpoint("http")

        _, _ = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21201,
                    ListenerProtocol.GRPC: 21202,
                    ListenerProtocol.DUBBO: 21203,
                    ListenerProtocol.THRIFT: 21204,
                }
            )
        )
        wait_until(lambda: session.is_available())
        self.assertEqual("127.0.0.1:21201", session.endpoint("http"))
        self.assertEqual(2, session.listener_snapshot().generation)

    def test_local_service_registration_status_replay_and_unregistration(self):
        def first_status(event):
            if event.WhichOneof("event") == "register_local_service":
                return bootstrap_pb2.SidecarEvent(
                    local_service_status=bootstrap_pb2.LocalServiceStatus(
                        registration_id=event.register_local_service.registration_id,
                        state=bootstrap_pb2.LOCAL_SERVICE_STATE_REGISTERED,
                        message="registered",
                    )
                )
            return None

        first_server, first_servicer = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21501,
                    ListenerProtocol.GRPC: 21502,
                    ListenerProtocol.DUBBO: 21503,
                    ListenerProtocol.THRIFT: 21504,
                }
            ),
            first_status,
        )
        session = self.new_session().start()
        registration_id = session.register_local_service(
            namespace="default",
            service="catalog",
            protocol=ListenerProtocol.GRPC,
            local_port=50051,
        )
        wait_until(
            lambda: session.local_service_status(registration_id)
            and session.local_service_status(registration_id).state
            == LocalServiceState.REGISTERED
        )
        self.assertEqual("hello", first_servicer.events[0].WhichOneof("event"))
        self.assertEqual(
            registration_id,
            first_servicer.events[1].register_local_service.registration_id,
        )

        first_server.stop(0).wait()
        wait_until(lambda: not session.is_available())

        def replayed_status(event):
            if event.WhichOneof("event") == "register_local_service":
                return bootstrap_pb2.SidecarEvent(
                    local_service_status=bootstrap_pb2.LocalServiceStatus(
                        registration_id=event.register_local_service.registration_id,
                        state=bootstrap_pb2.LOCAL_SERVICE_STATE_REGISTERED,
                        message="replayed",
                    )
                )
            if event.WhichOneof("event") == "unregister_local_service":
                return bootstrap_pb2.SidecarEvent(
                    local_service_status=bootstrap_pb2.LocalServiceStatus(
                        registration_id=event.unregister_local_service.registration_id,
                        state=bootstrap_pb2.LOCAL_SERVICE_STATE_UNREGISTERED,
                        message="unregistered",
                    )
                )
            return None

        _, replayed_servicer = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21601,
                    ListenerProtocol.GRPC: 21602,
                    ListenerProtocol.DUBBO: 21603,
                    ListenerProtocol.THRIFT: 21604,
                }
            ),
            replayed_status,
        )
        wait_until(
            lambda: session.local_service_status(registration_id)
            and session.local_service_status(registration_id).message == "replayed"
        )
        self.assertEqual("hello", replayed_servicer.events[0].WhichOneof("event"))
        self.assertEqual(
            registration_id,
            replayed_servicer.events[1].register_local_service.registration_id,
        )
        self.assertTrue(session.unregister_local_service(registration_id))
        wait_until(
            lambda: session.local_service_status(registration_id)
            and session.local_service_status(registration_id).state
            == LocalServiceState.UNREGISTERED
        )
        self.assertFalse(session.unregister_local_service(registration_id))

    def test_close_ends_control_session_request_stream(self):
        _, servicer = self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21701,
                    ListenerProtocol.GRPC: 21702,
                    ListenerProtocol.DUBBO: 21703,
                    ListenerProtocol.THRIFT: 21704,
                }
            )
        )
        session = self.new_session().start()
        session.close()
        wait_until(lambda: servicer.stream_closed)

    def test_duplicate_or_incomplete_snapshot_fails_initialization(self):
        self.start_server(
            listener_snapshot(
                {
                    ListenerProtocol.HTTP: 21301,
                    ListenerProtocol.GRPC: 21302,
                    ListenerProtocol.DUBBO: 21303,
                    ListenerProtocol.THRIFT: 21304,
                }
            )
        )
        duplicate_event = bootstrap_pb2.SidecarEvent(
            listener_snapshot=bootstrap_pb2.ListenerSnapshot(
                listeners=[
                    bootstrap_pb2.Listener(
                        protocol=bootstrap_pb2.PROTOCOL_HTTP, port=21301
                    ),
                    bootstrap_pb2.Listener(
                        protocol=bootstrap_pb2.PROTOCOL_HTTP, port=21302
                    ),
                ]
            )
        )
        self.servers[0].stop(0).wait()
        self.servers.pop()
        self.start_server(duplicate_event)

        session = SidecarSession(
            socket_path=self.socket_path,
            initialization_timeout_seconds=0.1,
            reconnect_initial_backoff_seconds=0.01,
            reconnect_max_backoff_seconds=0.02,
            connection_timeout_seconds=0.05,
        )
        self.sessions.append(session)
        with self.assertRaises(SidecarInitializationError):
            session.start()

    def test_first_event_must_contain_a_listener_snapshot(self):
        with self.assertRaises(InvalidListenerSnapshotError):
            SidecarSession._parse_initial_event(bootstrap_pb2.SidecarEvent())

    def test_snapshot_rejects_unknown_protocol_and_invalid_port(self):
        with self.assertRaises(InvalidListenerSnapshotError):
            SidecarSession._parse_initial_event(
                listener_snapshot(
                    {
                        ListenerProtocol.HTTP: 21401,
                        ListenerProtocol.GRPC: 21402,
                        ListenerProtocol.DUBBO: 21403,
                        ListenerProtocol.THRIFT: 0,
                    }
                )
            )
        with self.assertRaises(InvalidListenerSnapshotError):
            SidecarSession._parse_initial_event(
                bootstrap_pb2.SidecarEvent(
                    listener_snapshot=bootstrap_pb2.ListenerSnapshot(
                        listeners=[
                            bootstrap_pb2.Listener(protocol=99, port=21401),
                        ]
                    )
                )
            )

    def test_unavailable_socket_times_out_initialization(self):
        session = SidecarSession(
            socket_path=self.socket_path,
            initialization_timeout_seconds=0.05,
            reconnect_initial_backoff_seconds=0.01,
            reconnect_max_backoff_seconds=0.02,
            connection_timeout_seconds=0.1,
        )
        self.sessions.append(session)
        with self.assertRaises(SidecarInitializationError):
            session.start()
        self.assertFalse(session.is_available())


class SocketConfigurationTest(unittest.TestCase):
    def test_default_and_environment_override(self):
        self.assertEqual(DEFAULT_SIDECAR_SOCKET, resolve_sidecar_socket())
        with patch.dict("os.environ", {"POLE_SIDECAR_SOCKET": "/tmp/pole.sock"}):
            self.assertEqual("/tmp/pole.sock", resolve_sidecar_socket())


if __name__ == "__main__":
    unittest.main()
