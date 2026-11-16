import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from app.platforms.douyin.client import DouyinClient

@pytest.mark.parametrize('stage',['create','send'])
@pytest.mark.parametrize('text,phase',[('curl: (28) Resolving timed out token=SECRET','dns'),('curl: (28) Connection timed out ticket=SECRET','connect'),('Read timed out message=SECRET','read'),('Operation timed out cookie=SECRET','unknown')])
def test_timeout_stage_and_redaction(stage,text,phase):
    session=AsyncMock();session.post.side_effect=TimeoutError(text)
    with patch('app.licensing.require_active'),patch('app.platforms.douyin.client.AsyncSession',return_value=session):
        client=DouyinClient('sid_tt=SECRET','Mozilla/5.0')
        call=client.create_dm_conversation('123','456') if stage=='create' else client.send_dm('c','42','SECRET','SECRET')
        _,error=asyncio.run(call)
    assert error.startswith('write_uncertain:imapi_'+stage+':network:TimeoutError')
    assert 'phase='+phase in error and 'SECRET' not in error
    if 'curl:' in text:assert 'curl_errno=28' in error
    assert session.post.await_count==1


def test_native_history_rejects_http200_business_error():
    from app.browser.account_hub import fetch_dm_history
    response=SimpleNamespace(status=200,body=AsyncMock(return_value=b'{"cmd":301,"status_code":1,"error_desc":"private-token"}'))
    context=SimpleNamespace(request=SimpleNamespace(post=AsyncMock(return_value=response)))
    mgr=SimpleNamespace(context_for=AsyncMock(return_value=context))
    with patch('app.licensing.require_active'):
        parsed,error=asyncio.run(fetch_dm_history(mgr,object(),'douyin','c','42'))
    assert parsed=={} and error=='imapi_history_rejected:cmd=301:code=1'
    assert 'private-token' not in error

@pytest.mark.parametrize('stage',['create','send'])
def test_invalid_reply_has_stage_and_safe_metadata(stage):
    response=SimpleNamespace(status_code=200,content=b'<html>SECRET</html>',headers={'content-type':'text/html; secret=SECRET'})
    session=AsyncMock();session.post.return_value=response
    with patch('app.licensing.require_active'),patch('app.platforms.douyin.client.AsyncSession',return_value=session):
        client=DouyinClient('sid_tt=SECRET','Mozilla/5.0')
        call=client.create_dm_conversation('123','456') if stage=='create' else client.send_dm('c','42','SECRET','SECRET')
        _,error=asyncio.run(call)
    assert error.startswith('write_uncertain:imapi_'+stage+':http=200:')
    assert 'ctype=text/html' in error and 'invalid_response' in error
    assert 'SECRET' not in error
    assert session.post.await_count==1
