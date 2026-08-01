#!/usr/bin/env python3
"""Record the event-level public research decisions for the 2026-07-15 cycle."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def row(slug, event_id, game_id, title, market_id, direction, evidence, sources, missing, method, first_goal, trailing, lead, failure, trigger, exit_plan):
    return {
        "event_slug": slug, "event_id": event_id, "game_id": game_id, "title": title,
        "target_market_id": market_id, "target_side": "YES", "direction": direction,
        "rules_clear": True, "settlement_rules": "Polymarket exact 90分钟常规时间加补时；不含加时和点球；合同方向已按 target_market_id 核验。", "research_status": "insufficient",
        "research_probability": None, "confidence_low": None, "confidence_high": None,
        "calibrated_oos_samples": 0, "maximum_acceptable_price": None,
        "alpha_maximum_acceptable_price": None, "method": method, "key_evidence": evidence,
        "first_goal_profile": first_goal, "trailing_response": trailing, "lead_protection": lead,
        "failure_paths": failure, "conditional_trigger": trigger,
        "live_playbook": {"required": "仅在比分、比赛分钟、盘口时间戳均不超过120秒时研究盘中方向；同时核对射正、禁区触球、定位球和转换质量。", "invalidate": "实时数据过期、红牌、关键伤退、首发重大变化或盘口断层。"},
        "exit_plan": {"paper": exit_plan}, "research_missing_fields": missing,
        "next_review_at": "2026-07-15T20:30:00+08:00",
        "strength_classification": "事件身份与市场方向已核验，但证据不足，不能升级为研究概率或入场授权",
        "sources": sources,
    }


def main():
    payload = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    by_slug = {str(x.get("event_slug")): x for x in payload.get("research_items", [])}
    common_missing = ["双方最近5-10场及对手强度的可复核完整序列", "射门/xG质量或网球发接发序列", "确认首发、伤停、停赛", "比赛日天气/场地", "临近开赛CLOB深度、费用与滑点复核", "独立概率与可信区间"]
    rows = [
        row("auc-mcr-bjf-2026-07-15", "660057", "90117979", "Marlin Coast Rangers FC vs. Brunswick Juventus FC", "2773794", "AWAY",
            ["Football Australia confirms the Australia Cup Round of 32 fixture at Barlow Park Stadium, 7:30pm AEST.", "The Polymarket contract is Brunswick Juventus to win in 90 minutes plus stoppage time; it is not a qualification contract.", "Public preview anchors conflict materially and do not provide a reproducible opponent-adjusted last-10/xG sample; the current executable ask is about 77c with a wide spread."],
            [{"kind":"official","title":"Football Australia Round of 32 schedule","url":"https://footballaustralia.com.au/news/hahn-australia-cup-2026-round-32-match-schedule-finalised"},{"kind":"official","title":"Marlin Coast Rangers FC official site","url":"https://marlincoastrangersfc.com.au/"},{"kind":"independent","title":"Forebet match preview","url":"https://www.forebet.com/en/football/matches/marlin-coast-rangers-brunswick-juventus-2470114"},{"kind":"independent","title":"Tips.GG match prediction","url":"https://tips.gg/matches/football/15-07-2026/marlin-coast-rangers-vs-brunswick-juventus/10-30/predictions/"}], common_missing,
            "市场仅用于发现与盘口基准；公开资料确认比赛，但无法在冲突预估、阵容和可比样本不完整时形成独立概率。", "先入球会显著改变客胜路径，但双方首球与射门质量未量化。", "主队先入球、平局或红牌会破坏客胜；不能把晋级叙事替代90分钟结算。", "客队领先可保护，但旅行、轮换和末段控制未验证。", ["低比分平局", "Marlin 先入球", "Brunswick 轮换/疲劳", "红牌", "费用、spread和滑点吞噬优势"], "补齐最终首发、天气、完整近况/xG并重新抓取双边CLOB后再评估；净优势未达门槛不入场。", "不建立paper仓；若后续证据充分，仅允许极小试探，首球后可分批退出，禁止把90分钟方向当晋级持有。"),
        row("auc-fb-nse-2026-07-15", "660058", "90117980", "FK Beograd vs. North Sunshine Eagles FC", "2773795", "HOME",
            ["Football Australia confirms the Australia Cup Round of 32 fixture at Frank Mitchell Park, 8:00pm AEST.", "The Polymarket contract is FK Beograd to win in 90 minutes plus stoppage time.", "A public preview gives a home-favouring model anchor, while the event page confirms identity; however, confirmed XI, injuries, weather and a comparable two-team recent-form/xG package are not available."],
            [{"kind":"official","title":"Football Australia Round of 32 schedule","url":"https://footballaustralia.com.au/news/hahn-australia-cup-2026-round-32-match-schedule-finalised"},{"kind":"official","title":"FK Beograd official site","url":"https://www.fkbeograd.com.au/"},{"kind":"independent","title":"FotMob match page","url":"https://www.fotmob.com/en-GB/matches/fk-beograd-vs-north-sunshine-eagles-sc/406ip8hx"},{"kind":"independent","title":"Forebet match preview","url":"https://www.forebet.com/en/football/matches/fk-beograd-north-sunshine-eagles-2470115"}], common_missing,
            "市场仅用于发现与盘口基准；单一外部模型不能替代可复核的对手强度、阵容与机会质量研究。", "主场压迫可能提高先入球概率，但没有可靠射门/禁区触球序列。", "North Sunshine 先入球、低比分平局和主队关键攻击手缺阵均使主胜失效。", "主队领先后的保护路径、替补质量和杯赛末段风险未验证。", ["平局陷阱", "客队反击", "早红牌", "主队攻击线缺席", "90分钟与晋级结果偏离"], "补齐首发/伤停、天气、可比近况和新盘口后才允许小额paper复核；当前不入场。", "不建立仓；未来若通过门槛，仅极小paper探针，首球后按盘口/比赛状态分批退出。"),
        row("col-mal-vll-2026-07-15", "656048", "90112921", "FC Malisheva vs. KF Vllaznia Shkodër", "2760154", "HOME",
            ["UEFA confirms this is the Conference League first qualifying-round second leg; the first leg was reported as Vllaznia 2-1 Malisheva.", "Forebet reports first-leg shot context (Vllaznia 17 shots/8 on target; Malisheva 53% possession and 10 corners), while independent preview coverage reports Vllaznia's 2-1 aggregate lead and recent home-form context.", "The Polymarket contract is Malisheva to win in 90 minutes, not to advance; the current ask is about 53c and the draw/qualification paths remain material."],
            [{"kind":"official","title":"UEFA Conference League qualifying schedule","url":"https://www.uefa.com/uefaconferenceleague/news/02a6-20e5e911587f-cc10425958b3-1000--conference-league-qualifying-fixtures-results-dates-how-it-/"},{"kind":"official","title":"UEFA first qualifying round draw","url":"https://www.uefa.com/uefaconferenceleague/news/02a6-20df98709704-bdef1bc28610-1000--uefa-conference-league-first-qualifying-round-draw/"},{"kind":"independent","title":"Forebet second-leg preview","url":"https://www.forebet.com/en/football-match-previews/28843-malisheva-seek-a-turnaround-as-vllaznia-lean-on-european-know-how"},{"kind":"independent","title":"FreeTips preview","url":"https://www.freetips.com/football/uefa-europa-conference-league/malisheva-vs-vllaznia-preview-20260713-0038/"}], common_missing,
            "市场仅用于发现与盘口基准；首回合统计和晋级情境已核对，但最终首发、完整双方样本、天气及独立概率尚未可复核。", "Malisheva 需要追分，可能主动制造先入球和高节奏；但开放比赛也放大 Vllaznia 转换机会。", "Vllaznia 先入球或把比赛拖入低事件节奏会压低主胜；不能以主队晋级需求直接等同主胜。", "主队若领先可能收缩保护，但落后/追分状态会提高失误与平局风险。", ["Vllaznia 先入球", "1-1或低比分", "红牌/点球", "首发轮换", "90分钟主胜与晋级结果偏离"], "临场确认首发、天气、比分与CLOB年龄≤120秒，并补齐最近5-10场和机会质量后再评估；当前不入场。", "不建立仓；若触发研究门槛，优先首球后分批退出，不把晋级需求变成终场持仓理由。"),
        row("ucl-ucr-mlv-2026-07-15", "655878", "90112941", "Universitatea Craiova CS vs. FK ML Viciebsk", "2759524", "HOME",
            ["UEFA confirms the Champions League first qualifying-round fixture; independent match reporting records Craiova winning the first leg 4-1.", "Sports Mole and The Stats Zone describe Craiova as Romanian champions and ML Vitebsk as Belarusian champions/debutant European opposition; Sky Sports exposes the 4-1 first-leg result.", "The Polymarket contract is Craiova to win in 90 minutes plus stoppage time, not to qualify; the current executable ask is about 77c, so the remaining draw/low-event mass matters."],
            [{"kind":"official","title":"UEFA ML Vitebsk fixtures","url":"https://www.uefa.com/uefachampionsleague/clubs/2613945--vitebsk/matches/"},{"kind":"official","title":"UEFA Universitatea Craiova fixtures","url":"https://it.uefa.com/uefachampionsleague/clubs/2606501--u-craiova/matches/"},{"kind":"independent","title":"Sports Mole preview","url":"https://www.sportsmole.co.uk/football/vitebsk/champions-league/preview/vitebsk-vs-universitatea-craiova-prediction-team-news-lineups_600787.html"},{"kind":"independent","title":"Sky Sports match page","url":"https://www.skysports.com/football/cs-universitatea-craiova-vs-ml-vitebsk/557654"}], common_missing,
            "市场仅用于发现与盘口基准；首回合大比分与赛事层级可核对，但无法由此推出第二回合90分钟独立概率，尤其缺最终首发和完整机会质量样本。", "主队可能凭控球/压迫制造先入球，但大比分领先后降速会增加0-0/1-1或平局质量。", "ML Vitebsk 先入球、主队轮换或比赛降速会显著损害主胜；晋级优势不能替代90分钟结算。", "Craiova 若领先可能优先保护总比分/健康，导致终场主胜不如市场直觉稳定。", ["低事件平局", "客队先入球", "主队轮换/疲劳", "红牌", "晋级与90分钟结果偏离"], "临场确认首发、天气、最新盘口并补齐双方可比近况/xG；在77c附近未见足够摩擦后优势，当前不入场。", "不建立仓；若独立证据最终收敛，也只允许极小paper，首球/比赛状态改善后分批退出，避免隔夜。"),
        row("ucl-egn-pet-2026-07-15", "659493", "90112942", "KF Egnatia Rrogozhinë vs. FC Petrocub Hînceşti", "2771225", "HOME",
            ["FMF reports the first leg ended 1-1 and confirms the return leg is scheduled at Arena Egnatia in Rrogozhine on 15 July.", "Sports Mole describes the tie as level and frames the objective as reaching the second Champions League qualifying round; its preview is dated 13 July and is not a confirmed matchday XI.", "UEFA's public match page provides the event/line-up contract identity, while the current Polymarket ask is about 54c with a 1c spread; the market price is only a benchmark."],
            [{"kind":"official","title":"UEFA Egnatia vs Petrocub line-ups / match page","url":"https://es.uefa.com/uefachampionsleague/match/2048644--egnatia-vs-petrocub/lineups/"},{"kind":"official","title":"Moldovan Football Federation first-leg report","url":"https://www.fmf.md/noutate/18021/liga-campionilor-petrocub--egnatia-11"},{"kind":"independent","title":"Sports Mole preview and team news","url":"https://www.sportsmole.co.uk/football/egnatia-rrogozhine/champions-league/preview/egnatia-rrogozhine-vs-petrocub-prediction-team-news-lineups_601169.html"},{"kind":"independent","title":"DFB Datencenter fixture record","url":"https://datencenter.dfb.de/datencenter/qualifikation-zur-champions-league/2026-27/1-runde/kf-egnatia-fc-petrocub-hince-ti-2420499"}], common_missing,
            "市场仅用于发现与盘口基准；首回合1-1和主场回合已确认，但双方最近5-10场、机会质量、最终首发、天气和独立概率尚未形成可复核完整包。", "Egnatia主场主动权和首回合平局可能促使其先压迫，但Petrocub若先入球会把主队拉入更高风险追分。", "Petrocub先入球、0-0/1-1低事件路径、或主队无法把控球转成禁区高质量机会，均会压低90分钟主胜。", "平局足以把晋级问题推向加时/点球，不能把主场或晋级动机直接等同90分钟胜利。", ["低比分平局", "客队先入球", "双方谨慎开局", "红牌/点球", "90分钟与晋级结果偏离"], "补齐双方近况/对手强度、射门/xG、确认首发和临近CLOB后再评估；54c附近不授权入场。", "不建立paper仓；若盘中比分、分钟、盘口均在120秒内且主队持续制造高质量机会，最多0.10u观察仓，首球后以可执行bid分批退出。"),
    ]
    for x in rows: by_slug[x["event_slug"]] = x
    payload.update({"schema_version":"polymarket-sports-event-research-v1", "research_run_id":f"pm-sports-research-{NOW.replace(':','').replace('-','')}", "created_at":NOW, "report_date_beijing":"2026-07-15", "research_items":list(by_slug.values()), "paper_only":True, "live_orders_enabled":False, "private_api_used":False, "real_money_execution_authorized":False})
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"ok","updated":[x["event_slug"] for x in rows],"research_items":len(by_slug),"created_at":NOW},ensure_ascii=False))


if __name__ == "__main__": main()
