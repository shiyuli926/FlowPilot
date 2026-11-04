"""Deterministic scheduling on UTC slots, availability interpreted in the user's timezone."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from app.schemas import Task, Event, Block, Settings
from app.recurrence import expand_events

UTC = timezone.utc
STEP = timedelta(minutes=15)
def uid(): return uuid4().hex

def overlap(a, b, c, d): return a < d and c < b

def minutes(b): return int((b.end_at - b.start_at).total_seconds() // 60)

def slots_for(settings, now):
    tz = ZoneInfo(settings.timezone)
    start = now.astimezone(UTC).replace(second=0, microsecond=0)
    start += timedelta(minutes=(-start.minute) % 15)
    if start < now: start += STEP
    end = start + timedelta(days=settings.horizon_days)
    slots = []
    while start + STEP <= end:
        local = start.astimezone(tz)
        finish = (start + STEP).astimezone(tz)
        if any(r.weekday == local.weekday() and r.start_time <= local.time().replace(tzinfo=None) and finish.date() == local.date() and finish.time().replace(tzinfo=None) <= r.end_time for r in settings.availability):
            slots.append(start)
        start += STEP
    return slots

def validate_dependencies(tasks):
    by_id = {t.id: t for t in tasks}
    visiting, visited = set(), set()
    def visit(t):
        if t.id in visiting: raise ValueError('任务依赖存在循环')
        if t.id in visited: return
        visiting.add(t.id)
        for dep in t.dependencies:
            if dep not in by_id: raise ValueError('依赖任务不存在')
            visit(by_id[dep])
        visiting.remove(t.id); visited.add(t.id)
    for t in tasks: visit(t)

def validate_blocks(blocks, tasks, events, settings, now, allow_existing_past=True):
    start=min([now]+[b.start_at for b in blocks])
    end=max([now+timedelta(days=settings.horizon_days)]+[b.end_at for b in blocks])
    events=expand_events(events,start,end)
    by_id = {t.id: t for t in tasks}
    available = set(slots_for(settings, now))
    totals = {}
    for b in blocks:
        t = by_id.get(b.task_id)
        if not t or t.status == 'done': raise ValueError('时间块关联的任务已删除或完成')
        if b.end_at <= b.start_at: raise ValueError('时间块时长必须为正')
        if b.start_at < now and allow_existing_past: continue
        if t.earliest_start and b.start_at < t.earliest_start: raise ValueError('早于任务最早开始时间')
        if t.deadline_type == 'hard' and t.deadline and b.end_at > t.deadline: raise ValueError('时间块超过硬截止时间')
        s = b.start_at
        while s < b.end_at:
            if s.astimezone(UTC) not in available: raise ValueError('时间块不在可用时段或未按15分钟对齐')
            s += STEP
        if s != b.end_at: raise ValueError('时间块必须以15分钟对齐')
        n = minutes(b)
        if t.splittable and (n > t.max_block_minutes or n < min(t.min_block_minutes, t.remaining_minutes)):
            raise ValueError('时间块不满足最短或最长连续时长')
        if not t.splittable and n != t.remaining_minutes: raise ValueError('不可拆分任务需要连续完成')
        totals[t.id] = totals.get(t.id, 0) + n
        if totals[t.id] > t.remaining_minutes: raise ValueError('已安排时长超过剩余工作量')
    active = [b for b in blocks if b.end_at > now]
    for i, b in enumerate(active):
        if any(overlap(b.start_at, b.end_at, e.start_at, e.end_at) for e in events): raise ValueError('固定日程与保留的时间块冲突，请先解锁或调整')
        if any(overlap(b.start_at, b.end_at, c.start_at, c.end_at) for c in active[i+1:]): raise ValueError('时间块重叠')
    for b in active:
        for dep in by_id[b.task_id].dependencies:
            if by_id[dep].status == 'done': continue
            dep_blocks = [c for c in active if c.task_id == dep]
            if sum(minutes(c) for c in dep_blocks) < by_id[dep].remaining_minutes or max((c.end_at for c in dep_blocks), default=now) > b.start_at:
                raise ValueError('时间块不满足前置任务约束')


def schedule(tasks: list[Task], events: list[Event], previous: list[Block], settings: Settings, now: datetime):
    events=expand_events(events,min([now]+[b.start_at for b in previous]),max([now+timedelta(days=settings.horizon_days)]+[b.end_at for b in previous]))
    validate_dependencies(tasks)
    all_slots = slots_for(settings, now)
    slot_set = set(all_slots)
    active = {t.id: t for t in tasks if t.status != 'done' and t.remaining_minutes > 0}
    kept = [b.model_copy() for b in previous if b.task_id in active and (b.is_locked or b.start_at <= now < b.end_at) and b.end_at > now]
    # A locked successor also freezes its published prerequisites.
    required={d for b in kept for d in active[b.task_id].dependencies}
    while required:
        dep=required.pop()
        if dep not in active: continue
        existing=[b for b in previous if b.task_id==dep and b.end_at>now and b.id not in {c.id for c in kept}]
        kept.extend(b.model_copy() for b in existing)
        required.update(d for b in existing for d in active[b.task_id].dependencies)
    validate_blocks(kept, tasks, events, settings, now)
    busy = [(e.start_at, e.end_at) for e in events] + [(b.start_at, b.end_at) for b in kept]
    blocks = list(kept)
    done_at = {t.id: now for t in tasks if t.status == 'done'}
    pending = set(active)
    tz = ZoneInfo(settings.timezone)
    def pref(t, start):
        h = start.astimezone(tz).hour
        return int(t.preferred_period != 'any' and not {'morning': 6 <= h < 12, 'afternoon': 12 <= h < 18, 'evening': 18 <= h < 24}.get(t.preferred_period, True))
    while pending:
        ready = [active[i] for i in pending if all(d not in pending for d in active[i].dependencies)]
        if not ready: break
        ready.sort(key=lambda t: (t.deadline or datetime.combine(t.deadline_date, datetime.min.time(), tzinfo=tz) if t.deadline_date else t.deadline or datetime.max.replace(tzinfo=UTC), -t.priority, t.id))
        t = ready[0]; pending.remove(t.id)
        if any(d not in done_at for d in t.dependencies): continue
        earliest = max([now, t.earliest_start or now] + [done_at[d] for d in t.dependencies])
        remaining = t.remaining_minutes - sum(minutes(b) for b in blocks if b.task_id == t.id)
        # Keep unaffected published blocks whenever their constraints still hold.
        for b in sorted(previous, key=lambda b: b.start_at):
            if b.task_id != t.id or b.id in {k.id for k in blocks} or b.start_at < earliest or b.end_at <= now: continue
            n = minutes(b)
            if n > remaining or (t.deadline_type == 'hard' and t.deadline and b.end_at > t.deadline): continue
            if (t.splittable and (n > t.max_block_minutes or n < min(t.min_block_minutes, t.remaining_minutes))) or (not t.splittable and n != t.remaining_minutes): continue
            if all(b.start_at + STEP*j in slot_set for j in range(n//15)) and not any(overlap(b.start_at, b.end_at, a, z) for a,z in busy):
                blocks.append(b.model_copy()); busy.append((b.start_at,b.end_at)); remaining -= n
        while remaining > 0:
            min_n = min(t.min_block_minutes, t.remaining_minutes)
            max_n = min(t.max_block_minutes, remaining) if t.splittable else remaining
            if not t.splittable: min_n = remaining
            candidates = []
            for s in all_slots:
                if s < earliest: continue
                n = 0
                while n + 15 <= max_n:
                    end = s + timedelta(minutes=n+15)
                    if s + timedelta(minutes=n) not in slot_set or any(overlap(end-STEP,end,a,z) for a,z in busy) or (t.deadline_type == 'hard' and t.deadline and end > t.deadline): break
                    n += 15
                # Avoid leaving an undersized final fragment.
                if 0 < remaining - n < min_n: n -= min_n - (remaining - n)
                if n >= min_n: candidates.append((s,n))
            if not candidates: break
            candidates.sort(key=lambda c: (bool(t.deadline and c[0]+timedelta(minutes=c[1]) > t.deadline), pref(t,c[0]), c[0], -c[1]))
            s,n = candidates[0]
            b = Block(id=uid(), task_id=t.id, start_at=s, end_at=s+timedelta(minutes=n), reason=f'{"优先处理临近截止任务" if t.deadline or t.deadline_date else "利用可用专注时段"} · {n} 分钟' + (' · 符合偏好时段' if t.preferred_period != 'any' and not pref(t,s) else ''))
            blocks.append(b); busy.append((b.start_at,b.end_at)); remaining -= n
        own = [b for b in blocks if b.task_id == t.id]
        if remaining <= 0: done_at[t.id] = max([b.end_at for b in own] or [now])
    engine = 'heuristic'
    if settings.engine == 'cp-sat':
        from app.scheduler.optimizer import optimize
        blocks, engine = optimize(tasks, events, previous, settings, now, blocks)
    blocks.sort(key=lambda b: b.start_at)
    validate_blocks(blocks,tasks,events,settings,now)
    return blocks, analyze_risks(tasks, blocks, now), engine


def analyze_risks(tasks, blocks, now):
    risks = []
    for t in tasks:
        if t.status == 'done': continue
        own = [b for b in blocks if b.task_id == t.id and b.end_at > now]
        scheduled = sum(minutes(b) for b in own)
        before = sum(max(0,int((min(b.end_at,t.deadline)-b.start_at).total_seconds()//60)) if t.deadline else minutes(b) for b in own)
        missing = max(0, t.remaining_minutes - before)
        level = 'danger' if missing else 'warning' if t.deadline_date or (t.deadline and t.deadline-now < timedelta(days=2)) else 'ok'
        risks.append({'task_id':t.id,'title':t.title,'level':level,'scheduled_minutes':scheduled,'unscheduled_minutes':max(0,t.remaining_minutes-scheduled),'missing_before_deadline':missing,'deadline_needs_confirmation': bool(t.deadline_date), 'message': f'当前方案仍有 {missing} 分钟未能在截止前安排；可增加可用时间、调整优先级或减少范围。' if missing else '截止日期尚未确认具体时间，暂不保证按时完成。' if t.deadline_date else '已在可用时段安排；请按进度及时更新剩余时长。'})
    return risks
