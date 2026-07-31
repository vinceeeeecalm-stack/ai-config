#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "experiments/current-sports-event-research.json"
payload = json.loads(path.read_text(encoding="utf-8"))
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

items = [
    {
        "event_slug": "chi-hen-hai-2026-07-17", "event_id": "663699", "game_id": "90108717",
        "title": "Henan FC vs. Qingdao Hainiu FC", "target_market_id": "2787544", "target_market_slug": "chi-hen-hai-2026-07-17-hen",
        "target_side": "YES", "direction": "HOME", "rules_clear": True,
        "settlement_rules": "目标是河南 FC 90分钟含伤停补时获胜；不含加时、点球或晋级。Polymarket 赛事规则字段已捕获，当前没有实时比赛状态。",
        "research_status": "insufficient", "research_probability": None, "confidence_low": None, "confidence_high": None,
        "calibrated_oos_samples": 0, "maximum_acceptable_price": None, "alpha_maximum_acceptable_price": None,
        "method": "以Polymarket CLOB ask/bid作市场基准，不将其当独立概率；尝试用官方联赛/足协信息、独立赛前资料、近期赛果与阵容资料建立90分钟场景树，但官方逐场资料、确认首发、对手强度调整后的5-10场机会质量样本未在本轮同时可核验。",
        "key_evidence": ["CLOB快照：YES best bid 79c、ask 80c、含模拟摩擦fill 80.04c、spread 1c；市场基准约79.5%，不是研究概率。", "独立预览称河南此前遭遇三连败，降低了把79.5%热门基准直接上调的理由；未核验完整赛季和对手强度调整数据。", "低总进球/主队进攻连续性与青岛反击会把概率质量转移到平局和客胜路径；首球后领先保护、落后追分和定位球路径未完成量化。"],
        "historical_base_rate": "未完成：缺少同类中超主队高热门、对手强度调整和90分钟结算样本。", "historical_base_rate_samples": 0,
        "first_goal_profile": "河南若先入球可通过主场控球和领先保护提高胜率；青岛先入球或0-0半场会显著放大平局路径。未量化。",
        "trailing_response": "河南落后时需要增加压迫，可能同时暴露转换防守；青岛落后后的反击/定位球效率未核实。",
        "lead_protection": "未核验近期领先后失球率和替补深度，不得把市场热门度当领先保护证据。",
        "failure_paths": ["低事件平局", "河南近期状态延续并先失球", "青岛反击或定位球先得分", "首发/伤停临场变化", "盘口或规则字段与赛事状态冲突"],
        "conditional_trigger": "仅在官方首发、天气、近10场含对手强度、可复核射门/xG资料和最新CLOB同时完成后重研；若ask仍高于研究上限则PASS。",
        "live_playbook": {"monitoring_level": "M3", "activation": "unavailable", "owner": None, "checkpoints": ["10'", "25'", "半场", "60'", "进球/红牌/核心伤退"], "stale_timeout_seconds": 120, "invalidate": "比分、分钟或CLOB任一超过120秒，或首发/重大事件未核实。"},
        "exit_plan": {"paper": "无paper仓位；没有有效入场，不记录fill。", "catalyst": "不启用；研究不足。"},
        "research_missing_fields": ["official_fixture_or_lineup_confirmation", "confirmed_starting_XI", "complete_last_5_to_10_with_opponent_strength", "verified_xG_or_shot_quality", "injuries_suspensions_weather", "independent_probability", "P_price_up"],
        "strength_classification": "第一候选／强热门候选／研究完成但证据不足，不是买入建议",
        "next_review_at": "2026-07-17T18:45:00+08:00",
        "sources": [
            {"kind": "official", "title": "中国足协官网纪律与联赛信息入口（本轮未找到逐场首发页）", "url": "https://www.thecfa.cn/"},
            {"kind": "independent", "title": "7M Sport Henan vs Qingdao preview", "url": "https://news.7msport.com/news/newsdata/20260716/257923.shtml"},
            {"kind": "independent", "title": "FotMob Qingdao Hainiu team fixture/form page", "url": "https://www.fotmob.com/en-GB/teams/4183/overview/qingdao-hainiu"}
        ]
    },
    {
        "event_slug": "chi-bgu-tie-2026-07-17", "event_id": "663698", "game_id": "90108716",
        "title": "Beijing Guoan FC vs. Liaoning Tieren FC", "target_market_id": "2787539", "target_market_slug": "chi-bgu-tie-2026-07-17-bgu",
        "target_side": "YES", "direction": "HOME", "rules_clear": True,
        "settlement_rules": "目标是北京国安90分钟含伤停补时获胜；不含加时、点球或晋级。",
        "research_status": "insufficient", "research_probability": None, "confidence_low": None, "confidence_high": None,
        "calibrated_oos_samples": 0, "maximum_acceptable_price": None, "alpha_maximum_acceptable_price": None,
        "method": "市场仅作基准；尝试结合官方联赛入口、Sky Sports/Transfermarkt/FotMob/SoccerStats等独立资料，但本轮没有完成官方首发、完整5-10场对手强度调整、伤停和射门质量交叉核验，因此不发布研究概率。",
        "key_evidence": ["CLOB快照：YES best bid 69c、ask 70c、含模拟摩擦fill 70.03c、spread 1c；市场基准约69.2%。", "Sky Sports、Transfermarkt和FotMob均能确认赛事身份/时间；这些页面不能替代完整独立模型概率。", "北京国安的主场控制与辽宁铁人的转换/低位防守是主要风格变量；平局和主队无法持续制造高质量机会是失败路径。"],
        "historical_base_rate": "未完成：缺少同类中超主场热门90分钟胜率及对手强度调整样本。", "historical_base_rate_samples": 0,
        "first_goal_profile": "北京先入球会提高控场和领先保护路径；辽宁先入球会迫使国安提速并放大反击与平局尾部。未量化。",
        "trailing_response": "国安落后后可能增加边路/定位球投入，但若辽宁低位防守有效，0-0/1-1质量仍高。",
        "lead_protection": "未核验国安近期领先保护、门将/中卫组合和替补连续性。",
        "failure_paths": ["0-0或1-1平局陷阱", "辽宁先入球后低位守转反", "国安首发攻击点缺席", "定位球/红牌造成非重复性波动", "临场盘口或阵容信息过期"],
        "conditional_trigger": "补齐官方首发、近10场对手强度、射门质量/xG、伤停天气和最新CLOB后重研；未达净优势约5c不入场。",
        "live_playbook": {"monitoring_level": "M2", "activation": "unavailable", "owner": None, "checkpoints": ["10'", "25'", "半场", "60'", "每次进球/红牌/核心伤退"], "stale_timeout_seconds": 120, "invalidate": "连续两个检查点国安无法进入禁区或被反复打穿转换；任一实时源超过120秒。"},
        "exit_plan": {"paper": "无paper仓位；本轮WAIT/PASS。", "catalyst": "只有未来完成研究且实时确认后才可考虑首球后分批退出。"},
        "research_missing_fields": ["official_fixture_or_lineup_confirmation", "confirmed_starting_XI", "complete_last_5_to_10_with_opponent_strength", "verified_xG_or_shot_quality", "injuries_suspensions_weather", "independent_probability", "P_price_up"],
        "strength_classification": "第二候选／重点候选／研究完成但证据不足，不是买入建议",
        "next_review_at": "2026-07-17T18:45:00+08:00",
        "sources": [
            {"kind": "official", "title": "中国足协官网入口（本轮未找到逐场官方首发页）", "url": "https://www.thecfa.cn/"},
            {"kind": "independent", "title": "Sky Sports match page", "url": "https://www.skysports.com/football/beijing-guoan-vs-liaoning-tieren/554304"},
            {"kind": "independent", "title": "Transfermarkt match report page", "url": "https://www.transfermarkt.de/beijing-guoan_liaoning-tieren/index/spielbericht/4827848"}
        ]
    },
    {
        "event_slug": "nor-bog-ffk-2026-07-17", "event_id": "663524", "game_id": "90107535",
        "title": "FK Bodø/Glimt vs. Fredrikstad FK", "target_market_id": "2787029", "target_market_slug": "nor-bog-ffk-2026-07-17-bog",
        "target_side": "YES", "direction": "HOME", "rules_clear": True,
        "settlement_rules": "目标是Bodø/Glimt 90分钟含伤停补时获胜；不含加时、点球或晋级。",
        "research_status": "insufficient", "research_probability": None, "confidence_low": None, "confidence_high": None,
        "calibrated_oos_samples": 0, "maximum_acceptable_price": None, "alpha_maximum_acceptable_price": None,
        "method": "市场基准约85.8%、含摩擦ask 85.04c；官方NFF/Fredrikstad赛程与Glimt近期官方报道确认赛事，但未完成赛前确认阵容、世界杯归队/缺席影响和完整对手强度样本，故不把高热门转成买入。",
        "key_evidence": ["CLOB快照：YES bid 84c、ask 85c、含模拟摩擦fill 85.04c、spread 1c。", "NFF和Fredrikstad官方赛程确认17日19:15、Aspmyra；Glimt官方报道显示复赛击败KFUM且创造多次机会。", "独立预览报告Berg、Bjorkan、Hauge因国家队任务缺席的可能性；这会影响中场控制/边路创造，不能用85.8%热门基准覆盖。"],
        "historical_base_rate": "未完成：缺少同类Eliteserien强热门、阵容缺口及对手强度调整OOS样本。", "historical_base_rate_samples": 0,
        "first_goal_profile": "Glimt先入球可利用主场压迫和多点进攻；Fredrikstad先入球或Glimt攻击核心缺席会放大平局/反击路径。",
        "trailing_response": "Glimt落后时具备提速与边路压迫路径，但若中场关键人缺席，压迫质量和回防会下修。",
        "lead_protection": "Glimt主场领先保护需要确认中场和中卫配置；不可因强热门直接视为稳健。",
        "failure_paths": ["世界杯归队/缺席导致阵容断层", "Fredrikstad先入球后低位反击", "Glimt控球但机会质量不足", "低事件平局", "临场首发或盘口超过新鲜度上限"],
        "conditional_trigger": "确认首发并完成近10场/射门质量与缺席影响后重研；即使方向成立，85c附近仍需保守净优势门。",
        "live_playbook": {"monitoring_level": "M2", "activation": "unavailable", "owner": None, "checkpoints": ["10'", "25'", "半场", "60'", "每次进球/红牌/核心伤退"], "stale_timeout_seconds": 120, "invalidate": "Glimt两次连续检查点无高质量禁区机会，或被Fredrikstad反复打出转换。"},
        "exit_plan": {"paper": "无paper仓位；强热门仅研究排队。", "catalyst": "未达到研究门，不设虚假目标/止损。"},
        "research_missing_fields": ["confirmed_starting_XI", "World_Cup_return_and_absence_confirmation", "complete_last_5_to_10_with_opponent_strength", "verified_xG_or_shot_quality", "weather_and_pitch", "independent_probability", "P_price_up"],
        "strength_classification": "强热门候选／研究不足，非买入建议",
        "next_review_at": "2026-07-17T23:45:00+08:00",
        "sources": [
            {"kind": "official", "title": "Norges Fotballforbund official fixture", "url": "https://www.fotball.no/fotballdata/Lag/Hjem/?fiksId=4"},
            {"kind": "official", "title": "Fredrikstad FK official schedule", "url": "https://www.fredrikstadfk.no/terminliste"},
            {"kind": "official", "title": "Bodø/Glimt official KFUM match report", "url": "https://www.glimt.no/nyheter/glimt-tilbake-med-seier-over-kfum"},
            {"kind": "independent", "title": "Sports Mole preview/team news", "url": "https://www.sportsmole.co.uk/football/bodo-glimt/preview/bodoglimt-vs-fredrikstad-prediction-team-news-lineups_601253.html"},
            {"kind": "independent", "title": "FootballAnt predicted lineups", "url": "https://www.footballant.com/match-news/matches/bodo-glimt-vs-fredrikstad-lineup-2026/"}
        ]
    }
]

existing = {str(row.get("event_slug")): row for row in payload.get("research_items") or []}
for item in items:
    item["research_snapshot_at"] = now
    existing[item["event_slug"]] = item
payload["schema_version"] = "polymarket-sports-event-research-v1"
payload["research_run_id"] = "pm-sports-research-20260717T" + now[11:19].replace(":", "") + "Z"
payload["created_at"] = now
payload["report_date_beijing"] = "2026-07-17"
payload["research_items"] = list(existing.values())
payload["paper_only"] = True
payload["live_orders_enabled"] = False
payload["private_api_used"] = False
payload["real_money_execution_authorized"] = False
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"updated": [x["event_slug"] for x in items], "total_items": len(payload["research_items"]), "snapshot": now}, ensure_ascii=False))
