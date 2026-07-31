import importlib.util, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m

models=load("stage2_models","scripts/polymarket_stage2_model_audit.py")
readiness=load("stage2_readiness","scripts/polymarket_stage2_readiness.py")

class Stage2Tests(unittest.TestCase):
    def test_exactly_two_frozen_models(self):
        p=models.build();self.assertEqual(p["formal_market_type_count"],2);self.assertTrue(p["stage2_finite_research_complete"]);self.assertFalse(p["third_market_type_researched"])
        self.assertEqual(set(p["frozen_failed_models"]),{"pm-baseball-elo-v1","pm-nba-elo-v1"})
    def test_holdouts_closed(self):
        for row in models.build()["models"]:
            self.assertFalse(row["final_holdout_reuse_for_model_selection_allowed"]);self.assertGreaterEqual(row["independent_final_oos_events"],30)
    def test_readiness_distinguishes_engineering_and_edge(self):
        p=readiness.build();self.assertTrue(p["states"]["engineering_ready"]);self.assertTrue(p["states"]["strategy_edge_unproven"]);self.assertFalse(p["long_term_metrics_are_stage2_blockers"])
    def test_safety(self):
        p=readiness.build();self.assertTrue(p["paper_only"]);self.assertFalse(p["live_orders_enabled"]);self.assertFalse(p["private_api_used"]);self.assertFalse(p["real_money_execution_authorized"])

if __name__=="__main__":unittest.main()
