import hashlib
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
    ListenerProtocol,
    SidecarInitializationError,
    SidecarSession,
    SidecarUnavailableError,
    TargetService,
    TargetServiceError,
    resolve_sidecar_socket,
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
    def __init__(self, event):
        self.event = event
        self.opened = False
        self.request = None

    def OpenSession(self, request, context):
        self.opened = True
        self.request = request
        yield self.event
        while context.is_active():
            time.sleep(0.01)


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

        checksums = (CONTRACT_ROOT / "SHA256SUMS").read_text(encoding="utf-8")
        for line in checksums.strip().splitlines():
            expected, file_name = line.split()
            actual = hashlib.sha256((CONTRACT_ROOT / file_name).read_bytes()).hexdigest()
            self.assertEqual(expected, actual)

    def test_vendored_proto_defines_open_session(self):
        proto = (CONTRACT_ROOT / "bootstrap.proto").read_text(encoding="utf-8")
        self.assertIn("rpc OpenSession(ClientHello) returns (stream SidecarEvent);", proto)
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

    def start_server(self, event):
        servicer = BootstrapServicer(event)
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

    def test_open_session_installs_all_protocol_endpoints(self):
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
        self.assertEqual("python", servicer.request.sdk_language)
        self.assertEqual(
            [
                bootstrap_pb2.PROTOCOL_HTTP,
                bootstrap_pb2.PROTOCOL_GRPC,
                bootstrap_pb2.PROTOCOL_DUBBO,
                bootstrap_pb2.PROTOCOL_THRIFT,
            ],
            list(servicer.request.supported_protocols),
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
