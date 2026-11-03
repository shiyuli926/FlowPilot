from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from app.schemas import Task, Event, Block, Settings, Availability
from app.scheduler.engine import schedule, slots_for, minutes, validate_dependencies, overlap
from app.ai.parser import local_parse

NOW=datetime(2026,11,3,0,0,tzinfo=timezone.utc) # Tuesday 08:00 Shanghai

def task(id='a',n=180,**kwargs): return Task(id=id,title=id,estimated_minutes=n,remaining_minutes=n,**kwargs)
def config(**kwargs): return Settings(**kwargs)

def test_fixed_event_split_and_no_conflicts():
    events=[Event(id='e',title='课程',start_at=NOW+timedelta(hours=1),end_at=NOW+timedelta(hours=3))]
    blocks,risks,_=schedule([task()],events,[],config(),NOW)
    assert sum(minutes(b) for b in blocks)==180
    assert not risks[0]['missing_before_deadline']
    assert all(not overlap(b.start_at,b.end_at,e.start_at,e.end_at) for b in blocks for e in events)
    assert all(30<=minutes(b)<=120 for b in blocks)

def test_infeasible_hard_deadline_partial_work():
    blocks,risks,_=schedule([task(n=600,deadline=NOW+timedelta(hours=3),deadline_type='hard')],[],[],config(),NOW)
    assert sum(minutes(b) for b in blocks)==120
    assert risks[0]['missing_before_deadline']==480
    assert all(b.end_at<=NOW+timedelta(hours=3) for b in blocks)

def test_unsplittable_never_partially_scheduled():
    blocks,risks,_=schedule([task(n=600,splittable=False)],[],[],config(),NOW)
    assert not blocks and risks[0]['unscheduled_minutes']==600

def test_locked_and_unaffected_blocks_stable_after_meeting():
    tasks=[task('a'),task('b')]
    blocks,_,_=schedule(tasks,[],[],config(),NOW)
    blocks[0].is_locked=True
    moved=next(b for b in blocks if b.task_id=='b')
    event=Event(id='e',title='临时会议',start_at=moved.start_at,end_at=moved.end_at)
    replanned,_,_=schedule(tasks,[event],blocks,config(),NOW)
    locked=next(b for b in replanned if b.id==blocks[0].id)
    assert locked.start_at==blocks[0].start_at and locked.is_locked
    assert all(not overlap(b.start_at,b.end_at,event.start_at,event.end_at) for b in replanned)
    assert sum(minutes(b) for b in replanned)==360

def test_completed_tasks_not_scheduled():
    blocks,_,_=schedule([task(status='done')],[],[],config(),NOW)
    assert blocks==[]

def test_dependencies_finish_before_successor():
    blocks,_,_=schedule([task('a',n=120),task('b',n=60,priority=3,dependencies=['a'])],[],[],config(),NOW)
    assert max(b.end_at for b in blocks if b.task_id=='a')<=min(b.start_at for b in blocks if b.task_id=='b')

def test_infeasible_dependency_prevents_successor():
    blocks,risks,_=schedule([task('a',n=600,deadline=NOW,deadline_type='hard'),task('b',n=60,dependencies=['a'])],[],[],config(),NOW)
    assert not blocks and risks[1]['unscheduled_minutes']==60

def test_cycles_rejected():
    with pytest.raises(ValueError,match='循环'):validate_dependencies([task('a',dependencies=['b']),task('b',dependencies=['a'])])

def test_no_availability_reports_missing():
    blocks,risks,_=schedule([task()],[],[],config(availability=[]),NOW)
    assert not blocks and risks[0]['unscheduled_minutes']==180

def test_earliest_start_and_date_precision():
    t=task(earliest_start=NOW+timedelta(days=1),deadline_date='2026-11-06',deadline_type='soft')
    blocks,risks,_=schedule([t],[],[],config(),NOW)
    assert min(b.start_at for b in blocks)>=t.earliest_start
    assert risks[0]['deadline_needs_confirmation']

def test_priority_breaks_tie():
    blocks,_,_=schedule([task('a',n=60,priority=1),task('b',n=60,priority=3)],[],[],config(),NOW)
    assert blocks[0].task_id=='b'

def test_dst_fallback_has_two_real_hours():
    settings=config(timezone='America/New_York',horizon_days=1,availability=[Availability(weekday=6,start_time='01:00',end_time='03:00')])
    slots=slots_for(settings,datetime(2026,11,1,4,tzinfo=timezone.utc))
    assert len(slots)==12 and len(set(slots))==12

def test_cpsat_constraints():
    pytest.importorskip('ortools')
    blocks,risks,engine=schedule([task('a',n=120),task('b',n=90,dependencies=['a'])],[],[],config(engine='cp-sat',horizon_days=2),NOW)
    assert sum(minutes(b) for b in blocks)==210
    assert max(b.end_at for b in blocks if b.task_id=='a')<=min(b.start_at for b in blocks if b.task_id=='b')
    assert engine=='cp-sat'

@pytest.mark.parametrize('text,expected',[('本周五晚上8点前交电磁场作业，需要3小时','2026-11-06T20:00:00+08:00'),('下周五晚上8点前交作业，需要3小时','2026-11-13T20:00:00+08:00')])
def test_chinese_datetime(text,expected):
    result=local_parse(text,NOW,'Asia/Shanghai')[0]
    assert result['task']['deadline']==expected
    assert result['task']['estimated_minutes']==180

def test_date_only_and_missing_effort_require_confirmation():
    item=local_parse('周五之前准备考试',NOW,'Asia/Shanghai')[0]
    assert item['task']['deadline'] is None
    assert item['task']['deadline_date']=='2026-11-06'
    assert len(item['issues'])==2

def test_batch_and_chinese_hours():
    items=local_parse('明天读论文，需要两小时；后天写总结，需要半小时',NOW,'Asia/Shanghai')
    assert [i['task']['estimated_minutes'] for i in items]==[120,30]

def test_locked_successor_preserves_prerequisite():
    tasks=[task('a',n=60),task('b',n=60,dependencies=['a'])]
    original,_,_=schedule(tasks,[],[],config(),NOW)
    next(b for b in original if b.task_id=='b').is_locked=True
    result,_,_=schedule(tasks,[],original,config(),NOW)
    assert [(b.task_id,b.start_at,b.end_at) for b in result]==[(b.task_id,b.start_at,b.end_at) for b in original]

def test_twenty_tasks_and_meeting_acceptance():
    tasks=[task(str(i),n=60+(i%4)*30,priority=1+i%3,deadline=NOW+timedelta(days=7),deadline_type='hard') for i in range(20)]
    events=[Event(id=str(i),title='课程',start_at=NOW+timedelta(days=i,hours=1),end_at=NOW+timedelta(days=i,hours=3)) for i in range(5)]
    original,_,_=schedule(tasks,events,[],config(),NOW)
    original[0].is_locked=True
    target=original[2]
    events.append(Event(id='new',title='临时会议',start_at=target.start_at,end_at=target.end_at))
    result,risks,_=schedule(tasks,events,original,config(),NOW)
    assert sum(minutes(b) for b in result)==sum(t.remaining_minutes for t in tasks)
    assert all(r['missing_before_deadline']==0 for r in risks)
    assert any(b.id==original[0].id and b.start_at==original[0].start_at for b in result)
