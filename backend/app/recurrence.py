"""Expand stored series in a requested window, preserving local wall-clock times."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.schemas import Event, EventOccurrence


def occurs_on(event, day):
    rule=event.recurrence
    anchor=event.start_at.astimezone(ZoneInfo(rule.timezone)).date()
    if day < anchor or (rule.until and day > rule.until): return False
    if rule.frequency=='daily': return (day-anchor).days % rule.interval == 0
    weeks=((day-timedelta(days=day.weekday()))-(anchor-timedelta(days=anchor.weekday()))).days//7
    return weeks % rule.interval == 0 and day.weekday() in (rule.weekdays or [anchor.weekday()])


def wall_datetime(wall, zone):
    value=wall.replace(tzinfo=zone,fold=0)
    if value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
        return None  # Skip a nonexistent clock time during a spring DST transition.
    return value


def expand_events(events, start, end):
    if not start.tzinfo or not end.tzinfo or end <= start: raise ValueError('展开窗口需要有效带时区起止时间')
    result=[]
    for event in events:
        if not event.recurrence:
            if event.end_at > start and event.start_at < end:
                result.append(EventOccurrence(**event.model_dump()))
            continue
        zone=ZoneInfo(event.recurrence.timezone)
        first=event.start_at.astimezone(zone).replace(tzinfo=None)
        duration=event.end_at.astimezone(zone).replace(tzinfo=None)-first
        day=max(first.date(), (start.astimezone(zone).replace(tzinfo=None)-duration).date())
        last=end.astimezone(zone).date()
        if event.recurrence.until: last=min(last,event.recurrence.until)
        while day <= last:
            key=day.isoformat()
            if occurs_on(event,day) and key not in event.exceptions:
                begin=wall_datetime(datetime.combine(day,first.time()),zone)
                finish=wall_datetime(datetime.combine(day,first.time())+duration,zone)
                if begin and finish and finish > start and begin < end:
                    result.append(EventOccurrence(**{**event.model_dump(), 'id':event.id+'@'+key,
                        'start_at':begin,'end_at':finish,'recurrence':None,'exceptions':{},
                        'series_id':event.id,'occurrence_date':day}))
            day+=timedelta(days=1)
        # Overrides are evaluated independently, so moving an instance into another
        # calendar window does not make it disappear or create a second copy.
        for key,override in event.exceptions.items():
            if override.cancelled or not occurs_on(event,datetime.fromisoformat(key).date()): continue
            if override.end_at > start and override.start_at < end:
                result.append(EventOccurrence(**{**event.model_dump(),'id':event.id+'@'+key,
                    'title':override.title or event.title,'start_at':override.start_at,'end_at':override.end_at,
                    'recurrence':None,'exceptions':{},'series_id':event.id,'occurrence_date':key}))
    return sorted(result,key=lambda e:(e.start_at,e.id))


def validation_windows(events, blocks, now):
    """Creation checks the coming year and distant explicit events/protected blocks."""
    windows=[(now,now+timedelta(days=366))]
    for event in events:
        if event.recurrence:
            if event.start_at > now: windows.append((event.start_at,event.start_at+timedelta(days=366)))
        else: windows.append((event.start_at,min(event.end_at,event.start_at+timedelta(days=366))))
        for override in event.exceptions.values():
            if not override.cancelled: windows.append((override.start_at,override.end_at))
    windows.extend((block.start_at,block.end_at) for block in blocks)
    merged=[]
    for start,end in sorted(windows):
        if merged and start <= merged[-1][1]: merged[-1]=(merged[-1][0],max(merged[-1][1],end))
        else: merged.append((start,end))
    return merged
