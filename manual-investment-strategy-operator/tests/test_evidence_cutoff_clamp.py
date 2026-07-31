import sys
import unittest
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from generate_manual_report import clamp_evidence_as_of  # noqa: E402


class EvidenceCutoffClampTests(unittest.TestCase):
    def test_same_second_future_microseconds_are_clamped(self) -> None:
        cutoff = "2026-07-29T16:16:39+00:00"
        source = "2026-07-29T16:16:39.509907+00:00"
        self.assertEqual(clamp_evidence_as_of(source, cutoff), cutoff)

    def test_closed_source_time_keeps_original_precision(self) -> None:
        cutoff = "2026-07-29T16:16:40+00:00"
        source = "2026-07-29T16:16:39.509907+00:00"
        self.assertEqual(clamp_evidence_as_of(source, cutoff), source)

    def test_invalid_source_time_degrades_to_cutoff(self) -> None:
        cutoff = "2026-07-29T16:16:40+00:00"
        self.assertEqual(clamp_evidence_as_of("not-a-time", cutoff), cutoff)


if __name__ == "__main__":
    unittest.main()
