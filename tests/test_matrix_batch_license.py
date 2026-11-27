import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest

from app import licensing, main, runtime
from app.api import accounts, matrix, proxies


@pytest.mark.asyncio
@pytest.mark.parametrize("expires", [True, False], ids=["expires-in-queue", "stays-active"])
@pytest.mark.parametrize("action,module,operation", [
    ("delete_accounts", accounts, "del_account"),
    ("refresh_profile", accounts, "refresh_account_profile"),
    ("test_proxy", proxies, "test_account_proxy"),
])
async def test_matrix_rechecks_queued_account(monkeypatch, expires, action, module, operation):
    ids = [11, 22]
    session = Mock()
    session.exec.return_value.all.return_value = [
        SimpleNamespace(id=aid, alias=f"test-{aid}") for aid in ids
    ]
    monkeypatch.setattr(matrix, "get_session", lambda: nullcontext(session))
    monkeypatch.setattr(runtime, "_matrix_batch_jobs", {})
    monkeypatch.setattr(runtime, "_matrix_batch_tasks", {})
    progress = Mock()
    monkeypatch.setattr(matrix, "progress_hub", progress)
    active = True
    monkeypatch.setattr(licensing, "is_active", lambda: active)
    release_first = asyncio.Event()

    async def perform(aid):
        nonlocal active
        if aid == ids[0]:
            await release_first.wait()
            active = not expires
        return {"ok": True}

    execute = AsyncMock(side_effect=perform)
    monkeypatch.setattr(module, operation, execute)
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.post("/api/matrix/batch-jobs", json={
            "action": action, "account_ids": ids, "concurrency": 1,
        })
        assert response.status_code == 202
        job_id = response.json()["id"]
        worker = runtime._matrix_batch_tasks[job_id]
        release_first.set()
        await asyncio.wait_for(worker, timeout=5)
        result = (await client.get(f"/api/matrix/batch-jobs/{job_id}")).json()

    performed = ids[:1] if expires else ids
    skipped = ids[1:] if expires else []
    assert execute.await_args_list == [call(aid) for aid in performed]
    assert result["status"] == "completed" and result["done"] == result["total"] == 2
    assert result["ok_count"] == len(performed) and result["fail_count"] == len(skipped)
    assert result["deleted_ids"] == (performed if action == "delete_accounts" else [])
    assert result["failed_ids"] == result["license_skipped_ids"] == skipped
    final_progress = progress.upsert.call_args.kwargs
    assert final_progress["percent"] == 100 and final_progress["extra"]["license_skipped_ids"] == skipped
    if expires:
        assert "test-22" in result["failed_samples"][0] and "授权" in result["failed_samples"][0]
