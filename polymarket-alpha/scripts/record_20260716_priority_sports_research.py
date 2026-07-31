#!/usr/bin/env python3
import datetime as dt, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'experiments/current-sports-event-research.json'
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
MISSING = ['双方最近5-10场及对手强度','xG/射门质量或机会质量序列','确认首发、伤停、停赛','比赛日天气/场地','临场CLOB深度、费用、滑点复核','独立P_win与可信区间']

def make(slug, event_id, game_id, title, market_id, direction, ask, bid, official, indep, evidence, factor, failures, trigger):
    return {
      'event_slug':slug,'event_id':event_id,'game_id':game_id,'title':title,
      'target_market_id':market_id,'target_side':'YES','direction':direction,
      'rules_clear':True,
      'settlement_rules':'Polymarket精确90分钟常规时间加补时；不含加时和点球。次回合晋级目标与90分钟胜合同分离。',
      'research_status':'insufficient','research_probability':None,'confidence_low':None,'confidence_high':None,
      'calibrated_oos_samples':0,'maximum_acceptable_price':None,'alpha_maximum_acceptable_price':None,
      'method':'市场仅作发现、隐含概率和执行基准；独立估计需要官方赛事资料、至少两个独立来源、近况/对手强度、阵容和机会质量交叉核验。证据未齐时不输出概率。',
      'key_evidence':evidence + [f'本轮Polymarket目标YES ask约{ask:.2f}、best bid约{bid:.2f}、spread约{ask-bid:.2f}；市场隐含概率仅作基准，不是独立研究概率。'],
      'first_goal_profile':factor,
      'trailing_response':'目标方先失球会显著压缩90分钟胜路径；低事件0-0/1-1与客队反击是主要平局/失利分支。',
      'lead_protection':'总比分领先方可能降低90分钟争胜强度；必须核对首发、替补和场面控制，不能把晋级概率转译为90分钟P_win。',
      'failure_paths':failures,'conditional_trigger':trigger,
      'live_playbook':{'required':'只有比分、分钟和Polymarket盘口时间戳均不超过120秒，且射正、禁区触球、定位球和转换质量可核对时，才允许盘中方向。','invalidate':'实时数据过期、红牌、关键伤退、首发重大变化或盘口断层。'},
      'exit_plan':{'paper':'当前不建立paper仓；若后续证据补齐且摩擦后净优势达到门槛，最多0.10u观察仓，首球后按可执行bid分批退出；催化剂仓北京时间23:50前清仓。'},
      'research_missing_fields':MISSING,
      'next_review_at':'2026-07-16T22:30:00+08:00',
      'strength_classification':'强热门方向性候选，公开资料支持赛事身份和首回合背景，但研究证据不足，不能入场授权。',
      'sources':[official] + indep,
      'source_freshness':'本轮公开检索时间约2026-07-16 21:xx Asia/Shanghai；CLOB价格来自本轮扫描快照，需临场重新核验。',
      'monitoring_level':'M3','monitoring_activation':'required_for_live_entry','monitoring_owner':None,
      'paper_only':True,'live_orders_enabled':False,'private_api_used':False,'real_money_execution_authorized':False
    }

