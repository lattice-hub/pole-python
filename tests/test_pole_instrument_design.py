import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "context-kg" / "technical" / "adr" / "pole-instrument.md"


class PoleInstrumentDesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DESIGN.read_text(encoding="utf-8")

    def assert_has(self, *phrases):
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)

    def test_ac01_design_is_authoritative_and_discoverable(self):
        self.assert_has("# pole-instrument Technical Design", "Status: Proposed", "Scope")
        self.assertIn("[[pole-instrument]]", (ROOT / "context-kg" / "_meta" / "index.md").read_text())
        self.assertIn("pole-instrument", (ROOT / "README.md").read_text())

    def test_ac02_packaging_boundary(self):
        self.assert_has("pole-instrument", "pole_instrument", "pole-client-python", "pole_client")

    def test_ac03_runtime_contract_and_adapter_scope(self):
        self.assert_has("Activation contract", "Argument forwarding", "Exit codes", "Adapter interface", "No concrete framework adapter")

    def test_ac04_existing_sdk_integration(self):
        self.assert_has("SidecarSession", "TargetService", "TrafficContext", "Adapter boundary")

    def test_ac05_failure_policy(self):
        self.assert_has("Failure policy", "Launcher setup", "Sidecar unavailable", "Invalid listener snapshot", "Adapter import or patch", "Runtime callback", "stale endpoint")

    def test_ac06_patch_lifecycle(self):
        self.assert_has("Patch lifecycle", "idempotent", "partial patch", "Import order", "unpatch")

    def test_ac07_process_models(self):
        self.assert_has("Process model", "subprocess", "exec", "pre-fork", "post-fork", "fresh SidecarSession")

    def test_ac08_configuration(self):
        self.assert_has("Configuration", "CLI > environment > default", "POLE_SIDECAR_SOCKET", "validation", "Secrets")

    def test_ac09_diagnostics(self):
        self.assert_has("Diagnostics", "stderr", "severity", "diagnostic ID", "redact", "baggage")

    def test_ac10_compatibility_and_artifacts(self):
        self.assert_has("Python >=3.9", "Optional dependencies", "sitecustomize.py", "usercustomize.py", ".pth")

    def test_ac11_verification_matrix(self):
        self.assert_has("Verification matrix", "Launcher", "Sidecar disconnect", "Idempotency", "Fork", "Package isolation")

    def test_ac12_non_goals_and_open_questions(self):
        self.assert_has("Non-goals", "Open questions", "framework-specific", "automatic sitecustomize activation", "changes to pole_client")


if __name__ == "__main__":
    unittest.main()
