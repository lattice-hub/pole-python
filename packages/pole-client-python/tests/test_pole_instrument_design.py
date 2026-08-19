from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DESIGN_PATH = REPOSITORY_ROOT / "context-kg" / "technical" / "pole-instrument.md"


class PoleInstrumentTechnicalDesignTest(unittest.TestCase):
    @classmethod
    def design(cls) -> str:
        return DESIGN_PATH.read_text(encoding="utf-8")

    def test_defines_product_configuration_and_activation_lifecycle(self):
        design = self.design()
        for decision in (
            "pole-instrument-python",
            "pole_instrument",
            "pole-instrument",
            "POLE_INSTRUMENT_ENABLED",
            "POLE_INSTRUMENT_STRICT",
            "disabled by default",
            "idempotent",
            "unpatch",
            "after framework import",
        ):
            with self.subTest(decision=decision):
                self.assertIn(decision, design)

    def test_defines_outbound_routing_invalidation_and_failure_policy(self):
        design = self.design()
        for decision in (
            "SidecarSession.endpoint(ListenerProtocol.HTTP)",
            "TargetService.to_metadata()",
            "current_traffic_context()",
            "ListenerSnapshot.generation",
            "SidecarUnavailableError",
            "fail open",
            "strict mode",
            "must not guess",
        ):
            with self.subTest(decision=decision):
                self.assertIn(decision, design)

    def test_defines_coexistence_process_diagnostics_security_and_traceability(self):
        design = self.design()
        for decision in (
            "OpenTelemetry",
            "os.register_at_fork",
            "event loop",
            "activation_status()",
            "rate limit",
            "request bodies",
            "Python 3.9",
            "Traceability",
            "AC15",
        ):
            with self.subTest(decision=decision):
                self.assertIn(decision, design)


if __name__ == "__main__":
    unittest.main()
