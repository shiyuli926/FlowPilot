from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas import Event, EventException
from app.recurrence import expand_events
from test_api import client, add, plan, commit


def dt(value): return datetime.fromisoformat(value)


def series(**changes):
    data={'id':'series','title':'组会','start_at':'2026-11-04T15:00:00+08:00',
          'end_at':'2026-11-04T16:00:00+08:00',
          'recurrence':{'frequency':'weekly','weekdays':[2],'timezone':'Asia/Shanghai'}}
    data.update(changes)
    return Event.model_validate(data)


def expand(event, start='2026-11-01T00:00:00+08:00', end='2026-12-01T00:00:00+08:00'):
    return expand_events([event],dt(start),dt(end))


def query(client, start='2026-11-01T00:00:00+08:00', end='2026-12-01T00:00:00+08:00'):
    response=client.get('/api/calendar/occurrences',params={'start':start,'end':end})
    assert response.status_code==200,response.text
    return response.json()


def create(client, value=None):
    response=client.post('/api/calendar/events',json=(value or series()).model_dump(mode='json'))
    assert response.status_code==200,response.text
    return response.json()


def test_weekly_series_is_windowed_and_stable():
    event=series()
    occurrences=expand(event)
    assert [e.start_at.day for e in occurrences]==[4,11,18,25]
    assert all(e.series_id=='series' and e.recurrence is None for e in occurrences)
    assert [e.id for e in expand(event)]==[e.id for e in occurrences]
    assert len(expand(event,'2030-01-01T00:00:00+08:00','2030-02-01T00:00:00+08:00'))==5


def test_daily_interval_and_inclusive_until():
    event=series(recurrence={'frequency':'daily','interval':2,'until':'2026-11-08'})
    assert [e.start_at.day for e in expand(event)]==[4,6,8]


def test_weekly_multiple_days_and_interval_anchor():
    event=series(recurrence={'frequency':'weekly','interval':2,'weekdays':[0,2,4]})
    assert [e.start_at.day for e in expand(event)]==[4,6,16,18,20,30]


def test_cross_midnight_event_is_included_from_previous_day():
    event=series(start_at='2026-11-04T23:00:00+08:00',end_at='2026-11-05T01:00:00+08:00')
    values=expand(event,'2026-11-05T00:00:00+08:00','2026-11-06T00:00:00+08:00')
    assert len(values)==1 and values[0].occurrence_date.isoformat()=='2026-11-04'


def test_dst_preserves_clock_time_and_skips_nonexistent_time():
    event=series(start_at='2026-03-01T02:30:00-05:00',end_at='2026-03-01T03:30:00-05:00',
                 recurrence={'frequency':'weekly','weekdays':[6],'timezone':'America/New_York'})
    values=expand(event,'2026-03-01T00:00:00-05:00','2026-03-16T00:00:00-04:00')
    assert [e.start_at.day for e in values]==[1,15]
    assert [e.start_at.isoformat()[-6:] for e in values]==['-05:00','-04:00']
    assert all(e.start_at.hour==2 for e in values)


def test_fall_dst_keeps_nine_am():
    event=series(start_at='2026-10-25T09:00:00-04:00',end_at='2026-10-25T10:00:00-04:00',
                 recurrence={'frequency':'weekly','weekdays':[6],'timezone':'America/New_York'})
    values=expand(event,'2026-10-25T00:00:00-04:00','2026-11-03T00:00:00-05:00')
    assert [(e.start_at.hour,e.start_at.isoformat()[-6:]) for e in values]==[(9,'-04:00'),(9,'-05:00')]


def test_moved_exception_is_found_in_new_window_without_duplicate():
    event=series(exceptions={'2026-11-11':{'title':'改期组会','start_at':'2026-12-02T10:00:00+08:00','end_at':'2026-12-02T11:00:00+08:00'},
                             '2026-11-18':{'cancelled':True}})
    assert [e.start_at.day for e in expand(event)]==[4,25]
    moved=expand(event,'2026-12-02T00:00:00+08:00','2026-12-03T00:00:00+08:00')
    assert len(moved)==2
    assert next(e for e in moved if e.title=='改期组会').occurrence_date.isoformat()=='2026-11-11'


@pytest.mark.parametrize('rule',[
    {'frequency':'monthly'}, {'frequency':'weekly','weekdays':[7]},
    {'frequency':'daily','interval':0}, {'frequency':'daily','timezone':'Mars/Time'},
    {'frequency':'weekly','until':'2026-11-03'}, {'frequency':'daily','weekdays':[1]},
])
def test_invalid_rules_rejected(rule):
    with pytest.raises(ValidationError): series(recurrence=rule)


