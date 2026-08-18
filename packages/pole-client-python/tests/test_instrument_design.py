from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DESIGN = REPOSITORY_ROOT / "context-kg" / "technical" / "adr" / "pole-instrument.md"


class PoleInstrumentDesignTest(unittest.TestCase):
    def test_design_freezes_the_public_runtime_contract(self):
        document = DESIGN.read_text(encoding="utf-8")

        for required_text in (
            "status: proposed",
            "distribution: `pole-instrument`",
            "import package: `pole_instrument`",
            "console script: `pole-instrument`",
            "pole-instrument [--diagnostic-level LEVEL] -- COMMAND [ARG ...]",
            "os.execvpe",
            "exactly once",
            "fail-open",
            "SidecarSession",
            "TargetService.to_metadata()",
            "TargetService.to_grpc_metadata()",
            "current_traffic_context()",
            "snapshot generation",
            "process-idempotent",
            "after_in_child",
            "authorization values, baggage values, or payloads",
            "| Adapter | Supported versions | Status |",
            "| None | — | Not yet supported |",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, document)

        for forbidden_startup_hook in ("`sitecustomize.py`", "`usercustomize.py`", "`.pth`"):
            self.assertIn(forbidden_startup_hook, document)
        self.assertIn("does not depend on `pole-instrument`", document)


if __name__ == "__main__":
    unittest.main()
