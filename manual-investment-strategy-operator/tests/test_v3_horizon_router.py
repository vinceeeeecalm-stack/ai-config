import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v3_horizon_router import ResearchMode, route_request, split_request  # noqa: E402


class HorizonRouterV2Test(unittest.TestCase):
    def route(self, query):
        return route_request(
            request_id="request-1",
            query=query,
            requested_at="2026-07-25T12:00:00+08:00",
        )

    def test_all_five_modes_route_deterministically(self):
        cases = {
            "长期 DCA 定投分析": ResearchMode.LONGTERM_DCA,
            "给我一周短线计划": ResearchMode.TACTICAL_1_7D,
            "分析下一次财报交易": ResearchMode.EVENT_TRADE_1_3W,
            "这个现有仓位继续持有吗": ResearchMode.EXISTING_POSITION_REVIEW,
            "生成 08:30 晨报": ResearchMode.DAILY_DUAL_WINDOW,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self.route(query).mode, expected)

    def test_ambiguous_horizons_cannot_share_one_request_spec(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_split_request_required"):
            self.route("同时分析长期 DCA 和一周短线")

    def test_multi_horizon_request_splits_to_unique_ids(self):
        specs = split_request(
            request_id="request-mixed",
            query="长期 DCA 和一周短线",
            requested_at="2026-07-25T12:00:00+08:00",
        )
        self.assertEqual({item.mode for item in specs}, {
            ResearchMode.LONGTERM_DCA,
            ResearchMode.TACTICAL_1_7D,
        })
        self.assertEqual(len({item.request_id for item in specs}), 2)

    def test_unspecified_horizon_requires_explicit_mode(self):
        with self.assertRaisesRegex(ValueError, "explicit_mode_required"):
            self.route("帮我看看 SOL")
        spec = route_request(
            request_id="request-explicit",
            query="帮我看看 SOL",
            requested_at="2026-07-25T12:00:00+08:00",
            explicit_mode="existing_position_review",
        )
        self.assertEqual(spec.mode, ResearchMode.EXISTING_POSITION_REVIEW)

    def test_request_timestamp_must_be_explicit_iso_with_timezone(self):
        with self.assertRaisesRegex(ValueError, "timezone_required"):
            route_request(
                request_id="request-time",
                query="长期 DCA",
                requested_at="2026-07-25T12:00:00",
            )


if __name__ == "__main__":
    unittest.main()
