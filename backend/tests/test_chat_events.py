import pytest

from test_api import client, add, plan, commit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('LLM_BASE_URL', raising=False)
    monkeypatch.delenv('LLM_MODEL', raising=False)


def chat(client, text):
    response = client.post('/api/ai/chat', json={'text': text, 'now': '2026-11-04T10:00:00+08:00'})
    assert response.status_code == 200, response.text
    return response.json()


def event(title='组会', start='2026-11-05T15:00:00+08:00', end='2026-11-05T16:00:00+08:00'):
    return {'title': title, 'start_at': start, 'end_at': end}


def test_chat_event_preview_has_no_side_effects(client):
    before = client.get('/api/bootstrap').json()
    result = chat(client, '明天下午三点到四点开会')
    assert result['type'] == 'plan_preview'
    preview = result['preview']
    assert preview['items'] == []
    assert len(preview['events']) == 1
    draft = preview['events'][0]['event']
    assert draft['start_at'] == '2026-11-05T15:00:00+08:00'
    assert draft['end_at'] == '2026-11-05T16:00:00+08:00'
    assert client.get('/api/bootstrap').json() == before


@pytest.mark.parametrize(('text', 'start', 'end'), [
    ('明天15:00到16:30参加组会', '2026-11-05T15:00:00+08:00', '2026-11-05T16:30:00+08:00'),
    ('明天下午三点开会，持续45分钟', '2026-11-05T15:00:00+08:00', '2026-11-05T15:45:00+08:00'),
    ('明天下午三点开会', '2026-11-05T15:00:00+08:00', None),
    ('明天开会', None, None),
    ('明天25:00到26:00开会', None, None),
    ('明天16:00到15:00开会', '2026-11-05T16:00:00+08:00', None),
    ('明天23:00至次日01:00开会', '2026-11-05T23:00:00+08:00', '2026-11-06T01:00:00+08:00'),
])
def test_offline_event_times_and_missing_information(client, text, start, end):
    item = chat(client, text)['preview']['events'][0]
    assert item['event']['start_at'] == start
    assert item['event']['end_at'] == end
    if not start or not end: assert item['issues']


def test_mixed_preview_and_atomic_confirmation(client):
    preview = chat(client, '明天下午三点到四点开会；周五晚上8点前提交报告，需要2小时')['preview']
    assert len(preview['items']) == len(preview['events']) == 1
    response = client.post('/api/ai/confirm', json={
        'tasks': [preview['items'][0]['task']], 'events': [preview['events'][0]['event']]})
    assert response.status_code == 200, response.text
    state = client.get('/api/bootstrap').json()
    assert len(state['tasks']) == len(state['events']) == 1
    assert state['events'][0]['source'] == 'ai'
    assert state['schedule']['blocks'] == []


def test_prepare_meeting_material_is_still_a_task(client):
    preview = chat(client, '明天准备会议资料，需要1小时')['preview']
    assert len(preview['items']) == 1 and not preview['events']


def test_recurrence_is_a_single_series_preview(client):
    result = chat(client, '每周三下午三点到四点开会')
    assert result['type'] == 'plan_preview'
    events=result['preview']['events']
    assert len(events)==1
    assert events[0]['event']['recurrence']['weekdays']==[2]
    assert events[0]['event']['start_at']=='2026-11-04T15:00:00+08:00'
    assert client.get('/api/calendar/events').json() == []


def test_event_conflict_rolls_back_whole_confirmation(client):
    assert client.post('/api/calendar/events', json=event()).status_code == 200
    before = client.get('/api/bootstrap').json()
    response = client.post('/api/ai/confirm', json={'tasks': [{'title': '不能部分保存'}], 'events': [event('冲突')]})
    assert response.status_code == 400 and '冲突' in response.json()['detail']
    assert client.get('/api/bootstrap').json() == before


def test_same_batch_overlaps_rejected_but_adjacent_events_allowed(client):
    assert client.post('/api/ai/confirm', json={'events': [event(), event('重复')]}).status_code == 400
    assert client.get('/api/calendar/events').json() == []
    adjacent = event('下一场', '2026-11-05T16:00:00+08:00', '2026-11-05T17:00:00+08:00')
    assert client.post('/api/ai/confirm', json={'events': [event(), adjacent]}).status_code == 200


def test_locked_blocks_protected(client):
    add(client)
    preview = plan(client); commit(client, preview)
    block = preview['blocks'][0]
    client.patch('/api/schedule/blocks/'+block['id'], json={'is_locked': True})
    response = client.post('/api/ai/confirm', json={'events': [event(start=block['start_at'], end=block['end_at'])]})
    assert response.status_code == 400 and '锁定' in response.json()['detail']
    assert client.get('/api/calendar/events').json() == []


@pytest.mark.parametrize('draft', [
    {'title': '无结束', 'start_at': '2026-11-05T15:00:00+08:00', 'end_at': None},
    event(start='2026-11-05T15:00:00'),
    event(end='2026-11-05T14:00:00+08:00'),
])
def test_invalid_event_cannot_be_confirmed(client, draft):
    response = client.post('/api/ai/confirm', json={'tasks': [{'title': '也不能保存'}], 'events': [draft]})
    assert response.status_code == 422
    assert client.get('/api/tasks').json() == []
    assert client.get('/api/calendar/events').json() == []


def test_fixed_events_respected_by_scheduler(client):
    fixed = event(start='2026-11-03T09:00:00+08:00', end='2026-11-03T10:00:00+08:00')
    response = client.post('/api/ai/confirm', json={'tasks': [{'title': '学习'}], 'events': [fixed]})
    assert response.status_code == 200
    blocks = plan(client)['blocks']
    assert blocks and all(b['end_at'] <= fixed['start_at'] or b['start_at'] >= fixed['end_at'] for b in blocks)


@pytest.mark.parametrize(('text','frequency','days','interval'),[
    ('每周一、三、五下午三点到四点开会','weekly',[0,2,4],1),
    ('每周一到周五上午九点到十点开会','weekly',[0,1,2,3,4],1),
    ('每天晚上九点到十点复盘','daily',[],1),
    ('每隔两天晚上九点到十点复盘','daily',[],2),
    ('每个工作日上午九点到十点开会','weekly',[0,1,2,3,4],1),
])
def test_offline_repeated_event_rules(client,text,frequency,days,interval):
    preview=chat(client,text)['preview']
    assert preview['items']==[] and len(preview['events'])==1
    rule=preview['events'][0]['event']['recurrence']
    assert (rule['frequency'],rule['weekdays'],rule['interval'])==(frequency,days,interval)


def test_repeat_interval_respects_explicit_past_anchor(client):
    preview=chat(client,'从2025年11月5日开始每隔两周周三下午三点到四点开会')['preview']
    draft=preview['events'][0]['event']
    assert draft['start_at']=='2026-11-04T15:00:00+08:00'
    assert draft['recurrence']['interval']==2
