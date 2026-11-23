"""Commercial controls coexist with the upstream assistant in an offline demo."""
import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest
from patchright.sync_api import expect, sync_playwright

pytestmark = [pytest.mark.local_cdp, pytest.mark.skipif(
    os.environ.get("CREATORHUB_RUN_LOCAL_CDP") != "1", reason="local UI acceptance is opt-in")]


@pytest.fixture
def ui(tmp_path, monkeypatch):
    from preview import build_preview

    shutil.copytree(build_preview.ROOT / "assets" / "screenshots", tmp_path / "assets" / "screenshots")
    monkeypatch.setattr(build_preview, "ROOT", tmp_path)
    site = tmp_path / "site"
    build_preview.build(site)
    static = site / "static"
    static.mkdir()
    shutil.copy2(build_preview.SOURCE / "douyin-dm.js", static)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 960})
                context.route("**/*", lambda route: route.continue_()
                              if urlparse(route.request.url).netloc == urlparse(origin).netloc else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin, wait_until="networkidle")
                page.get_by_role("button", name="关闭新手向导", exact=True).click()
                yield page, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_and_commercial_license(ui):
    page, errors = ui
    page.add_script_tag(content="""const previousFetch = window.fetch; window.fetch = (url, ...args) => String(url).startsWith('/api/license') ? Promise.resolve(new Response(JSON.stringify({status:'inactive',valid:false,license_id:'fixture-license',device_name:'隔离测试电脑',device_digest:'fixture-device',reason:'请激活授权'}), {headers:{'Content-Type':'application/json'}})) : previousFetch(url,...args);""")
    expect(page.get_by_role('button', name='授权与激活', exact=True)).to_be_visible()
    page.get_by_role("button", name="打开全局 AI 助手", exact=True).click()
    expect(page.locator('#agent-dialog')).to_be_visible()
    from app.brand import PRODUCT_NAME
    expect(page.locator('.agent-window-footer')).to_contain_text(PRODUCT_NAME)
    page.keyboard.press('Escape')
    page.get_by_role('button', name='授权与激活', exact=True).click()
    expect(page.get_by_text('当前电脑名称：隔离测试电脑', exact=True)).to_be_visible()
    expect(page.get_by_text('授权编号：fixture-license', exact=True)).to_be_visible()
    assert '获客助手' in page.title()
    assert not errors

