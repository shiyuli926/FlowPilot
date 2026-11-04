"""Conservative fixed-event extraction used when the model is unavailable."""
import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from app.recurrence import wall_datetime

EVENT_WORDS = r'固定日程|会议|开会|组会|例会|讨论会|课程|上课|预约|面试|就诊|聚餐|航班|培训|约会'
DIGITS = '零一二三四五六七八九'
CN = r'(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})'
CLOCK = re.compile(r'(上午|下午|晚上|中午|早上|凌晨)?\s*('+CN+r')\s*[:：点时](?:\s*('+CN+r')分?|\s*(半))?')
DATE = r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?'


def recurrence_from_text(text, timezone):
    daily=bool(re.search(r'每天|每日|每(?:隔)?'+CN+r'天',text))
    weekly=bool(re.search(r'每周|每星期|每个星期|每个周|工作日|周末|每(?:隔)?'+CN+r'周',text))
    if not daily and not weekly: return None
    days=[]
    if '工作日' in text: days=list(range(5))
    elif '周末' in text: days=[5,6]
    elif weekly:
        for group in re.findall(r'(?:周|星期)([一二三四五六日天、和及，, ]+)',text):
            days.extend('一二三四五六日'.index(ch.replace('天','日')) for ch in group if ch in '一二三四五六日天')
        for first,last in re.findall(r'(?:周|星期)([一二三四五六日天])[到至—-](?:周|星期)?([一二三四五六日天])',text):
            a='一二三四五六日'.index(first.replace('天','日')); b='一二三四五六日'.index(last.replace('天','日'))
            days.extend(range(a,b+1) if a<=b else list(range(a,7))+list(range(b+1)))
    interval=re.search(r'每(?:隔)?('+CN+r')(?:周|天)',text)
    until=re.search(r'(?:截至|截止到?|直到|至|到)\s*'+DATE,text)
    until_day=datetime(*map(int,until.groups())).date().isoformat() if until else None
    return {'frequency':'daily' if daily else 'weekly','interval':integer(interval[1]) if interval else 1,
            'weekdays':sorted(set(days)), 'until':until_day,'timezone':timezone}


def is_event(text):
    if re.search(r'每天|每日|每周|工作日|周末|每星期|每个星期|每个周|每(?:隔)?'+CN+r'(?:周|天)',text): return True
    if '固定日程' in text: return True
    if re.search(r'(准备|撰写|整理|完成|提交|制作|写).*?(会议|课程|面试|培训)', text): return False
    if len(list(CLOCK.finditer(text))) >= 2 and re.search(r'到|至|[-~—]', text) and not re.search(r'之前|前完成|截止', text): return True
    return bool(re.search(EVENT_WORDS, text))


def integer(value):
    if value.isdigit(): return int(value)
    value = value.replace('两', '二')
    if '十' in value:
        left, right = value.split('十')
        return (DIGITS.index(left) if left else 1)*10 + (DIGITS.index(right) if right else 0)
    return DIGITS.index(value)


def local_event(text, now, timezone, parse_task):
    tz = ZoneInfo(timezone)
    task = parse_task(text, now, timezone)[0]['task']
    day = task['deadline_date'] or (task['deadline'] or '')[:10]
    start = end = None
    clocks = list(CLOCK.finditer(text))
    issues = []
    try:
        recurrence=recurrence_from_text(text,timezone)
        if recurrence:
            explicit_start=re.search(r'从\s*'+DATE,text)
            day=(datetime(*map(int,explicit_start.groups())).date() if explicit_start else now.astimezone(tz).date()).isoformat()
        def clock(match, inherited=None):
            period = match[1] or inherited
            hour = integer(match[2])
            if period in ('下午', '晚上', '中午') and hour < 12: hour += 12
            if period == '凌晨' and hour == 12: hour = 0
            return time(hour, integer(match[3]) if match[3] else 30 if match[4] else 0)

        if day and clocks:
            start = datetime.combine(datetime.fromisoformat(day).date(), clock(clocks[0]), tzinfo=tz)
        if day and len(clocks) >= 2:
            end = datetime.combine(datetime.fromisoformat(day).date(), clock(clocks[1], clocks[0][1]), tzinfo=tz)
            if re.search(r'次日|第二天|跨天', text): end += timedelta(days=1)
        elif start:
            duration = re.search(r'('+CN+r')\s*(小时|分钟)', text)
            if duration: end = start + timedelta(minutes=integer(duration[1])*(60 if duration[2]=='小时' else 1))
        if start and end and end <= start:
            end = None
            issues.append('结束时间必须晚于开始时间，请确认是否跨天。')
        if recurrence and start:
            duration=end-start if end else None
            anchor=start.date()
            if start.date()<now.astimezone(tz).date():
                start=datetime.combine(now.astimezone(tz).date(),start.timetz())
            for _ in range(370):
                weekday_ok=recurrence['frequency']=='daily' or not recurrence['weekdays'] or start.weekday() in recurrence['weekdays']
                steps=(start.date()-anchor).days if recurrence['frequency']=='daily' else ((start.date()-timedelta(days=start.weekday()))-(anchor-timedelta(days=anchor.weekday()))).days//7
                if start >= now.astimezone(tz) and weekday_ok and steps%recurrence['interval']==0: break
                start+=timedelta(days=1)
            if end: end=start+duration
            if recurrence['frequency']=='weekly' and not recurrence['weekdays'] and not explicit_start:
                start=end=None
                issues.append('请补充每周的星期几或明确首次日期。')
        if start and (wall_datetime(start.replace(tzinfo=None),tz) is None or (end and wall_datetime(end.replace(tzinfo=None),tz) is None)):
            start=end=None
            issues.append('此当地时间因夏令时切换不存在，请选择有效的首次日期和时间。')
    except ValueError:
        start = end = None
        issues.append('日期或时间无效，请修改。')
        recurrence=None
    if not start: issues.append('请补充具体开始日期和时间。')
    if not end: issues.append('请补充结束时间或明确的持续时长；不会默认安排一小时。')
    # Keep the original wording visible when offline rules cannot isolate a title.
    return {'event': {'title': text[:240], 'start_at': start.isoformat() if start else None,
                      'end_at': end.isoformat() if end else None,'recurrence':recurrence},
            'issues': issues, 'confidence': .6 if issues else .9}
