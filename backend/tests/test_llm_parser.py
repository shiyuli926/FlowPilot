import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.ai import parser


NOW = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
TEXT = '明天晚上8点前交作业，需要3小时'


def mock_model(monkeypatch, handler):
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:11434/v1/')
    monkeypatch.setenv('LLM_MODEL', 'qwen3:8b')
    monkeypatch.setenv('LLM_API_KEY', 'ollama')
    monkeypatch.setenv('LLM_REASONING_EFFORT', 'none')
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '90')
    client_class = httpx.AsyncClient
    monkeypatch.setattr(parser.httpx, 'AsyncClient', lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs))


def test_qwen3_extraction_preserves_date_precision_and_strips_model_ids(monkeypatch):
    def respond(request):
        assert str(request.url) == 'http://127.0.0.1:11434/v1/chat/completions'
        payload = json.loads(request.content)
        assert payload['model'] == 'qwen3:8b'
        assert payload['reasoning_effort'] == 'none'
        assert payload['response_format']['type'] == 'json_schema'
        schema = payload['response_format']['json_schema']['schema']
        task_schema = schema['properties']['items']['items']['properties']['task']
        assert task_schema['properties']['deadline_type']['enum'] == ['hard', 'soft', 'none']
        assert 'deadline_date' in task_schema['required']
        prompt = payload['messages'][0]['content']
        assert '2026-11-04T10:00:00+08:00' in prompt
        assert '周日=2026-11-08' in prompt
        assert request.extensions['timeout']['read'] == 90
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'items': [
            {'task': {'id': 'invented', 'title': '交作业', 'estimated_minutes': 180,
                      'remaining_minutes': 180, 'deadline': '2026-11-05T20:00:00+08:00',
                      'deadline_type': 'hard', 'dependencies': ['invented']}},
            {'task': {'title': '读论文', 'deadline_date': '2026-11-08', 'deadline_type': 'soft'}},
        ]})}}]})

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse(TEXT, NOW, 'Asia/Shanghai'))
    assert result['provider'] == 'llm'
    first, second = result['items']
    assert first['task']['deadline'] == '2026-11-05T20:00:00+08:00'
    assert first['task']['estimated_minutes'] == 180
    assert first['task']['id'] == '' and first['task']['dependencies'] == []
    assert second['task']['deadline'] is None
    assert second['task']['deadline_date'] == '2026-11-08'
    assert second['issues']


@pytest.mark.parametrize('failure', ['timeout', 'http', 'json', 'schema'])
def test_model_failure_falls_back_to_rules(monkeypatch, failure):
    def respond(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('model unavailable', request=request)
        if failure == 'http':
            return httpx.Response(503)
        content = 'invalid JSON' if failure == 'json' else json.dumps({
            'items': [{'task': {'title': '作业', 'estimated_minutes': 17}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse(TEXT, NOW, 'Asia/Shanghai'))
    assert result['provider'] == 'local-rules'
    assert result['items'][0]['task']['deadline'] == '2026-11-05T20:00:00+08:00'
    assert result['items'][0]['task']['estimated_minutes'] == 180


def test_reasoning_parameter_omitted_when_unconfigured(monkeypatch):
    def respond(request):
        assert 'reasoning_effort' not in json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'items': [{'task': {'title': '作业'}}]})}}]})

    mock_model(monkeypatch, respond)
    monkeypatch.delenv('LLM_REASONING_EFFORT')
    assert asyncio.run(parser.parse(TEXT, NOW, 'Asia/Shanghai'))['provider'] == 'llm'


def test_chat_model_extracts_mixed_events_and_tasks(monkeypatch):
    def respond(request):
        payload = json.loads(request.content)
        schema = payload['response_format']['json_schema']['schema']
        assert 'events' in schema['required']
        assert schema['properties']['items']['minItems'] == 0
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'items': [{'task': {'title': '交作业'}}],
            'events': [{'event': {'title': '组会', 'start_at': '2026-11-05T15:00:00+08:00',
                                   'end_at': None}, 'issues': [], 'confidence': .9}],
        })}}]})

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse('明天下午三点开组会；交作业', NOW, 'Asia/Shanghai', include_events=True))
    assert result['provider'] == 'llm'
    assert len(result['items']) == len(result['events']) == 1
    assert result['events'][0]['event']['end_at'] is None
    assert any('结束' in issue for issue in result['events'][0]['issues'])


def test_chat_model_timeout_falls_back_to_events_instead_of_tasks(monkeypatch):
    def respond(request):
        raise httpx.ReadTimeout('unavailable', request=request)

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse('明天下午三点到四点开会', NOW, 'Asia/Shanghai', include_events=True))
    assert result['provider'] == 'local-rules'
    assert result['items'] == []
    assert result['events'][0]['event']['end_at'] == '2026-11-05T16:00:00+08:00'


def test_model_does_not_duplicate_a_meeting_as_a_task(monkeypatch):
    def respond(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'items': [{'task': {'title': '参加组会'}}],
            'events': [{'event': {'title': '组会', 'start_at': '2026-11-06T14:00:00+08:00', 'end_at': None}}],
        })}}]})

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse('后天下午两点开组会', NOW, 'Asia/Shanghai', include_events=True))
    assert result['provider'] == 'llm' and result['items'] == []
    assert len(result['events']) == 1


def test_mixed_input_keeps_explicit_task_deadline_when_model_omits_it(monkeypatch):
    def respond(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'items': [{'task': {'title': '提交报告', 'deadline_date': '2026-11-06', 'deadline_type': 'soft'},
                       'issues': ['未提供具体截止时间']}],
            'events': [{'event': {'title': '开会', 'start_at': '2026-11-05T15:00:00+08:00',
                                   'end_at': '2026-11-05T16:00:00+08:00'}}],
        })}}]})

    mock_model(monkeypatch, respond)
    result = asyncio.run(parser.parse('明天下午三点到四点开会；周五晚上8点前提交报告，需要2小时',
                                     NOW, 'Asia/Shanghai', include_events=True))
    task = result['items'][0]['task']
    assert task['deadline'] == '2026-11-06T20:00:00+08:00'
    assert task['deadline_date'] is None and task['deadline_type'] == 'hard'
    assert result['items'][0]['issues'] == []
