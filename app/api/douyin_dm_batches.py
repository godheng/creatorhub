"""Import and track one explicitly confirmed Douyin DM batch."""
from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from typing import Annotated
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from openpyxl import load_workbook
from pydantic import BaseModel, Field, StrictBool, StringConstraints
from sqlalchemy import update
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from app import runtime
from app.db import get_session
from app.models import AccountActionTask, DouyinAccount, DouyinDmBatch, RiskAdminAudit
from app.services.metadata import _require_risk_admin
from app.submissions import submit_once

router = APIRouter(prefix="/api/douyin/dm-batches")
_MAX_RECIPIENTS = 200
_MAX_UPLOAD = 10 * 1024 * 1024
_MAX_WORKBOOK_SIZE = 128 * 1024 * 1024
_STATUSES = ("pending", "doing", "done", "failed", "uncertain", "canceled")


class Recipient(BaseModel):
    sec_uid: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z_0-9-]{1,256}$")]
    nickname: str = Field(default="", max_length=200)


class BatchIn(BaseModel):
    account_id: int
    recipients: list[Recipient] = Field(min_length=1, max_length=_MAX_RECIPIENTS)
    content: str = Field(min_length=1, max_length=500)
    confirmed_eligible: StrictBool


class ResolveIn(BaseModel):
    task_id: int
    confirmed_sent: StrictBool


def parse_leads(data: bytes) -> dict:
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > _MAX_WORKBOOK_SIZE:
                raise HTTPException(413, "Excel 展开后过大，请分批导出")
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except HTTPException:
        raise
    except (BadZipFile, OSError, ValueError, KeyError, SyntaxError) as exc:
        raise HTTPException(422, "无法读取 Excel，请选择关键词采集导出的 .xlsx 文件") from exc
    try:
        if "用户线索" not in workbook.sheetnames:
            raise HTTPException(422, "Excel 缺少「用户线索」工作表")
        rows = workbook["用户线索"].iter_rows(values_only=True)
        headers = tuple(str(value or "").strip() for value in next(rows, ()))
        if "用户sec_uid" not in headers:
            raise HTTPException(422, "「用户线索」表缺少「用户sec_uid」列")
        uid_column = headers.index("用户sec_uid")
        nick_column = headers.index("用户昵称") if "用户昵称" in headers else None
        recipients: dict[str, Recipient] = {}
        duplicates = skipped = 0
        for row in rows:
            if not any(value is not None for value in row):
                continue
            uid = row[uid_column] if uid_column < len(row) else None
            nickname = row[nick_column] if nick_column is not None and nick_column < len(row) else None
            try:
                recipient = Recipient(sec_uid=uid, nickname=str(nickname or "").strip()[:200])
            except ValueError:
                skipped += 1
                continue
            if recipient.sec_uid in recipients:
                duplicates += 1
                continue
            recipients[recipient.sec_uid] = recipient
            if len(recipients) > _MAX_RECIPIENTS:
                raise HTTPException(422, f"每批最多 {_MAX_RECIPIENTS} 个唯一用户，请拆分名单")
        return {"recipients": [row.model_dump() for row in recipients.values()],
                "duplicates": duplicates, "skipped": skipped}
    finally:
        workbook.close()


def _batch(session, batch_id: int) -> DouyinDmBatch:
    batch = session.get(DouyinDmBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "私信批次不存在")
    return batch


def _batch_view(session, batch: DouyinDmBatch) -> dict:
    tasks = session.exec(select(AccountActionTask).where(
        AccountActionTask.dm_batch_id == batch.id,
    ).order_by(AccountActionTask.id)).all()
    counts = dict.fromkeys(_STATUSES, 0)
    result = []
    for task in tasks:
        counts[task.status] += 1
        result.append({"id": task.id, "target_nick": task.target_nick,
                       "target_sec_uid": task.target_sec_uid, "status": task.status,
                       "error": task.error, "next_allowed_at": task.next_allowed_at})
    if counts["uncertain"]:
        status = "paused"
    elif counts["pending"] or counts["doing"]:
        status = "running"
    elif counts["canceled"] and counts["canceled"] == len(tasks):
        status = "canceled"
    else:
        status = "completed"
    return {"id": batch.id, "account_id": batch.account_id, "status": status,
            "counts": counts, "tasks": result, "created_at": batch.created_at}


