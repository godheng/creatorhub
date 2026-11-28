from unittest.mock import AsyncMock, Mock, call

import pytest
from patchright.async_api import TimeoutError as BrowserTimeoutError

from app.browser import hub_actions


@pytest.mark.asyncio
@pytest.mark.parametrize("editor_appears", [True, False], ids=["delayed-editor", "search-only"])
async def test_douyin_browser_dm_waits_for_the_message_editor(monkeypatch, editor_appears):
    entry = Mock(count=AsyncMock(return_value=1))
    editor = Mock()
    search = Mock(count=AsyncMock(return_value=1), is_visible=AsyncMock(return_value=True))
    send_button = Mock(click=AsyncMock())
    message = "私信测试文案"
    click = AsyncMock()

    async def wait_for_editor(*, state, timeout):
        click.assert_awaited_once_with(entry, page=page, timeout=20000)
        assert state == "visible" and timeout == 15000
        editor.fill.assert_not_called()
        if not editor_appears:
            raise BrowserTimeoutError("editor missing")

    editor.wait_for = AsyncMock(side_effect=wait_for_editor)
    editor.fill = AsyncMock()
    submit = AsyncMock()
    page = Mock(
        url="https://www.douyin.com/user/fixture",
        goto=AsyncMock(),
        close=AsyncMock(),
        is_closed=Mock(return_value=False),
    )
    page.get_by_text.return_value.first = entry
    response = Mock(status=200, body=AsyncMock(return_value=b'{"cmd":100,"status_code":0}'))
    request = Mock(response=AsyncMock(return_value=response))

    async def captured_request():
        page.close.assert_not_called()
        submit.assert_awaited_once()
        return request

    async def enter_request_wait():
        submit.assert_not_called()
        return Mock(value=captured_request())

    request_wait = Mock(__aenter__=AsyncMock(side_effect=enter_request_wait),
                        __aexit__=AsyncMock(return_value=False))
    page.expect_request.return_value = request_wait

    def locate(selector):
        if selector == (
            '.messageEditorinputArea[data-slate-editor="true"]'
            '[contenteditable="true"][data-placeholder="发送消息"]:visible'
        ):
            return editor
        if selector == (
            ".messageMsgInputinputRow .messageMsgInputpopupInputAction "
            "svg.e2e-send-msg-btn.messageMsgInputpublishRedBtn:visible"
        ):
            return send_button
        return Mock(first=search)

    page.locator.side_effect = locate
    manager = Mock(open_headed=AsyncMock(return_value=Mock(new_page=AsyncMock(return_value=page))))
    human = Mock(click_locator=click, type_text=AsyncMock())
    monkeypatch.setattr(hub_actions, "DEFAULT_HUMAN_ENGINE", human)
    monkeypatch.setattr(hub_actions, "human_wait", AsyncMock())
    monkeypatch.setattr(hub_actions, "_notify_submit_hook", submit)

    ok, error = await hub_actions.send_dm(manager, object(), "douyin", target_sec_uid="fixture", text=message)

    editor.wait_for.assert_awaited_once()
    human.type_text.assert_not_called()
    search.count.assert_not_called()
    if editor_appears:
        assert ok and not error
        editor.fill.assert_awaited_once_with(message, timeout=8000)
        click.assert_awaited_once_with(entry, page=page, timeout=20000)
        assert send_button.click.await_args_list == [call(trial=True, timeout=10000), call(timeout=3000)]
        submit.assert_awaited_once()
        page.close.assert_awaited_once()
    else:
        assert not ok and "输入框超时" in error
        editor.fill.assert_not_called()
        click.assert_awaited_once()
        submit.assert_not_called()
        page.expect_request.assert_not_called()
        send_button.click.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["no-button", "no-request", "business-rejected", "no-response"])
async def test_douyin_browser_dm_keeps_page_open_without_send_receipt(monkeypatch, result):
    editor = Mock(wait_for=AsyncMock(), fill=AsyncMock(), press=AsyncMock())
    button = Mock(click=AsyncMock(
        side_effect=BrowserTimeoutError("button missing") if result == "no-button" else None,
    ))
    page = Mock(url="https://www.douyin.com/user/fixture", goto=AsyncMock(),
                close=AsyncMock(), is_closed=Mock(return_value=False))
    page.get_by_text.return_value.first.count = AsyncMock(return_value=0)
    page.locator.side_effect = lambda selector: (
        editor if selector == hub_actions._DOUYIN_DM_EDITOR else button
    )
    submit = AsyncMock()
    response = Mock(status=200, body=AsyncMock(return_value=b'{"cmd":100,"status_code":1}'))
    request = Mock(response=AsyncMock(return_value=None if result == "no-response" else response))

    async def captured_request():
        page.close.assert_not_called()
        assert button.click.await_args_list == [call(trial=True, timeout=10000), call(timeout=3000)]
        if result == "no-request":
            raise BrowserTimeoutError("no request")
        return request

    async def enter_request_wait():
        return Mock(value=captured_request())

    page.expect_request.return_value = Mock(
        __aenter__=AsyncMock(side_effect=enter_request_wait), __aexit__=AsyncMock(return_value=False),
    )
    manager = Mock(open_headed=AsyncMock(return_value=Mock(new_page=AsyncMock(return_value=page))))
    monkeypatch.setattr(hub_actions, "human_wait", AsyncMock())
    monkeypatch.setattr(hub_actions, "_notify_submit_hook", submit)

    ok, error = await hub_actions.send_dm(manager, object(), "douyin", target_sec_uid="fixture", text="测试")

    assert not ok
    if result == "no-button":
        assert "未点击发送" in error
        button.click.assert_awaited_once_with(trial=True, timeout=10000)
        page.expect_request.assert_not_called()
        submit.assert_not_called()
    else:
        assert error.startswith("write_uncertain:")
        submit.assert_awaited_once()
    editor.press.assert_not_called()
    page.close.assert_not_called()


def test_douyin_dm_request_matches_only_send_post():
    url = "https://imapi.douyin.com/v1/message/send"
    assert hub_actions._is_douyin_dm_send_request(Mock(method="POST", url=url + "?test=1"))
    assert not hub_actions._is_douyin_dm_send_request(Mock(method="GET", url=url))
    assert not hub_actions._is_douyin_dm_send_request(Mock(method="POST", url=url + "_other"))
