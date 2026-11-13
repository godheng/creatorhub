"""Commercial controls coexist with the upstream assistant in an offline demo."""
import os
import pytest
from patchright.sync_api import expect
from test_web_appearance_browser import ui
from test_agent_browser import open_agent, capture_agent
pytestmark = pytest.mark.skipif(os.environ.get('CREATORHUB_RUN_LOCAL_CDP') != '1', reason='opt-in UI')

def test_agent_and_commercial_license(ui):
    page, errors = ui
    page.add_script_tag(content="""const previousFetch = window.fetch; window.fetch = (url, ...args) => String(url).startsWith('/api/license') ? Promise.resolve(new Response(JSON.stringify({status:'inactive',valid:false,license_id:'fixture-license',device_name:'隔离测试电脑',device_digest:'fixture-device',reason:'请激活授权'}), {headers:{'Content-Type':'application/json'}})) : previousFetch(url,...args);""")
    expect(page.get_by_role('button', name='授权与激活', exact=True)).to_be_visible()
    open_agent(page, enabled=False)
    expect(page.locator('#agent-dialog')).to_be_visible()
    from app.brand import PRODUCT_NAME
    expect(page.locator('.agent-window-footer')).to_contain_text(PRODUCT_NAME)
    capture_agent(page, 'commercial-ai-disabled')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='授权与激活', exact=True).click()
    expect(page.get_by_text('当前电脑名称：隔离测试电脑', exact=True)).to_be_visible()
    expect(page.get_by_text('授权编号：fixture-license', exact=True)).to_be_visible()
    capture_agent(page, 'commercial-license-entry')
    assert '获客助手' in page.title()
    assert not errors

pytestmark = [pytest.mark.local_cdp, pytestmark]

