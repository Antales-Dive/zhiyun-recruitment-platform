"""任务域单元测试公共夹具。"""
import pytest
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()
