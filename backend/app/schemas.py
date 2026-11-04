from datetime import datetime, date, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, model_validator, field_validator, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Task(StrictModel):
    id: str = ''
    title: str = Field(min_length=1, max_length=240)
    project_id: str | None = None
    deadline: datetime | None = None
    deadline_date: date | None = None
    deadline_type: Literal['hard', 'soft', 'none'] = 'none'
    priority: int = Field(default=2, ge=1, le=3)
    estimated_minutes: int = Field(default=60, ge=15, le=10080, multiple_of=15)
    remaining_minutes: int = Field(default=60, ge=0, le=10080, multiple_of=15)
    status: Literal['todo', 'in_progress', 'done'] = 'todo'
    earliest_start: datetime | None = None
    splittable: bool = True
    min_block_minutes: int = Field(default=30, ge=15, le=480, multiple_of=15)
    max_block_minutes: int = Field(default=120, ge=15, le=1440, multiple_of=15)
    preferred_period: Literal['any', 'morning', 'afternoon', 'evening'] = 'any'
    dependencies: list[str] = Field(default_factory=list)
    notes: str = ''
    source_text: str | None = None

    @field_validator('title')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError('任务名称不能为空')
        return value.strip()

    @model_validator(mode='after')
    def valid(self):
        for dt in [self.deadline, self.earliest_start]:
            if dt and dt.tzinfo is None: raise ValueError('精确时间必须包含时区')
        if self.deadline and self.deadline_date: raise ValueError('请选择精确截止时间或日期之一')
        if self.min_block_minutes > self.max_block_minutes: raise ValueError('最短时间块不能超过最长时间块')
        if self.remaining_minutes > self.estimated_minutes: raise ValueError('剩余时长不能超过预计时长，请先更新预计时长')
        if self.status == 'done': self.remaining_minutes = 0
        return self

class Recurrence(StrictModel):
    frequency: Literal['daily', 'weekly']
    interval: int = Field(default=1, ge=1, le=12)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    until: date | None = None
    timezone: str = 'Asia/Shanghai'

    @model_validator(mode='after')
    def valid(self):
        try: ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError: raise ValueError('重复规则需要有效 IANA 时区')
        if any(d < 0 or d > 6 for d in self.weekdays): raise ValueError('星期必须在0至6之间')
        self.weekdays=sorted(set(self.weekdays))
        if self.frequency=='daily' and self.weekdays: raise ValueError('每天重复无需指定星期')
        return self

class EventException(StrictModel):
    cancelled: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=240)
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode='after')
    def valid(self):
        if not self.cancelled:
            if not self.start_at or not self.end_at: raise ValueError('修改单次日程需要起止时间')
            if not self.start_at.tzinfo or not self.end_at.tzinfo: raise ValueError('日程时间需要时区')
            if self.end_at <= self.start_at: raise ValueError('结束必须晚于开始')
        return self

class Event(StrictModel):
    id: str = ''
    title: str = Field(min_length=1, max_length=240)
    start_at: datetime
    end_at: datetime
    color: str = '#b98960'
    source: str = 'manual'
    recurrence: Recurrence | None = None
    exceptions: dict[str, EventException] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid(self):
        if not self.start_at.tzinfo or not self.end_at.tzinfo: raise ValueError('日程时间需要时区')
        if self.end_at <= self.start_at: raise ValueError('结束必须晚于开始')
        if self.recurrence:
            zone=ZoneInfo(self.recurrence.timezone)
            wall_start=self.start_at.astimezone(zone).replace(tzinfo=None)
            wall_duration=self.end_at.astimezone(zone).replace(tzinfo=None)-wall_start
            if not timedelta(0)<wall_duration<=timedelta(days=7): raise ValueError('重复日程的当地起止时间需为正且不超过7天')
            start=wall_start.date()
            if self.recurrence.until and self.recurrence.until < start: raise ValueError('重复截止日期不能早于首次日期')
        elif self.exceptions: raise ValueError('单次日程不能设置重复例外')
        for key in self.exceptions: date.fromisoformat(key)
        return self

class EventOccurrence(Event):
    series_id: str | None = None
    occurrence_date: date | None = None

class Project(StrictModel):
    id: str = ''
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default='#637b66', pattern=r'^#[0-9a-fA-F]{6}$')

class Availability(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    @model_validator(mode='after')
    def valid(self):
        if self.end_time <= self.start_time: raise ValueError('可用时段必须在同一天且结束晚于开始')
        if any(t.minute % 15 or t.second for t in (self.start_time, self.end_time)): raise ValueError('可用时间以15分钟对齐')
        return self

class Settings(StrictModel):
    timezone: str = 'Asia/Shanghai'
    horizon_days: int = Field(default=14, ge=1, le=28)
    engine: Literal['heuristic', 'cp-sat'] = 'heuristic'
    availability: list[Availability] = Field(default_factory=lambda: [Availability(weekday=d, start_time='09:00', end_time='12:00') for d in range(7)] + [Availability(weekday=d, start_time='14:00', end_time='18:00') for d in range(7)] + [Availability(weekday=d, start_time='19:00', end_time='21:00') for d in range(7)])
    @field_validator('timezone')
    @classmethod
    def timezone_valid(cls, value):
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError: raise ValueError('无效 IANA 时区')
        return value

class Block(StrictModel):
    id: str
    task_id: str
    start_at: datetime
    end_at: datetime
    is_locked: bool = False
    status: Literal['planned', 'done'] = 'planned'
    reason: str = '根据截止日期、优先级和可用时段安排'

class ParseRequest(StrictModel):
    text: str = Field(min_length=1, max_length=10000)
    now: datetime | None = None

class EventDraft(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    start_at: datetime | None = None
    end_at: datetime | None = None
    recurrence: Recurrence | None = None

    @model_validator(mode='after')
    def valid(self):
        for value in (self.start_at, self.end_at):
            if value and value.tzinfo is None: raise ValueError('日程时间需要时区')
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError('结束必须晚于开始')
        return self

class AIConfirmation(StrictModel):
    tasks: list[Task] = Field(default_factory=list, max_length=30)
    events: list[Event] = Field(default_factory=list, max_length=30)

class PreviewRequest(StrictModel):
    now: datetime | None = None

class CommitRequest(StrictModel):
    preview_id: str

class MoveBlock(StrictModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    is_locked: bool = True
