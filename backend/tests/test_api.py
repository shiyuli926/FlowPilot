import importlib
from datetime import datetime,timezone
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'test.db'))
    from app.core import db
    importlib.reload(db)
    from app import main
    importlib.reload(main)
    monkeypatch.setattr(main,'now',lambda:datetime(2026,11,3,0,tzinfo=timezone.utc))
    with TestClient(main.app) as c: yield c
    db.engine.dispose()

def add(client,title='任务',**fields):
    r=client.post('/api/tasks',json={'title':title,**fields});assert r.status_code==200,r.text;return r.json()

def plan(client):
    r=client.post('/api/schedule/preview',json={}); assert r.status_code==200,r.text;return r.json()

def commit(client,p):
    r=client.post('/api/schedule/commit',json={'preview_id':p['id']});assert r.status_code==200,r.text

def test_preview_commit_lock_and_replan(client):
    add(client);p=plan(client)
    assert client.get('/api/schedule').json()['blocks']==[]
    commit(client,p)
    block=p['blocks'][0]
    assert client.patch('/api/schedule/blocks/'+block['id'],json={'is_locked':True}).status_code==200
    replanned=plan(client)
    assert replanned['blocks'][0]['id']==block['id']
    assert replanned['blocks'][0]['is_locked']

def test_stale_preview_rejected(client):
    add(client);p=plan(client);add(client,'新任务')
    assert client.post('/api/schedule/commit',json={'preview_id':p['id']}).status_code==409

def test_complete_releases_future_blocks(client):
    t=add(client);commit(client,plan(client))
    assert client.post('/api/tasks/'+t['id']+'/complete').status_code==200
    assert plan(client)['blocks']==[]

def test_invalid_event_and_naive_time_rejected(client):
    assert client.post('/api/calendar/events',json={'title':'会议','start_at':'2026-11-03T09:00:00','end_at':'2026-11-03T08:00:00'}).status_code==422

def test_lock_conflicting_event_rejected(client):
    add(client);p=plan(client);commit(client,p);b=p['blocks'][0]
    client.patch('/api/schedule/blocks/'+b['id'],json={'is_locked':True})
    r=client.post('/api/calendar/events',json={'title':'会议','start_at':b['start_at'],'end_at':b['end_at']})
    assert r.status_code==400

def test_undo_preserves_version(client):
    add(client);p=plan(client);commit(client,p);add(client,'新任务');commit(client,plan(client))
    assert client.post('/api/schedule/undo').status_code==200
    assert client.get('/api/schedule').json()['blocks']==p['blocks']
    assert len(client.get('/api/schedule/history').json())>=2

def test_task_dependency_update_cycle_rejected(client):
    a=add(client,'a');b=add(client,'b',dependencies=[a['id']])
    assert client.patch('/api/tasks/'+a['id'],json={'dependencies':[b['id']]}).status_code==400
    assert client.delete('/api/tasks/'+a['id']).status_code==400

def test_invalid_settings_and_move_rejected(client):
    settings=client.get('/api/settings').json();settings['timezone']='Mars/Olympus'
    assert client.put('/api/settings',json=settings).status_code==422
    add(client);p=plan(client);commit(client,p)
    assert client.patch('/api/schedule/blocks/'+p['blocks'][0]['id'],json={'start_at':'2026-11-03T02:00:00Z','end_at':'2026-11-03T05:00:00Z'}).status_code==400

def test_parse_no_side_effects(client):
    r=client.post('/api/tasks/parse',json={'text':'明天晚上8点前交作业，需要3小时'})
    assert r.status_code==200,r.text
    assert r.json()['items'][0]['task']['deadline']=='2026-11-04T20:00:00+08:00'
    assert client.get('/api/tasks').json()==[]

def test_demo_not_reloaded(client):
    assert client.post('/api/demo').status_code==200
    assert client.post('/api/demo').status_code==400
    p=plan(client);assert len(p['blocks'])>=5

def test_batch_atomicity(client):
    r=client.post('/api/tasks/batch',json=[{'title':'valid'},{'title':'invalid','dependencies':['missing']}])
    assert r.status_code==400 and client.get('/api/tasks').json()==[]
    assert client.post('/api/tasks/batch',json=[{'title':'a'},{'title':'b'}]).status_code==200
    assert len(client.get('/api/tasks').json())==2

def test_complete_locked_task_immediately_releases_time(client):
    t=add(client);p=plan(client);commit(client,p);b=p['blocks'][0]
    client.patch('/api/schedule/blocks/'+b['id'],json={'is_locked':True})
    assert client.post('/api/tasks/'+t['id']+'/complete').status_code==200
    assert client.get('/api/schedule').json()['blocks']==[]
    assert client.post('/api/calendar/events',json={'title':'会议','start_at':b['start_at'],'end_at':b['end_at']}).status_code==200
