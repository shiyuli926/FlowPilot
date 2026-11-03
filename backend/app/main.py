from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select
from app.core.db import Session, init_db, StateRow, TaskRow, EventRow, ProjectRow, RunRow
from app.schemas import Task, Event, Project, Settings, Block, ParseRequest, PreviewRequest, CommitRequest, MoveBlock
from app.scheduler.engine import schedule, analyze_risks, validate_dependencies, validate_blocks
from app.ai.parser import parse

lock=RLock()
def now(): return datetime.now(timezone.utc)
def uid(): return uuid4().hex
def fail(message, code=400): raise HTTPException(code, str(message))
def rows(db, cls, schema): return [schema.model_validate(r.data) for r in db.scalars(select(cls)).all()]
def current_blocks(db, state):
    run=db.get(RunRow,state['current_run']) if state['current_run'] else None
    active={r.id for r in db.scalars(select(TaskRow)).all() if r.data['status']!='done' and r.data['remaining_minutes']>0}
    return [Block.model_validate(b) for b in run.data['blocks'] if b['task_id'] in active] if run else []
def bump(db,state,**changes):
    data={**state.data,**changes,'revision':state.data['revision']+1}; state.data=data
    return data

def record(db, blocks, state, engine='manual', risks=None, base_revision=None):
    run_id=uid()
    data={'id':run_id,'created_at':now().isoformat(),'base_revision':state['revision'] if base_revision is None else base_revision,'blocks':[b.model_dump(mode='json') for b in blocks], 'engine':engine,'risks':risks or [],'status':'preview'}
    db.add(RunRow(id=run_id,data=data)); return data

def data_sets(db): return rows(db,TaskRow,Task), rows(db,EventRow,Event), Settings.model_validate(db.get(StateRow,1).data['settings'])

@asynccontextmanager
async def lifespan(app):
    init_db(); yield
app=FastAPI(title='FlowPilot',version='0.1.0',lifespan=lifespan)

@app.get('/api/health')
def health(): return {'status':'ok','mode':'local-personal'}

@app.get('/api/bootstrap')
def bootstrap():
    with lock,Session() as db:
        state=db.get(StateRow,1).data; tasks,events,settings=data_sets(db); blocks=current_blocks(db,state)
        return {'tasks':[t.model_dump(mode='json') for t in tasks],'events':[e.model_dump(mode='json') for e in events],'projects':[p.model_dump(mode='json') for p in rows(db,ProjectRow,Project)],'settings':settings.model_dump(mode='json'),'schedule':{'blocks':[b.model_dump(mode='json') for b in blocks],'risks':analyze_risks(tasks,blocks,now()),'run_id':state['current_run']},'revision':state['revision'],'can_undo':bool(state.get('undo_run'))}

@app.get('/api/tasks')
def get_tasks():
    with Session() as db: return rows(db,TaskRow,Task)

@app.post('/api/tasks')
def create_task(task: Task):
    with lock,Session.begin() as db:
        task.id=uid(); tasks=rows(db,TaskRow,Task)
        if task.project_id and not db.get(ProjectRow,task.project_id): fail('项目不存在')
        try: validate_dependencies(tasks+[task])
        except ValueError as e: fail(e)
        db.add(TaskRow(id=task.id,data=task.model_dump(mode='json'))); bump(db,db.get(StateRow,1))
        return task

@app.post('/api/tasks/batch')
def create_batch(tasks: list[Task]):
    if not tasks or len(tasks)>30: fail('每批需包含1至30个任务')
    with lock,Session.begin() as db:
        for task in tasks:
            task.id=uid()
            if task.project_id and not db.get(ProjectRow,task.project_id): fail('项目不存在')
        try: validate_dependencies(rows(db,TaskRow,Task)+tasks)
        except ValueError as e: fail(e)
        for task in tasks: db.add(TaskRow(id=task.id,data=task.model_dump(mode='json')))
        bump(db,db.get(StateRow,1))
        return tasks

@app.patch('/api/tasks/{task_id}')
def update_task(task_id: str, patch: dict):
    if 'id' in patch: fail('任务ID不可修改')
    with lock,Session.begin() as db:
        row=db.get(TaskRow,task_id)
        if not row: fail('任务不存在',404)
        try:
            task=Task.model_validate({**row.data,**patch})
            if task.project_id and not db.get(ProjectRow,task.project_id): fail('项目不存在')
            tasks=[t if t.id != task_id else task for t in rows(db,TaskRow,Task)]
            validate_dependencies(tasks)
            state=db.get(StateRow,1)
            kept=[b for b in current_blocks(db,state.data) if b.is_locked and task.status!='done']
            # Check new task constraints against locked commitments.
            validate_blocks(kept,tasks,rows(db,EventRow,Event),Settings.model_validate(state.data['settings']),now())
        except (ValueError,ValidationError) as e: fail(e)
        row.data=task.model_dump(mode='json'); bump(db,state)
        return task

@app.post('/api/tasks/{task_id}/complete')
def complete_task(task_id: str): return update_task(task_id,{'status':'done','remaining_minutes':0})

