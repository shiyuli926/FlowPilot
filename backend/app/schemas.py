from datetime import datetime, date, time
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

class Event(StrictModel):
    id: str = ''
    title: str = Field(min_length=1, max_length=240)
    start_at: datetime
    end_at: datetime
    color: str = '#b98960'
    source: str = 'manual'

    @model_validator(mode='after')
    def valid(self):
        if not self.start_at.tzinfo or not self.end_at.tzinfo: raise ValueError('日程时间需要时区')
        if self.end_at <= self.start_at: raise ValueError('结束必须晚于开始')
        return self

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

class PreviewRequest(StrictModel):
    now: datetime | None = None

class CommitRequest(StrictModel):
    preview_id: str

class MoveBlock(StrictModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    is_locked: bool = True
