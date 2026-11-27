"""Exercise the ASGI lifespan and commercial gates with a signed, local test issuer."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_activation_expiry_and_process_restart(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "CREATORHUB_CONFIG_PATH": str(tmp_path / "config.yaml")}
    (tmp_path / "config.yaml").write_bytes((root / "config.example.yaml").read_bytes())
    for phase in ("activation", "cached", "expired"):
        subprocess.run([sys.executable, __file__, phase], cwd=tmp_path, env=env, check=True, timeout=60)


async def exercise(phase):
    from app import db, licensing, main, runtime
    from app.browser import BrowserManager
    from app.models import PublishTask
    from unittest.mock import AsyncMock, patch

    # Only the issuer and elapsed time are simulated. The client uses its normal
    # device identifier and persists credentials through the application's SQLite settings.
    key = Ed25519PrivateKey.generate()
    tick = [100]
    calls = []

    def signed_reply(path, body):
        calls.append(path)
        if path.endswith("/activate"):
            assert body["code"] == "fixture-code" and phase == "activation"
        credential = body.get("credential")
        valid = phase != "expired" and (credential == "fixture-token" or path.endswith("/activate"))
        status = "active" if valid else "expired" if credential else "inactive"
        fields = dict(protocol=1, nonce=body["nonce"], device=body["device"], status=status,
                      server_time=1000, expires_at=1010, valid_until=1010, binding_version=1,
                      license_id="fixture", credential="fixture-token")
        raw = json.dumps(fields).encode()
        return {name: base64.urlsafe_b64encode(value).decode().rstrip("=")
                for name, value in (("payload", raw), ("signature", key.sign(raw)))}

    licensing._client = licensing.LicenseClient(timer=lambda: (tick[0], tick[0]),
                                                request=signed_reply, public_key=key.public_key())
    with patch.object(BrowserManager, "start", AsyncMock()), patch.object(BrowserManager, "stop", AsyncMock()):
        async with main.app.router.lifespan_context(main.app):
            transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 3001))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                async def check(active):
                    assert (await client.get("/api/license")).json()["valid"] is active
                    response = await client.put("/api/settings", json={})
                    assert response.status_code == (200 if active else 403), response.text
                    if not active:
                        assert response.json()["code"] == "license_required"
                        assert (await client.get("/api/publish/published")).status_code == 403

                if phase == "activation":
                    await check(False)
                    assert (await client.post("/api/license/activate", json={"code": "fixture-code"})).json()["valid"]
                    await check(True)
                elif phase == "cached":
                    await check(True)
                    assert calls and all(path == "/v1/check" for path in calls)
                    tick[0] += 11
                    await check(False)
                    assert (await client.get("/api/license")).json()["status"] == "expired"
                    with db.get_session() as session:
                        session.add(PublishTask(title="expiry regression"))
                        session.commit()
                else:
                    await check(False)
                    assert calls and all(path == "/v1/check" for path in calls)
                    assert (await client.get("/api/license")).json()["status"] == "expired"
                if phase in {"cached", "expired"}:
                    result = await runtime.engine.publish_task(1, scheduled=True)
                    assert result["deferred"] and result["error"] == "license_required"
                    await runtime.engine._process_publish()
                    with db.get_session() as session:
                        assert session.get(PublishTask, 1).status == "pending"


if __name__ == "__main__":
    asyncio.run(exercise(sys.argv[1]))
