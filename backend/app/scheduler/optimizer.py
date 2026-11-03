"""Optional interval CP-SAT optimizer, bounded to three seconds; safe greedy fallback."""
from datetime import timedelta
from app.schemas import Block
from app.scheduler.engine import slots_for, minutes, uid, validate_blocks

def optimize(tasks, events, previous, settings, now, greedy):
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return greedy, 'heuristic (OR-Tools 未安装)'
    model = cp_model.CpModel()
    slots = slots_for(settings, now)
    if not slots: return greedy, 'cp-sat'
    origin = slots[0]
    offset = lambda d: int((d-origin).total_seconds()//60)
    horizon = offset(slots[-1])+15
    slot_set = set(slots)
    intervals, variables, groups = [], [], {}
    kept = [b for b in previous if b.end_at > now and (b.is_locked or b.start_at <= now < b.end_at) and any(t.id == b.task_id and t.status != 'done' for t in tasks)]
    for e in events:
        intervals.append(model.new_fixed_size_interval_var(offset(e.start_at), int((e.end_at-e.start_at).total_seconds()//60), uid()))
    for b in kept:
        intervals.append(model.new_fixed_size_interval_var(offset(b.start_at), minutes(b), uid()))
    objective = []
    for t in tasks:
        if t.status == 'done': continue
        fixed = [b for b in kept if b.task_id == t.id]
        remaining = t.remaining_minutes-sum(minutes(b) for b in fixed)
        if remaining < 0: return greedy, 'heuristic (锁定安排保留)'
        chunks = []
        while remaining:
            n = min(t.max_block_minutes,remaining) if t.splittable else remaining
            minimum = min(t.min_block_minutes,t.remaining_minutes)
            if 0 < remaining-n < minimum: n -= minimum-(remaining-n)
            if n < minimum: return greedy, 'heuristic (连续时长约束保留)'
            chunks.append(n); remaining -= n
        group = []
        earliest = max([now,t.earliest_start or now]+[b.end_at for b in fixed])
        for idx,n in enumerate(chunks):
            choices = [offset(s) for s in slots if s >= earliest and all(s+timedelta(minutes=k) in slot_set for k in range(0,n,15)) and not(t.deadline_type == 'hard' and t.deadline and s+timedelta(minutes=n)>t.deadline)]
            present = model.new_bool_var(uid())
            start = model.new_int_var_from_domain(cp_model.Domain.from_values(choices or [0]), uid())
            end = model.new_int_var(0,horizon+10080,uid())
            interval = model.new_optional_interval_var(start,n,end,present,uid())
            if not choices: model.add(present == 0)
            if group:
                model.add(present <= group[-1][0])
                model.add(start >= group[-1][2]).only_enforce_if(present)
            intervals.append(interval)
            group.append((present,start,end,n))
            variables.append((t, present,start,end,n))
            objective.append((1-present)*n*(100000+t.priority*10000))
            objective.append(start)
            old = [b for b in previous if b.task_id==t.id and b not in fixed]
            if idx<len(old):
                delta=model.new_int_var(0,horizon+10080,uid())
                model.add_abs_equality(delta,start-offset(old[idx].start_at))
                objective.append(delta*3)
            if t.deadline and t.deadline_type=='soft':
                late=model.new_int_var(0,horizon+10080,uid())
                model.add(late >= end-offset(t.deadline)).only_enforce_if(present)
                objective.append(late*100)
        groups[t.id] = (group, fixed)
    for t,p,s,e,n in variables:
        for dep in t.dependencies:
            g,f = groups.get(dep,([],[]))
            for dp,ds,de,dn in g:
                model.add(p <= dp)
                model.add(s >= de).only_enforce_if(p)
            for b in f: model.add(s >= offset(b.end_at)).only_enforce_if(p)
    for b in kept:
        t=next(t for t in tasks if t.id==b.task_id)
        for dep in t.dependencies:
            g,f=groups.get(dep,([],[]))
            for dp,ds,de,dn in g:
                model.add(dp==1); model.add(de<=offset(b.start_at))
    model.add_no_overlap(intervals)
    model.minimize(sum(objective))
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=3; solver.parameters.num_search_workers=2
    status=solver.solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE): return greedy, 'heuristic (CP-SAT 限时回退)'
    result=list(kept)
    for t,p,s,e,n in variables:
        if solver.value(p): result.append(Block(id=uid(),task_id=t.id,start_at=origin+timedelta(minutes=solver.value(s)),end_at=origin+timedelta(minutes=solver.value(s)+n),reason='CP-SAT 综合优化 · 截止时间 / 优先级 / 减少移动'))
    try: validate_blocks(result,tasks,events,settings,now)
    except ValueError: return greedy, 'heuristic (优化结果约束回退)'
    score=lambda bs: sum((t.remaining_minutes-sum(minutes(b) for b in bs if b.task_id==t.id))*(100000+t.priority*10000) for t in tasks if t.status!='done')
    return (result,'cp-sat') if score(result)<=score(greedy) else (greedy,'heuristic (保留较完整方案)')
