from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openpyxl import Workbook
import pytest
from sqlmodel import select

from app import db, licensing, main, runtime
from app.api.douyin_dm_batches import parse_leads
from app.config import EngineConfig
from app.engine.action_jobs import ActionJobMixin
from app.engine.monitor import MonitorEngine
from app.models import AccountActionTask, DouyinAccount, RiskAdminAudit
from app.reporting import build_keyword_collection_report


@pytest.fixture
def account_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_engine", db._engine)
    db.init_db(str(tmp_path / "batches.db"))
    monkeypatch.setattr(licensing, "is_active", lambda: True)
    monkeypatch.delenv("CREATORHUB_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(runtime, "browser", None)
    try:
        with db.get_session() as session:
            account = DouyinAccount(platform="douyin", status="active")
            session.add(account)
            session.commit()
            session.refresh(account)
            result = account.id
        yield result
    finally:
        db._engine.dispose()


def test_existing_collection_export_is_directly_importable():
    data = build_keyword_collection_report(
        {"platform": "douyin", "keywords": '["测试"]'},
        [{"id": 1, "keyword": "测试"}],
        [{"content_id": 1, "user_sec_uid": "MS4w_A", "user_nickname": "示例用户"}],
    )
    assert parse_leads(data)["recipients"] == [{"sec_uid": "MS4w_A", "nickname": "示例用户"}]


@pytest.mark.asyncio
async def test_import_create_and_review_batch(account_id, monkeypatch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用户线索"
    sheet.append(["用户sec_uid", "用户昵称", "评论数"])
    sheet.append(["MS4w_A", "甲", 3])
    sheet.append(["MS4w_A", "甲", 2])
    sheet.append([None, "无 ID", 1])
    sheet.append(["MS4w_B", "乙", 1])
    excel = BytesIO()
    workbook.save(excel)
    workbook.close()

    base = "/api/douyin/dm-batches"
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post(base + "/import", files={"file": ("leads.xlsx", excel.getvalue())})
        assert response.status_code == 200
        imported = response.json()
        assert (imported["duplicates"], imported["skipped"]) == (1, 1)
        assert [row["sec_uid"] for row in imported["recipients"]] == ["MS4w_A", "MS4w_B"]
        with db.get_session() as session:
            assert not session.exec(select(AccountActionTask)).all()

        payload = {"account_id": account_id, "content": "测试私信", "confirmed_eligible": False,
                   "recipients": imported["recipients"] + imported["recipients"][:1]}
        headers = {"Idempotency-Key": "dm-batch-fixture-1"}
        assert (await client.post(base, json=payload, headers=headers)).status_code == 422
        payload["confirmed_eligible"] = True
        response = await client.post(base, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        batch = response.json()
        first, second = batch["tasks"]
        assert batch["counts"]["pending"] == 2
        assert (await client.post(base, json=payload, headers=headers)).json()["id"] == batch["id"]
        assert (await client.post(base, json=payload, headers={
            "Idempotency-Key": "dm-batch-fixture-2",
        })).status_code == 409

        with db.get_session() as session:
            task = session.get(AccountActionTask, first["id"])
            task.status = "uncertain"
            session.add(task)
            session.commit()
        current = (await client.get(base, params={"account_id": account_id})).json()[0]
        assert current["status"] == "paused"
        assert current["counts"]["pending"] == 1
        monkeypatch.setenv("CREATORHUB_ADMIN_TOKEN", "fixture-token")
        review = {"task_id": first["id"], "confirmed_sent": True}
        resolve = f"{base}/{batch['id']}/resolve"
        assert (await client.post(resolve, json=review)).status_code == 403
        resumed = await client.post(resolve, json=review, headers={"X-CreatorHub-Admin-Token": "fixture-token"})
        assert resumed.json()["status"] == "running"
        assert resumed.json()["counts"]["done"] == 1
        canceled = (await client.post(f"{base}/{batch['id']}/cancel")).json()
        assert canceled["tasks"][1]["id"] == second["id"]
        assert canceled["counts"]["done"] == canceled["counts"]["canceled"] == 1
        with db.get_session() as session:
            assert len(session.exec(select(AccountActionTask)).all()) == 2
            assert session.exec(select(RiskAdminAudit)).one().action == "dm_batch_task_resolved"


@pytest.mark.asyncio
async def test_queue_keeps_batch_order_gap_and_pause(account_id):
    engine = ActionJobMixin()
    engine.cfg = SimpleNamespace(engine=EngineConfig())
    engine._today_start = lambda _: datetime.utcnow().replace(hour=0, minute=0, second=0)
    engine._hour_ago = lambda: datetime.utcnow() - timedelta(hours=1)
    engine._task_due = MonitorEngine._task_due
    engine.execute_action_task = AsyncMock()
    with db.get_session() as session:
        rows = [AccountActionTask(account_id=account_id, action="send_dm", dm_batch_id=1,
                                  target_sec_uid=f"MS4w_{index}") for index in range(3)]
        session.add_all(rows)
        session.commit()
        ids = [row.id for row in rows]

    def set_state(index, status, *, done_at=None, next_allowed_at=None):
        with db.get_session() as session:
            row = session.get(AccountActionTask, ids[index])
            row.status, row.done_at, row.next_allowed_at = status, done_at, next_allowed_at
            session.add(row)
            session.commit()

    set_state(0, "pending", next_allowed_at=datetime.utcnow() + timedelta(minutes=1))
    await engine._process_action_tasks()
    engine.execute_action_task.assert_not_called()
    set_state(0, "pending")
    await engine._process_action_tasks()
    engine.execute_action_task.assert_awaited_once_with(ids[0], scheduled=True)
    engine.execute_action_task.reset_mock()
    set_state(0, "done", done_at=datetime.utcnow())
    await engine._process_action_tasks()
    engine.execute_action_task.assert_not_called()
    set_state(0, "done", done_at=datetime.utcnow() - timedelta(seconds=61))
    await engine._process_action_tasks()
    engine.execute_action_task.assert_awaited_once_with(ids[1], scheduled=True)
    engine.execute_action_task.reset_mock()
    set_state(1, "uncertain")
    await engine._process_action_tasks()
    engine.execute_action_task.assert_not_called()
