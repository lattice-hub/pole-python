import hashlib
import json
from pathlib import Path
import unittest
from dataclasses import FrozenInstanceError
from typing import Dict, Mapping, Optional

from pole_client import (
    DEFAULT_SIDECAR_ENDPOINT,
    ENVELOPE_VERSION,
    TargetEnvelope,
    TargetEnvelopeError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "contract"
CONFORMANCE = json.loads(
    (CONTRACT_ROOT / "conformance.json").read_text(encoding="utf-8")
)


def create_envelope(values: Mapping[str, str]) -> TargetEnvelope:
    return TargetEnvelope(
        namespace=values["namespace"],
        service=values["service"],
        protocol=values.get("protocol"),
        group=values.get("group"),
        service_version=values.get("service_version"),
        method=values.get("method"),
        original_endpoint=values.get("original_endpoint"),
    )


def normalized_shape(envelope: TargetEnvelope) -> Dict[str, str]:
    result = {
        "namespace": envelope.namespace,
        "service": envelope.service,
    }
    optional_fields = (
        "protocol",
        "group",
        "service_version",
        "method",
        "original_endpoint",
    )
    for field_name in optional_fields:
        value: Optional[str] = getattr(envelope, field_name)
        if value is not None:
            result[field_name] = value
    return result


class ContractAssetTest(unittest.TestCase):
    def test_contract_identity_and_version(self) -> None:
        self.assertEqual("pole-target-envelope", CONFORMANCE["contract"])
        self.assertEqual("1.0.0", CONFORMANCE["contract_version"])
        self.assertEqual(CONFORMANCE["envelope_version"], ENVELOPE_VERSION)
        self.assertEqual(
            "http://127.0.0.1:15001",
            DEFAULT_SIDECAR_ENDPOINT,
        )

    def test_vendored_assets_and_checksums(self) -> None:
        checksum_lines = (
            (CONTRACT_ROOT / "SHA256SUMS")
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()
        )
        self.assertEqual(
            ["schema.json", "conformance.json"],
            [line.split()[1] for line in checksum_lines],
        )
        for line in checksum_lines:
            expected, file_name = line.split()
            actual = hashlib.sha256(
                (CONTRACT_ROOT / file_name).read_bytes()
            ).hexdigest()
            self.assertEqual(expected, actual)

        version = (CONTRACT_ROOT / "VERSION").read_text(encoding="utf-8")
        self.assertIn("contract=pole-target-envelope\n", version)
        self.assertIn("version=1.0.0\n", version)
        self.assertIn("tag=thin-sdk-contract-v1.0.0\n", version)
        self.assertIn(
            "commit=f45b0396b4680fe588a93086ceb2934d3e157d04\n",
            version,
        )

    def test_sidecar_receive_vectors_are_structurally_complete(self) -> None:
        receive_vectors = CONFORMANCE["sidecar_receive"]
        self.assertTrue(receive_vectors["valid"])
        self.assertTrue(receive_vectors["invalid"])

        for vector in receive_vectors["valid"]:
            self.assertTrue(vector["name"])
            self.assertGreaterEqual(len(vector["headers"]), 3)
            self.assertIsInstance(vector["expected_envelope"]["namespace"], str)
            self.assertIsInstance(vector["expected_envelope"]["service"], str)
        for vector in receive_vectors["invalid"]:
            self.assertTrue(vector["name"])
            self.assertTrue(vector["headers"])
            self.assertTrue(vector["diagnostic"])


class TargetEnvelopeConformanceTest(unittest.TestCase):
    def test_all_sdk_valid_vectors(self) -> None:
        for vector in CONFORMANCE["valid"]:
            with self.subTest(vector=vector["name"]):
                envelope = create_envelope(vector["input"])
                self.assertEqual(vector["normalized"], normalized_shape(envelope))
                base_headers = dict(vector.get("base_headers", ()))
                self.assertEqual(
                    [tuple(pair) for pair in vector["expected_headers"]],
                    list(envelope.to_headers(base_headers).items()),
                )
                with self.assertRaises(FrozenInstanceError):
                    envelope.service = "payments"  # type: ignore[misc]

    def test_all_sdk_invalid_vectors(self) -> None:
        for vector in CONFORMANCE["invalid"]:
            with self.subTest(vector=vector["name"]):
                with self.assertRaises(TargetEnvelopeError) as caught:
                    create_envelope(vector["input"])
                self.assertEqual(vector["diagnostic"], caught.exception.diagnostic)

    def test_all_language_specific_invalid_vectors(self) -> None:
        for vector in CONFORMANCE["language_specific_invalid"]:
            with self.subTest(vector=vector["name"]):
                invalid_value = "".join(
                    chr(int(code_unit, 16))
                    for code_unit in vector["utf16_code_units"]
                )
                values = {
                    "namespace": "default",
                    "service": "orders",
                    vector["field"]: invalid_value,
                }
                with self.assertRaises(TargetEnvelopeError) as caught:
                    create_envelope(values)
                self.assertEqual(vector["diagnostic"], caught.exception.diagnostic)

    def test_encoding_revalidates_constructed_instance(self) -> None:
        envelope = TargetEnvelope(namespace="default", service="orders")
        object.__setattr__(envelope, "original_endpoint", "orders.internal:080")

        with self.assertRaises(TargetEnvelopeError) as caught:
            envelope.to_headers()
        self.assertEqual(
            "INVALID_ORIGINAL_ENDPOINT",
            caught.exception.diagnostic,
        )


if __name__ == "__main__":
    unittest.main()
