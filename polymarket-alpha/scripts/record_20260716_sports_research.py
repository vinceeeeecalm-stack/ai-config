#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime as dt, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'experiments/current-sports-event-research.json'
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def row(slug, event_id, game_id, title, market_id, direction, evidence, sources, missing, factor, failures, trigger):
    return {
        'event_slug': slug, 'event_id': event_id, 'game_id': game_id, 'title': title,
        'target_market_id': market_id, 'target_side': 'YES', 'direction': direction,
        'rules_clear': True,
        'settlement_rules': 'Polymarket exact 90分钟常规时间加补时；不含加时和点球。',
        'research_status': 'insufficient', 'research_probability': None,
        'confidence_low': None, 'confidence_high': None, 'calibrated_oos_samples': 0,
        'maximum_acceptable_price': None, 'alpha_maximum_acceptable_price': None,
        'method': '市场仅作发现和盘口基准；以官方赛事身份/赛程、至少两项独立资料、近期样本、阵容和比赛状态交叉核验。当前缺少可复核完整近5-10场、机会质量和确认首发包，因此不编造独立概率。',
        'key_evidence': evidence, 'first_goal_profile': factor,
        'trailing_response': '若目标方先失球，90分钟胜率和盘中价格路径均需重新估计；低事件平局是主要陷阱。',
        'lead_protection': '领先后是否保护90分钟结果取决于晋级总比分、替补和场面控制；不能把晋级优势当作90分钟胜率。',
        'failure_paths': failures, 'conditional_trigger': trigger,
        'live_playbook': {'required':'仅在比分、分钟、盘口时间戳均不超过120秒时研究盘中方向；同时核对射正、禁区触球、定位球和转换质量。','invalidate':'实时数据过期、红牌、关键伤退、首发重大变化或盘口断层。'},
        'exit_plan': {'paper':'当前不建立paper仓；若后续证据补齐且净优势达门槛，最多0.10u观察仓，首球后用可执行bid分批退出，催化剂仓北京时间23:50前清仓。'},
        'research_missing_fields': missing,
        'next_review_at': '2026-07-16T21:00:00+08:00',
        'strength_classification': '事件身份、合同方向与公开资料已核验，但证据不足，不能升级为研究概率或入场授权',
        'sources': sources,
    }