def test_api_persists_one_master_and_expands_later_months(client):
    master=create(client)
    assert len(client.get('/api/calendar/events').json())==1
    assert len(query(client))==4
    assert len(query(client,'2027-02-01T00:00:00+08:00','2027-03-01T00:00:00+08:00'))==4
    assert all(e['series_id']==master['id'] for e in query(client))


def test_cancel_and_move_one_occurrence_and_edit_group(client):
    master=create(client); url='/api/calendar/events/'+master['id']
    assert client.put(url+'/occurrences/2026-11-11',json={'cancelled':True}).status_code==200
    assert [e['occurrence_date'] for e in query(client)]==['2026-11-04','2026-11-18','2026-11-25']
    response=client.put(url+'/occurrences/2026-11-18',json={'title':'改期',
        'start_at':'2026-11-19T10:00:00+08:00','end_at':'2026-11-19T11:00:00+08:00'})
    assert response.status_code==200,response.text
    changed={**master,'title':'新组会','start_at':'2026-11-04T17:00:00+08:00','end_at':'2026-11-04T18:00:00+08:00'}
    changed['recurrence']={**changed['recurrence'],'until':'2026-11-25'}
    response=client.put(url,json=changed)
    assert response.status_code==200,response.text
    values=query(client)
    assert [e['start_at'][11:16] for e in values]==['17:00','10:00','17:00']
    assert len(values)==3 and values[1]['title']=='改期'
    assert client.delete(url).status_code==200
    assert query(client)==[]


def test_invalid_occurrence_date_and_incomplete_override_rejected(client):
    master=create(client);url='/api/calendar/events/'+master['id']+'/occurrences/'
    for day in ('2026-11-12','invalid','2026-10-28'):
        assert client.put(url+day,json={'cancelled':True}).status_code==400
    assert client.put(url+'2026-11-11',json={'start_at':'2026-11-11T15:00:00+08:00'}).status_code==422
    assert len(query(client))==4


def test_conflict_with_later_occurrence_and_atomic_ai_save(client):
    create(client)
    before=client.get('/api/bootstrap').json()
    response=client.post('/api/ai/confirm',json={'tasks':[{'title':'不应保存'}],
        'events':[{'title':'冲突','start_at':'2026-11-18T15:30:00+08:00','end_at':'2026-11-18T16:30:00+08:00'}]})
    assert response.status_code==400 and '冲突' in response.json()['detail']
    assert client.get('/api/bootstrap').json()==before


def test_moving_occurrence_into_conflict_rolls_back(client):
    master=create(client)
    before=query(client)
    response=client.put('/api/calendar/events/'+master['id']+'/occurrences/2026-11-11',json={
        'start_at':'2026-11-18T15:00:00+08:00','end_at':'2026-11-18T16:00:00+08:00'})
    assert response.status_code==400
    assert query(client)==before


@pytest.mark.parametrize('engine',['heuristic','cp-sat'])
def test_scheduler_avoids_every_occurrence(client,engine):
    settings=client.get('/api/settings').json();settings['engine']=engine
    assert client.put('/api/settings',json=settings).status_code==200
    value=series(start_at='2026-11-03T09:00:00+08:00',end_at='2026-11-03T10:00:00+08:00',
                 recurrence={'frequency':'daily'})
    create(client,value)
    add(client,estimated_minutes=600,remaining_minutes=600)
    blocks=plan(client)['blocks']
    events=query(client,'2026-11-03T00:00:00+08:00','2026-11-18T00:00:00+08:00')
    assert blocks
    for b in blocks:
        assert not any(dt(b['start_at'])<dt(e['end_at']) and dt(e['start_at'])<dt(b['end_at']) for e in events)


def test_query_bounds_rejected(client):
    assert client.get('/api/calendar/occurrences',params={'start':'2026-11-01','end':'2028-11-01'}).status_code==400


def test_series_started_in_past_still_cannot_cover_locked_future_block(client):
    add(client); preview=plan(client);commit(client,preview)
    block=preview['blocks'][0]
    client.patch('/api/schedule/blocks/'+block['id'],json={'is_locked':True})
    value=series(start_at='2026-10-20T09:00:00+08:00',end_at='2026-10-20T10:00:00+08:00',
                 recurrence={'frequency':'weekly','weekdays':[1]})
    response=client.post('/api/calendar/events',json=value.model_dump(mode='json'))
    assert response.status_code==400 and '锁定' in response.json()['detail']
    assert client.get('/api/calendar/events').json()==[]


def test_rule_change_clears_exceptions_but_preserves_old_series_on_failure(client):
    master=create(client);url='/api/calendar/events/'+master['id']
    client.put(url+'/occurrences/2026-11-11',json={'cancelled':True})
    master={**master,'recurrence':{**master['recurrence'],'weekdays':[3]}}
    response=client.put(url,json=master)
    assert response.status_code==200 and response.json()['exceptions']=={}
    assert len(query(client))==4