async def _finish_if_terminal(view: dict) -> None:
    if view["status"] in {"completed", "canceled"} and runtime.browser is not None:
        await runtime.browser.finish_douyin_dm_batch(view["id"])


@router.post("/import")
async def import_recipients(file: UploadFile = File(...)):
    try:
        data = await file.read(_MAX_UPLOAD + 1)
    finally:
        await file.close()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, "Excel 文件不能超过 10 MiB")
    return await run_in_threadpool(parse_leads, data)


@router.post("")
async def create_batch(body: BatchIn, request: Request):
    if not request.headers.get("idempotency-key"):
        raise HTTPException(422, "缺少提交编号，请重新确认发送")
    if not body.confirmed_eligible:
        raise HTTPException(422, "请先确认所选收件人具备触达资格")
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "私信内容不能为空")
    recipients = {}
    for recipient in body.recipients:
        recipients.setdefault(recipient.sec_uid, recipient.nickname)

    with get_session() as session:
        def create():
            account = session.get(DouyinAccount, body.account_id)
            if account is None or account.platform != "douyin":
                raise HTTPException(404, "抖音账号不存在")
            if account.status != "active":
                raise HTTPException(400, "账号登录状态不可用")
            outstanding = session.exec(select(AccountActionTask.id).where(
                AccountActionTask.account_id == account.id,
                AccountActionTask.dm_batch_id.is_not(None),
                AccountActionTask.status.in_(("pending", "doing", "uncertain")),
            ).limit(1)).first()
            if outstanding is not None:
                raise HTTPException(409, "该账号已有未结束的私信批次，请先处理")
            batch = DouyinDmBatch(account_id=body.account_id)
            session.add(batch)
            session.flush()
            session.add_all([
                AccountActionTask(
                    platform="douyin", account_id=body.account_id, action="send_dm",
                    dm_batch_id=batch.id, target_sec_uid=uid, target_nick=nickname,
                    content=content,
                )
                for uid, nickname in recipients.items()
            ])
            return {"id": batch.id}

        receipt, _ = submit_once(session, request=request, scope="douyin-dm-batch", body=body, create=create)
        return _batch_view(session, _batch(session, receipt["id"]))


@router.get("")
async def list_batches(account_id: int):
    with get_session() as session:
        batches = session.exec(select(DouyinDmBatch).where(
            DouyinDmBatch.account_id == account_id,
        ).order_by(DouyinDmBatch.id.desc()).limit(20)).all()
        return [_batch_view(session, batch) for batch in batches]


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: int):
    with get_session() as session:
        batch = _batch(session, batch_id)
        session.exec(update(AccountActionTask).where(
            AccountActionTask.dm_batch_id == batch_id,
            AccountActionTask.status == "pending",
        ).values(status="canceled"))
        session.commit()
        view = _batch_view(session, batch)
    await _finish_if_terminal(view)
    return view


@router.post("/{batch_id}/resolve")
async def resolve_batch_task(batch_id: int, body: ResolveIn, request: Request):
    actor = _require_risk_admin(request)
    with get_session() as session:
        batch = _batch(session, batch_id)
        task = session.get(AccountActionTask, body.task_id)
        if task is None or task.dm_batch_id != batch_id:
            raise HTTPException(404, "该批次下没有这条私信任务")
        if task.status != "uncertain":
            raise HTTPException(409, "该条私信当前无需核对")
        status = "done" if body.confirmed_sent else "failed"
        updated = session.exec(update(AccountActionTask).where(
            AccountActionTask.id == task.id, AccountActionTask.status == "uncertain",
        ).values(status=status, done_at=datetime.utcnow() if body.confirmed_sent else None,
                 error="" if body.confirmed_sent else "人工核对未发送",
                 result="ok" if body.confirmed_sent else ""))
        if updated.rowcount != 1:
            raise HTTPException(409, "任务状态已变化，请刷新后再核对")
        session.add(RiskAdminAudit(action="dm_batch_task_resolved", account_id=batch.account_id, actor=actor,
                                   detail=json.dumps({"batch_id": batch.id, "task_id": task.id,
                                                      "status": status}, ensure_ascii=False)))
        session.commit()
        view = _batch_view(session, batch)
    await _finish_if_terminal(view)
    return view