def main():
    p = json.loads(PATH.read_text())
    by = {x.get('event_slug'): x for x in p.get('research_items', [])}
    missing = ['双方最近5-10场及对手强度','xG/射门质量序列','确认首发、伤停、停赛','比赛日天气/场地','临近CLOB深度、费用、滑点复核','独立P_win与可信区间']
    rows = [
      row('nwsl-got-spi-2026-07-15','658829','90106298','NJ/NY Gotham FC vs. Washington Spirit','2768763','HOME',
        ['NWSL/Washington Spirit official match page confirms the Citi Field fixture and 8:00pm kickoff; it reports the game in progress.','SportyTrader live page displayed Gotham 1-0 Spirit at 43\' with a Lavelle goal, but the page timestamp/quote age cannot be verified to the required 120-second window.','The Polymarket target is the exact Gotham 90-minute win; scan snapshot captured ask about 75c, bid 72c and 3c spread. The quote is a benchmark only and live execution freshness is not proven.'],
        [{'kind':'official','title':'NWSL/Washington Spirit official match page','url':'https://washingtonspirit.com/match/07-15-26/'},{'kind':'official','title':'NWSL official news / schedule','url':'https://www.nwslsoccer.com/news/nwsl-action-returns-with-wave-tea-party-sophia-wilson-s-50th-goal-and-trinity-rodman-late-game-winner'},{'kind':'independent','title':'SportyTrader live score and team form','url':'https://www.sportytrader.com/en/results-live/sky-blue-washington-spirit-8240994/'},{'kind':'independent','title':'Sofascore match page','url':'https://www.sofascore.com/football/match/washington-spirit-njny-gotham-fc/tUnsmvkb'}],['verified_score_minute_quote_bundle_within_120_seconds','full_last_5_to_10_opponent_adjusted_sample','verified_xG_shot_quality_live_sequence','confirmed_lineup_injury_status','fresh_CLOB_depth_fees_slippage','independent_P_win_and_confidence_interval'],'Gotham home control and the early goal are the main path, but the live score/clock and quote are not jointly timestamped within 120 seconds; this forces M3 and no live direction.',['Spirit equalizer or comeback','stale/conflicting live state','red card/key injury','low shot quality after the lead','3c spread and slippage consume edge'],'Only reconsider after a fresh score/minute/quote bundle within 120 seconds and repeated box-entry/shot-quality confirmation; otherwise no entry.'),
      row('col-ast1-din3-2026-07-16','659693','90112916','Astana FK vs. FC Dinamo City','2772322','HOME',
        ['Polymarket合同为Astana 90分钟胜，当前YES ask约69c、bid约67c、spread约2c；市场共识约68%。','beIN确认16 July赛事身份；Soccerzz提供赛前阵容/预测页，但尚未形成确认XI。','UEFA Conference League资格赛语境意味着晋级目标与90分钟胜不是同一合同；首回合/总比分和轮换需临场复核。'],
        [{'kind':'official','title':'UEFA Conference League official competition/match centre','url':'https://www.uefa.com/uefaconferenceleague/'},{'kind':'official','title':'FC Astana official site','url':'https://fcastana.kz/'},{'kind':'independent','title':'beIN Sports match preview','url':'https://www.beinsports.com/en-us/soccer/uefa-conference-league/astana-vs-dinamo-city-2026-07-16'},{'kind':'independent','title':'Soccerzz team news and lineups','url':'https://www.soccerzz.com/news/astana-vs-dinamo-city-team-news-lineups-predictions-europa-conference-league-qual-16-07/1157029'}],missing,'主队主场控制、首球和定位球质量；2c spread已经显著压缩可执行优势。',['低比分平局','客队反击先入球','首发/轮换不确定','红牌或点球','晋级结果与90分钟结果偏离'],'补齐官方确认XI、总比分/首回合、近况和临场CLOB；ask≤研究上限且摩擦后净优势≥5c才考虑。'),
      row('col-eli-ala-2026-07-16','659691','90112922','Elimai FK vs. Alashkert FA','2772316','HOME',
        ['Polymarket合同为Elimai 90分钟胜，当前YES ask约58c、bid约57c、spread约1c；市场共识约58%。','The Stats Zone确认16 July Conference League资格赛身份并给出赛前背景；Sky Sports/FotMob确认赛事、时间和队伍。','公开资料仍未提供双方可复核完整近5-10场、对手强度、确认首发与机会质量序列；不把外部预测当独立概率。'],
        [{'kind':'official','title':'UEFA Conference League official competition/match centre','url':'https://www.uefa.com/uefaconferenceleague/'},{'kind':'official','title':'FC Yelimay official/club information','url':'https://www.instagram.com/fcyelimay/'},{'kind':'independent','title':'The Stats Zone preview','url':'https://www.thestatszone.com/yelimay-vs-alashkert-preview-prediction-2026-27-uefa-conference-league-first-qualifying-round-206293'},{'kind':'independent','title':'Sky Sports match page','url':'https://www.skysports.com/football/yelimay-vs-alashkert-fc/558219'},{'kind':'independent','title':'FotMob match page','url':'https://www.fotmob.com/matches/fc-yelimay-vs-alashkert-fc/1ntynm3e'}],missing,'主队主场压迫与客队转换；1c spread可交易但证据不足，不能将58c市场价当研究P_win。',['低比分平局','Alashkert先入球','主队关键攻击手缺阵','首发/赛程疲劳','晋级与90分钟结果偏离'],'补齐官方XI、首回合/晋级矩阵、近况与射门质量；仅在净优势达门槛时重评。'),
      row('col-lev-crf-2026-07-16','659711','90112910','FCI Levadia vs. Caernarfon Town FC','2772382','HOME',
        ['官方Levadia赛前页面确认16 July主场次回合；俱乐部报道首回合客场5-0胜。','Sports Mole给出次回合预览与可能阵容；beIN独立确认赛事和日期。','5-0总比分领先提高晋级概率，但可能降低90分钟争胜动机；当前YES ask约80c、bid约79c，约1c spread，晋级不能直接转译为90分钟P_win。'],
        [{'kind':'official','title':'FCI Levadia official match announcement','url':'https://fcilevadia.ee/en/fci-levadia-will-face-welsh-club-caernarfon-town-in-the-first-qualifying-round-of-the-uefa-conference-league/'},{'kind':'official','title':'UEFA Conference League official competition/match centre','url':'https://www.uefa.com/uefaconferenceleague/'},{'kind':'independent','title':'Sports Mole preview and team news','url':'https://www.sportsmole.co.uk/football/caernarfon-town/europa-conference-league/preview/levadia-vs-caernarfon-prediction-team-news-lineups_601186.html'},{'kind':'independent','title':'beIN Sports match preview','url':'https://www.beinsports.com/en-mena/football/uefa-conference-league/fci-levadia-vs-caernarfon-town-2026-07-16'}],missing,'总比分领先后的动机/轮换是最大决定因素；目标方即使明显更强，也未必需要90分钟取胜。',['主队轮换降速','低事件平局','客队先入球','红牌/伤停','晋级与90分钟结果偏离'],'只有官方XI确认且盘口价格明显低于保守研究上限才重评；当前不授权80c附近买入。'),
      row('uel-ves-qar-2026-07-16','663330','90112870','ÍF Vestri vs. Qarabağ Ağdam FK','2786494','AWAY',
        ['Polymarket合同为Qarabağ 90分钟胜，当前YES ask约88c、bid约87c、spread约1c；市场共识约89%。','UEFA官方页面提供Vestri vs Qarabağ squad lists；Sports Mole指出Vestri首次参加欧战、Qarabağ具备更高层级经验与阵容优势。','IFFHS提供独立赛事记录；但公开资料尚未给出完整双方近5-10场、确认XI、天气和可复核xG序列。'],
        [{'kind':'official','title':'UEFA Vestri vs Qarabağ squad lists','url':'https://www.uefa.com/uefaeuropaleague/match/2048656--vestri-vs-qarabag/lineups/'},{'kind':'official','title':'Qarabağ official club site','url':'https://qarabagh.com/'},{'kind':'independent','title':'Sports Mole preview and team news','url':'https://www.sportsmole.co.uk/football/vestri/europa-league/preview/vestri-vs-qarabag-prediction-team-news-lineups_601212.html'},{'kind':'independent','title':'IFFHS match record','url':'https://iffhs.com/en/match/2171'}],missing,'实力差距和Qarabağ控球/压迫是主路径，但88c高价几乎已反映热门优势；低比分、轮换和客场节奏是价格风险。',['Vestri先入球','0-0/1-1低事件','Qarabağ轮换或疲劳','红牌/点球','市场冲击与滑点吞噬优势'],'必须补齐官方XI和临场状态；即使方向强，ask需显著低于保守上限且摩擦后净优势达5c才可考虑。')
    ]
    for x in rows: by[x['event_slug']] = x
    p.update({'schema_version':'polymarket-sports-event-research-v1','research_run_id':'pm-sports-research-'+NOW.replace(':','').replace('-',''),'created_at':NOW,'report_date_beijing':'2026-07-16','research_items':list(by.values()),'paper_only':True,'live_orders_enabled':False,'private_api_used':False,'real_money_execution_authorized':False})
    PATH.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'ok','updated':[x['event_slug'] for x in rows],'created_at':NOW},ensure_ascii=False))
if __name__ == '__main__': main()
