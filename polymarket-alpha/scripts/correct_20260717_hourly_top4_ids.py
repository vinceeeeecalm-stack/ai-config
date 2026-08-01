import json
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "experiments/current-sports-event-research.json"
d = json.loads(p.read_text(encoding="utf-8"))
fixes = {
    "chi-bgu-tie-2026-07-17": ("663698", "90108716", "2787539"),
    "nor-bog-ffk-2026-07-17": ("663524", "90107535", "2787029"),
    "bra-bah-cha-2026-07-17": ("666990", "90104629", "2798178"),
}
for row in d["research_items"]:
    if row.get("event_slug") in fixes:
        row["event_id"], row["game_id"], row["target_market_id"] = fixes[row["event_slug"]]
d["research_run_id"] = "pm-sports-research-20260717T0644Z"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status":"ok","corrected":list(fixes)}, ensure_ascii=False))
