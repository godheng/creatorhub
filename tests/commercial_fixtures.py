"""Shared synthetic fixtures for commercial API regressions."""
import asyncio
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import db
from app.config import Config


class BrowserStub:
    def __init__(self):
        self.locks = {}
        self.close_context = AsyncMock()

    def lock_for(self, key):
        return self.locks.setdefault(key, asyncio.Lock())

    def identity_for(self, _account):
        return None

    def anon_identity(self):
        return None


@pytest.fixture(scope="session")
def empty_schema(tmp_path_factory):
    path = tmp_path_factory.mktemp("commercial-schema") / "empty.db"
    previous_engine = db._engine
    try:
        db.init_db(str(path))
    finally:
        if db._engine is not previous_engine and db._engine is not None:
            db._engine.dispose()
        db._engine = previous_engine
    return path


@pytest.fixture
def local_project(tmp_path, monkeypatch, empty_schema):
    from app import runtime

    previous_engine = db._engine
    db_path = tmp_path / "commercial.db"
    shutil.copyfile(empty_schema, db_path)
    db.init_db(str(db_path))
    cfg = Config()
    cfg.engine.profiles_dir = str(tmp_path / "profiles")
    cfg.engine.media_dir = str(tmp_path / "media")
    browser = BrowserStub()
    automation = SimpleNamespace(stop_account=AsyncMock())
    monkeypatch.setattr(runtime, "cfg", cfg)
    monkeypatch.setattr(runtime, "browser", browser)
    monkeypatch.setattr(runtime, "engine", SimpleNamespace(dm_automation=automation))
    monkeypatch.setattr(runtime, "im_receiver", SimpleNamespace(stop_account=AsyncMock()))
    monkeypatch.setattr(runtime, "login_tasks", {})
    monkeypatch.setattr(runtime, "open_browsers", {})
    monkeypatch.delenv("CREATORHUB_ADMIN_TOKEN", raising=False)
    try:
        yield SimpleNamespace(path=db_path, root=tmp_path, cfg=cfg, browser=browser)
    finally:
        db._engine.dispose()
        db._engine = previous_engine


def client(*, peer="127.0.0.1", base="http://127.0.0.1"):
    from app import main

    transport = httpx.ASGITransport(app=main.app, client=(peer, 1234))
    return httpx.AsyncClient(transport=transport, base_url=base)


def store(row):
    with db.get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id
