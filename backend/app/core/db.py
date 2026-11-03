import os
from pathlib import Path
from sqlalchemy import create_engine, String, Integer, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase): pass
class TaskRow(Base):
    __tablename__ = 'tasks'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
class EventRow(Base):
    __tablename__ = 'calendar_events'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
class ProjectRow(Base):
    __tablename__ = 'projects'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
class RunRow(Base):
    __tablename__ = 'schedule_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
class StateRow(Base):
    __tablename__ = 'app_state'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON)

path = Path(__file__).resolve().parents[2] / 'flowpilot.db'
engine = create_engine(os.getenv('DATABASE_URL', f'sqlite:///{path}'), connect_args={'check_same_thread': False})
Session = sessionmaker(engine, expire_on_commit=False)

def init_db():
    from app.schemas import Settings
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        if not db.get(StateRow, 1):
            db.add(StateRow(id=1, data={'revision': 0, 'current_run': None, 'undo_run': None, 'settings': Settings().model_dump(mode='json')}))
