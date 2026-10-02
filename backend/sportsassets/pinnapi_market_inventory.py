"""Loss-visible REST inventory, not a trading qualification or WS parser.

PinnAPI REST uses decimal odds. Raw WS American odds belong to pinnapi_feed.
REST event.last is serialization time; response.last is a cursor. Neither
becomes price_changed_at. Every parsed line retains sport, league, event,
parent, phase and provider period; unknown shapes become explicit gaps.
"""
from .market_comparison import number


def inventory(payload):
    rows, gaps = [], []
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        return {'rows':[], 'gaps':[{'reason':'INVALID_EVENTS_ENVELOPE'}]}

    def gap(event, path, reason):
        gaps.append({'event_id':event.get('event_id'), 'path':path, 'reason':reason})

    for ev in payload['events']:
        if not isinstance(ev, dict):
            gaps.append({'reason':'INVALID_EVENT'})
            continue
        base = {k:ev.get(k) for k in ('event_id','parent_id','sport_id','league_id',
                                     'home','away','starts','event_type')}
        if ev.get('event_id') is None:
            gap(ev, '', 'EVENT_ID_MISSING')
            continue
        if ev.get('special_markets'):
            gap(ev, 'special_markets', 'SPECIAL_SCHEMA_REQUIRES_VERIFIED_ADAPTER')
        periods = ev.get('periods')
        if not isinstance(periods, dict):
            gap(ev, 'periods', 'PERIODS_MISSING_OR_INVALID')
            continue

        def add(period, path, family, side, odds, line=None, team=None):
            try:
                price = number(odds)
                threshold = None if line is None else number(line)
                if price <= 1:
                    raise ValueError('decimal odds must exceed one')
            except ValueError:
                gap(ev, path, 'INVALID_DECIMAL_ODDS_OR_LINE')
                return
            rows.append(dict(base, period=period, family=family, selection=side,
                             team=team, line=None if threshold is None else str(threshold),
                             decimal_odds=str(price), source_path=path,
                             price_changed_at=None, eligible_for_decision=False,
                             blocker='PRICE_FRESHNESS_AND_SETTLEMENT_UNVERIFIED'))

        for period_key, period in periods.items():
            if not isinstance(period, dict):
                gap(ev, period_key, 'INVALID_PERIOD')
                continue
            number_id = period.get('number')
            if number_id is None:
                gap(ev, period_key, 'PERIOD_NUMBER_MISSING')
                continue
            for key in period:
                if key not in {'number','description','meta','money_line','spreads',
                               'totals','team_total','team_totals'}:
                    gap(ev, period_key+'.'+key, 'UNKNOWN_PERIOD_FIELD')
            ml = period.get('money_line', {})
            if not isinstance(ml, dict):
                gap(ev, period_key+'.money_line', 'INVALID_MONEYLINE')
            else:
                for side, odds in ml.items():
                    if side not in ('home','away','draw'):
                        gap(ev, period_key+'.money_line.'+side, 'UNKNOWN_OUTCOME')
                    else:
                        add(number_id, period_key+'.money_line.'+side,
                            'moneyline', side, odds)
            for key, family, field, sides in (
                ('spreads','spread','hdp',('home','away')),
                ('totals','total','points',('over','under'))):
                lines = period.get(key, {})
                if not isinstance(lines, dict):
                    gap(ev, period_key+'.'+key, 'INVALID_LINES')
                    continue
                for line_key, line in lines.items():
                    path = period_key+'.'+key+'.'+str(line_key)
                    if not isinstance(line, dict) or field not in line:
                        gap(ev, path, 'LINE_METADATA_MISSING')
                        continue
                    try:
                        if number(line_key) != number(line[field]):
                            raise ValueError('line-key conflict')
                    except ValueError:
                        gap(ev, path, 'LINE_KEY_CONFLICT')
                        continue
                    for side in sides:
                        if side not in line:
                            gap(ev,path+'.'+side,'OUTCOME_MISSING')
                            continue
                        # Store the provider's HOME handicap, not an inferred
                        # selected-team handicap. Signed conversion needs proof.
                        add(number_id,path+'.'+side,family,side,line[side],line[field])
            # Plural carries alternate lines; singular is its main-line alias.
            plural = period.get('team_totals')
            singular = period.get('team_total', {})
            teams = plural if plural is not None else singular
            if not isinstance(teams, dict):
                gap(ev,period_key+'.team_totals','INVALID_TEAM_TOTALS')
                continue
            for team, entries in teams.items():
                path = period_key+'.team_totals.'+str(team)
                if team not in ('home','away') or not isinstance(entries,dict):
                    gap(ev,path,'INVALID_TEAM_TOTAL_SIDE')
                    continue
                if plural is None:
                    entries = {str(entries.get('points')):entries}
                for line_key, line in entries.items():
                    if not isinstance(line,dict) or 'points' not in line:
                        gap(ev,path,'LINE_METADATA_MISSING'); continue
                    try:
                        if number(line_key) != number(line['points']):
                            raise ValueError('conflict')
                    except ValueError:
                        gap(ev,path,'LINE_KEY_CONFLICT'); continue
                    for side in ('over','under'):
                        if side not in line:
                            gap(ev,path,'OUTCOME_MISSING'); continue
                        add(number_id,path+'.'+str(line_key)+'.'+side,'team_total',
                            side,line[side],line['points'],team)
    return {'rows':rows,'gaps':gaps,'cursor':payload.get('last'),
            'price_freshness_proven':False,
            'basis':'PINNAPI_REST_DECIMAL_ODDS_INVENTORY_V1'}