@app.delete('/api/tasks/{task_id}')
def delete_task(task_id: str):
    with lock,Session.begin() as db:
        row=db.get(TaskRow,task_id)
        if not row: fail('任务不存在',404)
        if any(task_id in t.dependencies for t in rows(db,TaskRow,Task)): fail('其他任务依赖此任务，请先解除依赖')
        db.delete(row); bump(db,db.get(StateRow,1)); return {'ok':True}

@app.get('/api/projects')
def get_projects():
    with Session() as db: return rows(db,ProjectRow,Project)

@app.post('/api/projects')
def create_project(project: Project):
    with lock,Session.begin() as db:
        project.id=uid(); db.add(ProjectRow(id=project.id,data=project.model_dump(mode='json'))); bump(db,db.get(StateRow,1)); return project

@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    """Remove the category while keeping its tasks and published schedule intact."""
    with lock,Session.begin() as db:
        project=db.get(ProjectRow,project_id)
        if not project: fail('项目不存在',404)
        moved=0
        for task in db.scalars(select(TaskRow)).all():
            if task.data.get('project_id') == project_id:
                task.data={**task.data,'project_id':None}
                moved+=1
        db.delete(project)
        bump(db,db.get(StateRow,1))
        return {'ok':True,'unclassified_tasks':moved}

@app.get('/api/calendar/events')
def get_events():
    with Session() as db: return rows(db,EventRow,Event)

@app.post('/api/calendar/events')
def create_event(event: Event):
    with lock,Session.begin() as db:
        event.id=uid(); state=db.get(StateRow,1)
        from app.scheduler.engine import overlap
        if any((b.is_locked or b.start_at<=now()<b.end_at) and b.end_at>now() and overlap(b.start_at,b.end_at,event.start_at,event.end_at) for b in current_blocks(db,state.data)):
            fail('新事件与锁定或正在执行的时间块冲突，请先解锁或调整时间')
        db.add(EventRow(id=event.id,data=event.model_dump(mode='json'))); bump(db,state); return event

@app.delete('/api/calendar/events/{event_id}')
def delete_event(event_id: str):
    with lock,Session.begin() as db:
        row=db.get(EventRow,event_id)
        if not row: fail('日程不存在',404)
        db.delete(row); bump(db,db.get(StateRow,1)); return {'ok':True}

@app.get('/api/settings')
def get_settings():
    with Session() as db: return db.get(StateRow,1).data['settings']

@app.put('/api/settings')
def save_settings(settings: Settings):
    with lock,Session.begin() as db:
        state=db.get(StateRow,1); tasks,events,_=data_sets(db)
        try: validate_blocks([b for b in current_blocks(db,state.data) if b.is_locked],tasks,events,settings,now())
        except ValueError as e: fail(e)
        bump(db,state,settings=settings.model_dump(mode='json')); return settings

@app.post('/api/tasks/parse')
async def parse_tasks(request: ParseRequest):
    with Session() as db: tz=db.get(StateRow,1).data['settings']['timezone']
    clock=request.now or now()
    if not clock.tzinfo: fail('输入时间必须包含时区')
    return await parse(request.text,clock,tz)

@app.get('/api/schedule')
def get_schedule(): return bootstrap()['schedule']
@app.get('/api/schedule/risks')
def get_risks(): return bootstrap()['schedule']['risks']

@app.post('/api/schedule/preview')
@app.post('/api/schedule/replan')
def preview(request: PreviewRequest = PreviewRequest()):
    clock=request.now or now()
    if not clock.tzinfo: fail('时间必须包含时区')
    with lock,Session.begin() as db:
        state=db.get(StateRow,1).data; tasks,events,settings=data_sets(db); old=current_blocks(db,state)
        try: blocks,risks,engine=schedule(tasks,events,old,settings,clock)
        except ValueError as e: fail(e)
        data=record(db,blocks,state,engine,risks)
        old_signature={(b.task_id,b.start_at,b.end_at,b.is_locked) for b in old}
        new_signature={(b.task_id,b.start_at,b.end_at,b.is_locked) for b in blocks}
        data['changes']={'added':len(new_signature-old_signature),'removed':len(old_signature-new_signature),'retained':len(new_signature&old_signature)}
        row=db.get(RunRow,data['id']); row.data=dict(data)
        return data

@app.post('/api/schedule/commit')
def commit(request: CommitRequest):
    with lock,Session.begin() as db:
        run=db.get(RunRow,request.preview_id); state=db.get(StateRow,1)
        if not run or run.data['status']!='preview': fail('预览不存在或已经提交',404)
        if run.data['base_revision']!=state.data['revision']: fail('任务或日历已改变，请重新预览',409)
        tasks,events,settings=data_sets(db); blocks=[Block.model_validate(b) for b in run.data['blocks']]
        old_ids={b.id for b in current_blocks(db,state.data)}
        if any(b.start_at<now() and b.id not in old_ids for b in blocks): fail('预览开始时间已过去，请重新规划',409)
        try: validate_blocks(blocks,tasks,events,settings,now())
        except ValueError as e: fail(e)
        run.data={**run.data,'status':'committed'}
        bump(db,state,undo_run=state.data['current_run'],current_run=run.id)
        return {'ok':True,'run_id':run.id}

