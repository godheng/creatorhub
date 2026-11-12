"""Submission authorization behavior; fake pages never contact platforms."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app import licensing
from app.browser import fetcher, ks_fetcher

@pytest.mark.parametrize('module,fn,url,payload', [
    (fetcher,fetcher.post_comment_browser,'https://fixture/comment/publish/',{'status_code':0}),
    (ks_fetcher,ks_fetcher.post_ks_comment,'https://fixture/rest/v/photo/comment/add',{'result':1}),
])
@pytest.mark.parametrize('case', ['button_denied','fallback_denied','expires_after_failure','button_allowed','fallback_allowed','expires_after_submit'])
@pytest.mark.asyncio
async def test_comment_submission_gate(monkeypatch,module,fn,url,payload,case):
    active=[case not in ('button_denied','fallback_denied')]
    monkeypatch.setattr(licensing,'is_active',lambda:active[0])
    monkeypatch.setattr(module,'_COMMENT_INPUT',['editor'])
    monkeypatch.setattr(module,'_COMMENT_SUBMIT',['button'])
    if module is fetcher: url='https://fixture'+module._PUBLISH_API
    calls=[]
    async def submit(**kwargs):
        calls.append('click')
        if case=='expires_after_failure':
            active[0]=False
            raise RuntimeError('button unavailable')
        await page.response(SimpleNamespace(url=url,json=AsyncMock(return_value=payload)))
        if case=='expires_after_submit':active[0]=False
    async def press(key):
        calls.append(key)
        await page.response(SimpleNamespace(url=url,json=AsyncMock(return_value=payload)))
    editor=SimpleNamespace(click=AsyncMock(),inner_text=AsyncMock(return_value=''))
    editor.first=editor;editor.count=AsyncMock(return_value=1)
    button=SimpleNamespace(count=AsyncMock(return_value=0 if case.startswith('fallback') else 1),is_enabled=AsyncMock(return_value=True),click=submit)
    button.first=button
    page=SimpleNamespace(url='https://fixture/video',goto=AsyncMock(),wait_for_timeout=AsyncMock(),evaluate=AsyncMock(),wait_for_selector=AsyncMock(),close=AsyncMock(),keyboard=SimpleNamespace(type=AsyncMock(),press=press),locator=lambda selector:editor if selector=='editor' else button)
    page.on=lambda event,handler:setattr(page,'response',handler)
    mgr=SimpleNamespace(new_page=AsyncMock(return_value=page))
    if case in ('button_denied','fallback_denied','expires_after_failure'):
        with pytest.raises(licensing.LicenseRequired):
            await fn(mgr,None,'fixture','test',headed=False)
        assert calls==(['click'] if case=='expires_after_failure' else [])
    else:
        assert await fn(mgr,None,'fixture','test',headed=False)==(True,'')
        assert calls==(['Enter'] if case=='fallback_allowed' else ['click'])
    page.close.assert_awaited_once()