def main():
    p=json.loads(PATH.read_text())
    by={x.get('event_slug'):x for x in p.get('research_items',[])}
    rows=[
      make('col-shk-eur-2026-07-16','659732','90112912','KF Shkëndija 79 vs. Europa FC','2772477','HOME',.81,.80,
        {'kind':'official','title':'UEFA Conference League qualifying fixtures/results','url':'https://www.uefa.com/uefaconferenceleague/news/02a6-20e5e911587f-1000--conference-league-qualifying-fixtures-results-dates-how-it-works/'},
        [{'kind':'independent','title':'The Stats Zone preview','url':'https://www.thestatszone.com/shkendija-vs-europa-preview-prediction-2026-27-uefa-conference-league-first-qualifying-round-206302'},{'kind':'independent','title':'Forebet preview','url':'https://www.forebet.com/en/football-match-previews/28900-shkendenija-79-in-command-but-warned-by-europa-fcs-sturdy-away-habits'}],
        ['UEFA列出次回合及首回合Europa 0-5 Shkëndija；官方俱乐部/赛事页确认合同赛事身份。','独立预览对Shkëndija主胜方向存在分歧，且总比分5-0使90分钟动机、轮换与平局质量敏感。'],
        '总比分5-0领先后的90分钟动机、轮换和客队早期反扑强度。',['主队轮换降速导致平局','Europa先入球','低比分0-0/1-1','红牌/关键伤停','晋级与90分钟合同偏离'],'补齐官方XI、伤停和近况/机会质量后重评；ask需低于保守上限且摩擦后净优势约5c。'),
      make('col-pyu-mxl-2026-07-16','659696','90112901','Pyunik FA vs. Marsaxlokk FC','2772328','HOME',.82,.81,
        {'kind':'official','title':'UEFA Conference League qualifying fixtures/results','url':'https://www.uefa.com/uefaconferenceleague/news/02a6-20e5e911587f-1000--conference-league-qualifying-fixtures-results-dates-how-it-works/'},
        [{'kind':'independent','title':'Forebet preview','url':'https://www.forebet.com/en/football-match-previews/28887-pyunik-in-command-but-warned-marsaxlokk-seek-a-response-after-0-3-first-leg-setback'},{'kind':'independent','title':'OddsCalendar match preview','url':'https://www.oddscalendar.com/football/europe/conference-league/pyunik-yerevan-vs-marsaxlokk/1554431'}],
        ['UEFA列出首回合Marsaxlokk 0-3 Pyunik及次回合赛程。','独立预览一致认为Pyunik明显占优，但尚无可复核完整近况、首发和机会质量包；3-0总比分会降低主队90分钟强攻必要性。'],
        '3-0总比分领先后的控球降速与轮换，及Marsaxlokk必须提高风险的反击路径。',['低事件平局','客队先入球','Pyunik轮换/攻击端缺席','红牌/点球','晋级与90分钟合同偏离'],'补齐官方XI、伤停和近况/机会质量后重评；82c附近不授权买入。'),
      make('uel-ftc-vns-2026-07-16','659584','90112868','Ferencvárosi TC vs. FK Vojvodina Novi Sad','2771681','HOME',.70,.69,
        {'kind':'official','title':'UEFA Europa League qualifying fixtures/results','url':'https://es.uefa.com/uefaeuropaleague/news/02a6-20e5e12a3527-27e4d60ddacb-1000--fase-de-clasificacion-de-la-europa-league-partidos-fecha/'},
        [{'kind':'independent','title':'Sports Mole preview, team news and lineups','url':'https://www.sportsmole.co.uk/football/ferencvaros/europa-league/preview/ferencvaros-vs-fk-vojvodina-prediction-team-news-lineups_601204.html'},{'kind':'independent','title':'Sky Sports form and head-to-head','url':'https://www.skysports.com/football/ferencvaros-vs-fk-vojvodina/557657'}],
        ['UEFA列出次回合Ferencváros主场对Vojvodina；独立资料确认首回合Ferencváros 2-1领先、晋级对手为Twente。','Sports Mole称Ferencváros有一球优势且近期不败，但客队仍有客场进球/追分路径；市场70c ask未形成5c保守优势。'],
        'Ferencváros在一球领先下的90分钟风险管理、Vojvodina追分时的转换与定位球。',['平局陷阱','Vojvodina先入球','主队领先后收缩导致90分钟不胜','关键前锋/中卫缺阵','晋级与90分钟合同偏离'],'即使首发确认，也需独立概率显著高于70c且扣摩擦后达到门槛；否则WAIT。'),
      make('col-rfs-glt-2026-07-16','659710','90112902','FK Rīgas Futbola Skola vs. Glentoran FC','2772365','HOME',.71,.70,
        {'kind':'official','title':'UEFA Conference League qualifying fixtures/results','url':'https://www.uefa.com/uefaconferenceleague/news/02a6-20e5e911587f-1000--conference-league-qualifying-fixtures-results-dates-how-it-works/'},
        [{'kind':'independent','title':'Sports Mole preview, team news and lineups','url':'https://www.sportsmole.co.uk/football/rigas-futbola-skola/europa-conference-league/preview/rfs-vs-glentoran-prediction-team-news-lineups_601222.html'},{'kind':'independent','title':'beIN Sports match preview','url':'https://www.beinsports.com/en-us/soccer/uefa-conference-league/rfs-vs-glentoran-2026-07-16'}],
        ['UEFA列出首回合Glentoran 1-2 RFS及次回合赛程。','Sports Mole确认RFS一球领先、Glentoran客场追分且RFS有Diomande早伤背景；独立资料还不足以给出完整近况/机会质量/确认XI概率。'],
        'RFS能否在Glentoran高风险追分时保持防转换与两条进攻路线；Diomande伤情是关键不确定性。',['平局陷阱','Glentoran先入球','RFS攻击端伤停/轮换','客队转换制造高质量机会','晋级与90分钟合同偏离'],'补齐官方XI、伤停和近况/机会质量后重评；71c附近不授权买入。')]
    for r in rows: by[r['event_slug']]=r
    p.update({'schema_version':'polymarket-sports-event-research-v1','research_run_id':'pm-sports-research-'+NOW.replace(':','').replace('-',''),'created_at':NOW,'report_date_beijing':'2026-07-16','research_items':list(by.values()),'paper_only':True,'live_orders_enabled':False,'private_api_used':False,'real_money_execution_authorized':False})
    PATH.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'ok','updated':[r['event_slug'] for r in rows],'research_items':len(p['research_items'])},ensure_ascii=False))
if __name__=='__main__': main()
