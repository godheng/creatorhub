from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import licensing

router = APIRouter()


class Activation(BaseModel):
    code: str


async def commercial_license_error(request: Request, exc: licensing.LicenseRequired):
    return JSONResponse(status_code=403, content={"detail": "授权不可用：" + str(exc), "code": "license_required"})


async def commercial_license_gate(request: Request, call_next):
    path = request.url.path
    local_action = path.endswith(("/cancel", "/reveal", "/open", "/mark-read")) or path == "/api/task-queue/batch-stop"
    remote_read = path.endswith("/xhs/favorite-boards") or path in {
        "/api/publish/published", "/api/publish/note-media", "/api/publish/note-comments", "/api/dm/stream"
    }
    license_endpoint = path in {"/api/license", "/api/license/activate", "/api/license/revalidate"}
    protected = (path.startswith("/api/") and not license_endpoint and not local_action
                 and (request.method not in {"GET", "HEAD", "OPTIONS"} or remote_read))
    if protected and not licensing.is_active():
        return JSONResponse(status_code=403, content={"detail": "授权未激活或已到期，请激活或续期", "code": "license_required"})
    return await call_next(request)


@router.get("/api/license")
def commercial_license_status():
    return licensing.status()


@router.post("/api/license/activate")
def commercial_license_activate(payload: Activation):
    try:
        return licensing.activate(payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/license/revalidate")
def commercial_license_revalidate():
    return licensing.revalidate()
