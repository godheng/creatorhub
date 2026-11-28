from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from patchright.async_api import TimeoutError as BrowserTimeoutError

from app.browser import hub_actions
from app.browser.identity import Identity
from app.browser.manager import BrowserManager


@pytest.mark.asyncio
@pytest.mark.parametrize("second_has_receipt", [True, False])
async def test_batch_reuses_headed_tab_and_releases_only_owned_page(
    monkeypatch, tmp_path, second_has_receipt
):
    identity = Identity(account_id=7, profile_dir=str(tmp_path), platform="douyin")
    manager = BrowserManager(default_ua="", profiles_root=str(tmp_path))
    user_tab = Mock(url="https://example.invalid/", is_closed=Mock(return_value=False))
    page = Mock(
        url="https://www.douyin.com/user/fixture",
        goto=AsyncMock(),
        close=AsyncMock(),
        is_closed=Mock(return_value=False),
    )
    context = Mock(new_page=AsyncMock(return_value=page), pages=[user_tab, page], close=AsyncMock())
    page.context = context

    async def goto(url, **_):
        page.url = url

    page.goto.side_effect = goto
    editor = Mock(wait_for=AsyncMock(), fill=AsyncMock())
    button = Mock(click=AsyncMock())
    entry = Mock(wait_for=AsyncMock(), click=AsyncMock())
    page.get_by_text.return_value.filter.return_value = entry
    page.locator.side_effect = lambda selector: editor if selector == hub_actions._DOUYIN_DM_EDITOR else button

    async def open_headed(current_identity):
        manager._contexts[current_identity.key] = context
        backend = manager.effective_browser_backend(current_identity)
        manager._proxy_signature_by_key[current_identity.key] = manager._context_signature(
            current_identity, backend, "", "direct")
        return context

    monkeypatch.setattr(manager, "open_headed", AsyncMock(side_effect=open_headed))
    monkeypatch.setattr(hub_actions, "human_wait", AsyncMock())
    monkeypatch.setattr(hub_actions.DEFAULT_HUMAN_ENGINE, "click_locator", AsyncMock())
    monkeypatch.setattr(hub_actions, "_notify_submit_hook", AsyncMock())

    responses = iter([True, second_has_receipt])

    async def observed_request():
        body = b'{"cmd":100,"status_code":0}' if next(responses) else b'{"cmd":100,"status_code":1}'
        return Mock(response=AsyncMock(return_value=Mock(status=200, body=AsyncMock(return_value=body))))

    async def request_info():
        return SimpleNamespace(value=observed_request())

    page.expect_request.return_value = Mock(
        __aenter__=AsyncMock(side_effect=request_info), __aexit__=AsyncMock(return_value=False))

    async with manager.lock_for(identity.key):
        first = await hub_actions.send_dm(
            manager, identity, "douyin", target_sec_uid="first", text="fixture", batch_id=1)
        second = await hub_actions.send_dm(
            manager, identity, "douyin", target_sec_uid="second", text="fixture", batch_id=1)
        assert first == (True, "")
        assert second[0] is second_has_receipt
        if not second_has_receipt:
            assert second[1].startswith("write_uncertain:")
        manager.open_headed.assert_awaited_once_with(identity)
        context.new_page.assert_awaited_once()
        assert page.goto.await_args_list == [
            call("https://www.douyin.com/user/first", wait_until="domcontentloaded", timeout=30000),
            call("https://www.douyin.com/user/second", wait_until="domcontentloaded", timeout=30000),
        ]
        assert entry.wait_for.await_args_list == [call(state="visible", timeout=30000)] * 2
        assert entry.click.await_args_list == [call(timeout=20000)] * 2
        page.close.assert_not_called()
        await manager.finish_douyin_dm_batch(1)

    page.close.assert_awaited_once()
    user_tab.close.assert_not_called()
    context.close.assert_not_called()


@pytest.mark.asyncio
async def test_batch_stops_before_input_if_the_profile_redirects(monkeypatch):
    page = Mock(
        url="https://www.douyin.com/user/another-user",
        goto=AsyncMock(),
        close=AsyncMock(),
    )
    manager = Mock(douyin_dm_batch_page=AsyncMock(return_value=page))
    monkeypatch.setattr(hub_actions, "human_wait", AsyncMock())

    ok, reason = await hub_actions.send_dm(
        manager, object(), "douyin", target_sec_uid="expected", text="fixture", batch_id=1)

    assert not ok
    assert reason == "未进入当前收件人的抖音主页，未发送"
    page.get_by_text.assert_not_called()
    page.locator.assert_not_called()
    page.expect_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_visible", [True, False])
async def test_batch_reports_entry_lookup_and_click_failures_separately(monkeypatch, entry_visible):
    entry = Mock(
        wait_for=AsyncMock(side_effect=None if entry_visible else BrowserTimeoutError("entry missing")),
        click=AsyncMock(side_effect=BrowserTimeoutError("entry obstructed")),
    )
    page = Mock(url="https://www.douyin.com/user/fixture", goto=AsyncMock(), close=AsyncMock())
    page.get_by_text.return_value.filter.return_value = entry
    manager = Mock(douyin_dm_batch_page=AsyncMock(return_value=page))
    monkeypatch.setattr(hub_actions, "human_wait", AsyncMock())

    ok, reason = await hub_actions.send_dm(
        manager, object(), "douyin", target_sec_uid="fixture", text="fixture", batch_id=1)

    assert not ok
    entry.wait_for.assert_awaited_once_with(state="visible", timeout=30000)
    page.get_by_text.return_value.filter.assert_called_once_with(visible=True)
    if entry_visible:
        entry.click.assert_awaited_once_with(timeout=20000)
        assert "入口，但点击失败" in reason
    else:
        entry.click.assert_not_called()
        assert "等待私信入口超时（30 秒）" in reason
    page.locator.assert_not_called()
    page.expect_request.assert_not_called()
