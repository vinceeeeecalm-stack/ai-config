#!/usr/bin/env python3
import datetime as dt, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'experiments/current-sports-event-research.json'
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def main():
    payload = json.loads(PATH.read_text(encoding='utf-8'))
    by_slug = {str(x.get('event_slug')): x for x in payload.get('research_items', [])}
    row = {
        'event_slug':'col-ast1-din3-2026-07-16', 'event_id':'659693', 'game_id':'90112916',
        'title':'Astana FK vs. FC Dinamo City', 'target_market_id':'2772322', 'target_side':'YES',
        'direction':'HOME', 'rules_clear':True,
        'settlement_rules':'Polymarket resolves Astana\'s exact 90-minute win plus stoppage time, not team to advance, extra time or penalties.',
        'research_status':'insufficient', 'research_probability':None, 'confidence_low':None,
        'confidence_high':None, 'calibrated_oos_samples':0, 'maximum_acceptable_price':None,
        'method':'Market used only for discovery. UEFA confirms the Conference League first qualifying-round fixture and independent previews report Astana carrying a narrow 1-0 first-leg lead; confirmed XI, opponent-adjusted last-5-to-10 sample, verified xG/shot-quality sequence, weather and a reconciled independent probability are unavailable at this run.',
        'key_evidence':[
            'UEFA club fixtures identify Astana vs Dinamo City on 16 July 2026 in the Conference League qualifying schedule.',
            'Sports Mole/Stats Zone/Forebet-style public previews describe the first-leg 1-0 Astana lead and the return-leg incentive, but do not establish a complete event model or confirmed starting XI.',
            'The Polymarket target is the exact Astana 90-minute win; the scan captured approximately 69c ask, 67c bid and 2c spread. Market probability remains a benchmark only.'
        ],
        'first_goal_profile':'Astana can use home territory and the aggregate lead to control the match, but a first goal by Dinamo City would force the home side into a higher-risk chase; first-goal and shot-quality rates are not independently quantified.',
        'trailing_response':'Dinamo City scoring first, a low-event draw, or Astana protecting the aggregate lead all damage the 90-minute home-win route even if Astana remains more likely to advance.',
        'lead_protection':'Astana may reduce tempo after leading on aggregate; late control, substitutions and repeatable chance creation are unverified.',
        'failure_paths':['0-0/1-1 low-event draw','Dinamo City scores first','Astana rotation or fatigue','red card/penalty','90-minute result diverges from qualification outcome','2c spread and fees consume any theoretical edge'],
        'conditional_trigger':'Do not enter. Recheck confirmed lineups, official match state, weather and fresh executable CLOB; only reconsider if a reconciled independent estimate clears the friction-adjusted 5c gate.',
        'live_playbook':{'required':'Only inspect live if score, minute and CLOB quote are each no older than 120 seconds; monitor box entries, shot quality, set pieces, field tilt and transition concessions.','invalidate':'Stale/conflicting data, red card, key injury, major XI change or aggregate-protection state without sustained home chance quality.'},
        'exit_plan':{'paper':'No paper position. If a later fully evidenced catalyst entry is opened, cap at 0.10u, use executable bid for any trim, recover stake after the first verified repricing, and hard-exit before 23:50 Beijing.'},
        'research_missing_fields':['confirmed_starting_XI','complete_last_5_to_10_both_teams','opponent_strength_adjustment','verified_xG_or_shot_quality_sequence','injuries_and_suspensions','weather_at_venue','independent_probability','fresh_CLOB_depth_and_slippage'],
        'next_review_at':'2026-07-16T12:00:00+08:00',
        'strength_classification':'市场强热门/方向性候选，但研究证据不足；不能升级为研究概率或入场授权',
        'sources':[
            {'kind':'official','title':'UEFA Astana fixtures 2026/27','url':'https://www.uefa.com/uefaconferenceleague/clubs/2600605--astana/matches/'},
            {'kind':'independent','title':'The Stats Zone: Astana vs Dinamo City preview','url':'https://www.thestatszone.com/astana-vs-dinamo-city-preview-prediction-2026-27-uefa-conference-league-first-qualifying-round-206295'},
            {'kind':'independent','title':'SportyTrader: Astana vs Dinamo City preview','url':'https://www.sportytrader.com/en/betting-tips/fc-astana-dinamo-tirana-359001/'},
            {'kind':'independent','title':'Ratingbet: Astana vs Dinamo City team news/lineup preview','url':'https://ratingbet.com/predictions/astana-vs-dinamo-city-prediction-expert-analysis-possible-lineups-july-16-2026/'},
        ]
    }
    by_slug[row['event_slug']] = row
    levadia = {
        'event_slug':'col-lev-crf-2026-07-16', 'event_id':'659711', 'game_id':'90112910',
        'title':'FCI Levadia vs. Caernarfon Town FC', 'target_market_id':'2772382', 'target_side':'YES',
        'direction':'HOME', 'rules_clear':True,
        'settlement_rules':'Polymarket resolves Levadia exact 90-minute win plus stoppage time, not team to advance, extra time or penalties.',
        'research_status':'insufficient', 'research_probability':None, 'confidence_low':None,
        'confidence_high':None, 'calibrated_oos_samples':0, 'maximum_acceptable_price':None,
        'method':'Market used only for discovery. UEFA and FCI Levadia official pages confirm the two-leg Conference League tie, the 16 July Tallinn return leg and a 5-0 Levadia first-leg win; independent previews support the class gap and European context. A complete opponent-adjusted last-5-to-10 opportunity-quality sample, confirmed XI/injuries, matchday weather and reconciled independent probability are not available at this run.',
        'key_evidence':[
            'FCI Levadia official fixture page confirms the return leg at A. Le Coq Arena on 16 July and records Caernarfon Town FC 0-5 FCI Levadia on 9 July.',
            'UEFA schedule and Sports Mole/Stats Zone previews confirm the Conference League first qualifying-round context; Levadia are the more experienced European side, but preview evidence is not a complete model.',
            'The Polymarket target is the exact Levadia 90-minute win; the scan captured about 80c ask, 79c bid and 1c spread. Market probability remains a benchmark only.'
        ],
        'first_goal_profile':'Levadia home territory and the 5-0 aggregate lead can produce control, but the same lead may lower urgency; first-20-minute pressure and shot quality are unverified.',
        'trailing_response':'Caernarfon scoring first would be a large but not complete shock; a 0-0/1-0 low-event path can still lose the 90-minute home-win contract while Levadia advances.',
        'lead_protection':'Levadia only needs to protect a five-goal aggregate lead; rotation and reduced tempo make advancement probability materially different from the 90-minute home-win probability.',
        'failure_paths':['0-0/1-0 low-event draw or loss','Levadia rotation/low urgency','Caernarfon scores first','red card/penalty','90-minute result diverges from qualification outcome','80c price plus fees leaves no conservative edge'],
        'conditional_trigger':'Do not enter. Recheck confirmed XI, official match state, weather and fresh executable CLOB; only reconsider if a reconciled independent estimate clears the friction-adjusted 5c gate.',
        'live_playbook':{'required':'Only inspect live if score, minute and CLOB quote are each no older than 120 seconds; monitor field tilt, box entries, shot quality, set pieces and transition concessions.','invalidate':'Stale/conflicting data, red card, key injury or Levadia protecting aggregate without sustained home chance quality.'},
        'exit_plan':{'paper':'No paper position. If later fully evidenced, cap at 0.10u, use executable bid for any trim and hard-exit catalyst exposure before 23:50 Beijing.'},
        'research_missing_fields':['confirmed_starting_XI','complete_last_5_to_10_both_teams','opponent_strength_adjustment','verified_xG_or_shot_quality_sequence','injuries_and_suspensions','weather_at_venue','independent_probability','fresh_CLOB_depth_and_slippage'],
        'next_review_at':'2026-07-16T12:00:00+08:00',
        'strength_classification':'市场强热门/方向性候选，但研究证据不足；不能升级为研究概率或入场授权',
        'sources':[
            {'kind':'official','title':'FCI Levadia official fixture and first-leg result','url':'https://fcilevadia.ee/en/fci-levadia-will-face-welsh-club-caernarfon-town-in-the-first-qualifying-round-of-the-uefa-conference-league/'},
            {'kind':'official','title':'UEFA Conference League qualification schedule','url':'https://fr.uefa.com/uefaconferenceleague/news/02a6-20e5f497f0ab-8207c5460c54-1000--qualifications-pour-la-ligue-conference-calendrier-dates-mode-d/'},
            {'kind':'independent','title':'Sports Mole Caernarfon vs Levadia preview','url':'https://www.sportsmole.co.uk/football/caernarfon-town/europa-conference-league/preview/caernarfon-vs-levadia-prediction-team-news-lineups_600846.html'},
            {'kind':'independent','title':'The Stats Zone Caernarfon vs Levadia preview','url':'https://www.thestatszone.com/caernarfon-town-vs-levadia-preview-prediction-2026-27-uefa-conference-league-first-qualifying-round-204681'}
        ]
    }
    by_slug[levadia['event_slug']] = levadia
    payload.update({'schema_version':'polymarket-sports-event-research-v1','research_run_id':f'pm-sports-research-{NOW.replace(":","").replace("-","")}', 'created_at':NOW,'report_date_beijing':'2026-07-16','research_items':list(by_slug.values()),'paper_only':True,'live_orders_enabled':False,'private_api_used':False,'real_money_execution_authorized':False})
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':'ok','updated':row['event_slug'],'research_items':len(by_slug),'created_at':NOW},ensure_ascii=False))
if __name__ == '__main__': main()