@app.post('/api/schedule/undo')
def undo():
    with lock,Session.begin() as db:
        state=db.get(StateRow,1); prev=state.data.get('undo_run')
        if not prev: fail('没有可撤销的排程')
        tasks,events,settings=data_sets(db); row=db.get(RunRow,prev)
        blocks=[Block.model_validate(b) for b in row.data['blocks']]
        try: validate_blocks(blocks,tasks,events,settings,now())
        except ValueError as e: fail(f'原计划与当前数据冲突，无法撤销：{e}')
        # Locked blocks from the current plan must survive an undo.
        locked=[b for b in current_blocks(db,state.data) if b.is_locked and b.end_at>now()]
        if any(not any((c.task_id,c.start_at,c.end_at,c.is_locked)==(b.task_id,b.start_at,b.end_at,b.is_locked) for c in blocks) for b in locked): fail('撤销将改变锁定块，请先解锁')
        bump(db,state,current_run=prev,undo_run=None); return {'ok':True}

@app.patch('/api/schedule/blocks/{block_id}')
def move_block(block_id: str, request: MoveBlock):
    with lock,Session.begin() as db:
        state=db.get(StateRow,1); blocks=current_blocks(db,state.data)
        block=next((b for b in blocks if b.id==block_id),None)
        if not block: fail('时间块不存在',404)
        if block.start_at<=now(): fail('已开始的时间块不能移动或修改锁定状态')
        if request.start_at is not None and not request.start_at.tzinfo or request.end_at is not None and not request.end_at.tzinfo: fail('时间必须带时区')
        block.start_at=request.start_at or block.start_at; block.end_at=request.end_at or block.end_at; block.is_locked=request.is_locked
        tasks,events,settings=data_sets(db)
        try: validate_blocks(blocks,tasks,events,settings,now(),False)
        except ValueError as e: fail(e)
        run=record(db,blocks,state.data); row=db.get(RunRow,run['id']); row.data={**run,'status':'committed'}
        bump(db,state,undo_run=state.data['current_run'],current_run=run['id']); return block

@app.get('/api/schedule/history')
def history():
    with Session() as db:
        runs=list(db.scalars(select(RunRow)).all())
        return [{k:v for k,v in r.data.items() if k!='blocks'} for r in sorted(runs,key=lambda r:r.data['created_at'],reverse=True)[:30]]

@app.post('/api/ai/chat')
async def chat(request: ParseRequest):
    if any(w in request.text for w in ['能完成','风险','来得及']):
        risks=get_risks(); bad=[r for r in risks if r['missing_before_deadline']]
        return {'type':'analysis','message':f'根据当前已发布计划，{len(bad)} 个任务有未安排工作量，共 {sum(r["missing_before_deadline"] for r in bad)} 分钟。风险是当前方案的结果，不代表全局不可行证明。可先重新规划，再增加可用时间或调整任务范围。','risks':risks}
    return {'type':'task_preview','message':'我已提取任务建议，请确认后保存。调整可用时间请前往设置；对话不会直接移动或删除任务。','preview':await parse_tasks(request)}

@app.post('/api/demo')
def seed_demo():
    from zoneinfo import ZoneInfo
    with lock,Session.begin() as db:
        if db.scalar(select(TaskRow.id)) or db.scalar(select(EventRow.id)) or db.scalar(select(ProjectRow.id)): fail('仅可在空工作区加载示例')
        base=now().astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        projects=[]
        for name,color in [('课程学习','#657d68'),('海雾研究','#8b80aa'),('个人成长','#ba8c56')]:
            p=Project(id=uid(),name=name,color=color); projects.append(p); db.add(ProjectRow(id=p.id,data=p.model_dump(mode='json')))
        for title,duration,priority,day,proj in [('完成电磁场作业',180,3,2,0),('海雾论文阅读',240,2,4,1),('模型实验与结果整理',180,2,5,1),('英语听力练习',45,1,6,2),('整理本周学习笔记',60,1,4,0)]:
            t=Task(id=uid(),title=title,estimated_minutes=duration,remaining_minutes=duration,priority=priority,deadline=base+timedelta(days=day,hours=20),deadline_type='hard',project_id=projects[proj].id)
            db.add(TaskRow(id=t.id,data=t.model_dump(mode='json')))
        for day,hour,duration,title in [(0,9,120,'通信原理课程'),(1,14,90,'课题组例会'),(2,9,120,'电磁场课程')]:
            e=Event(id=uid(),title=title,start_at=base+timedelta(days=day,hours=hour),end_at=base+timedelta(days=day,hours=hour,minutes=duration)); db.add(EventRow(id=e.id,data=e.model_dump(mode='json')))
        bump(db,db.get(StateRow,1)); return {'ok':True}

# A built frontend is served by FastAPI, giving the personal app a single origin.
dist=Path(__file__).resolve().parents[2]/'frontend'/'dist'
if dist.is_dir():
    app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'): fail('接口不存在',404)
        return FileResponse(dist/'index.html')
