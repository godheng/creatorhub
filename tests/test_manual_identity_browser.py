"""Manual identity browser input uses real isolated ASGI preview, never sends."""
import asyncio,json,re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from patchright.sync_api import expect
from app import main,douyin_dm_batches as dm
from app.models import DouyinAccount,DmConversation
from test_project_optimizations import local_project,store
from test_access_and_submissions import client
from test_web_appearance_browser import ui,capture
pytestmark=pytest.mark.local_cdp

def test_workbench_manual_identity(ui,local_project,monkeypatch):
    page,errors=ui
    monkeypatch.setattr(main.licensing,'is_active',lambda:True)
    def forbidden(*args,**kwargs):pytest.fail('Manual identity must not call platform')
    monkeypatch.setattr(dm,'resolve_profile',forbidden)
    account=store(DouyinAccount(nickname='独立测试账号'))
    contact=store(DmConversation(account_id=account,conv_id='fixture',peer_uid='123456',peer_sec_uid='MS4wLjABmanual',peer_nickname='已有联系人'))
    calls=[]
    async def call(path,method,body,headers):
        async with client() as http:
            r=await http.request(method,path,content=body or None,headers=headers)
            calls.append((path,body,r.status_code))
            return {'text':r.text,'status':r.status_code}
    with ThreadPoolExecutor(max_workers=1) as pool:
        html=pool.submit(asyncio.run,call('/','GET','',{})).result()['text']
        version=re.search(r'<meta name="creatorhub-ui-version" content="([^"]+)"',html)[1]
        page.expose_function('manualAsgi',lambda p,m,b,h:pool.submit(asyncio.run,call(p,m,b,h)).result())
        page.add_script_tag(content="const mv=document.createElement('meta');mv.name='creatorhub-ui-version';mv.content="+json.dumps(version)+";document.head.appendChild(mv);")
        page.add_script_tag(content="""(() => {const old=fetch;window.fetch=async(url,options={})=>{
          const u=new URL(String(url),location.origin);
          if(u.pathname.startsWith('/api/douyin/dm/batches')){
            if(options.method==='POST')document.documentElement.dataset.created=options.body;
            return new Response(JSON.stringify({batch_id:'mock-only',total:1,counts:{pending:1},tasks:[]}),{headers:{'Content-Type':'application/json'}});
          }
          if(['/api/ui-version','/api/douyin/dm/preview','/api/dm/conversations'].includes(u.pathname)){
            const r=await manualAsgi(u.pathname+u.search,options.method||'GET',options.body||'',Object.fromEntries(new Headers(options.headers||{})));
            return new Response(r.text,{status:r.status,headers:{'Content-Type':'application/json'}});
          }return old(url,options);
        };ACCOUNTS=[{id:ACCOUNT,platform:'douyin',status:'active',nickname:'独立测试账号'}];})();""".replace('ACCOUNT,',str(account)+','))
        for script in ('ui-version.js','douyin-dm.js'):page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/web'/script))
        page.add_script_tag(content='openDouyinDm(false);')
        page.locator('#dy-dm-content').fill('模拟消息，不真实发送')
        page.locator('#dy-dm-uid').fill('123456')
        page.locator('#dy-dm-preview-button').click()
        expect(page.locator('#dy-dm-error')).to_contain_text('同时填写')
        page.locator('#dy-dm-sec').fill('123456')
        page.locator('#dy-dm-preview-button').click()
        expect(page.locator('#dy-dm-error')).to_contain_text('sec_uid')
        page.locator('#dy-dm-sec').fill('MS4wLjABmanual')
        page.locator('#dy-dm-uid').fill('abc')
        page.locator('#dy-dm-preview-button').click()
        expect(page.locator('#dy-dm-error')).to_contain_text('正整数')
        page.locator('#dy-dm-uid').fill('123456')
        page.locator('#dy-dm-nickname').fill('手工接收人')
        expect(page.locator('#dy-dm-error')).to_have_text('')
        capture(page,'manual-identity-input')
        page.locator('#douyin-dm-dialog').get_by_role('button',name='关闭',exact=True).click()
        page.add_script_tag(content='openDouyinDm(false);')
        expect(page.locator('#dy-dm-uid')).to_have_value('123456')
        expect(page.locator('#dy-dm-sec')).to_have_value('MS4wLjABmanual')
        page.locator('[name="dy-dm-contact"]').check()
        page.locator('#dy-dm-preview-button').click()
        expect(page.locator('#dy-dm-preview')).to_contain_text('有效 1 人')
        expect(page.locator('#dy-dm-preview')).to_contain_text('重复 1 项')
        expect(page.locator('#dy-dm-preview')).to_contain_text('未核验身份绑定')
        page.locator('#dy-dm-uid').fill('123457')
        assert page.locator('#dy-dm-submit').count()==0
        page.locator('#dy-dm-uid').fill('123456')
        page.locator('#dy-dm-preview-button').click()
        page.locator('#dy-dm-submit').scroll_into_view_if_needed()
        capture(page,'manual-identity-confirmation')
        page.locator('#dy-dm-submit').click()
        expect(page.locator('#douyin-dm-dialog')).to_contain_text('mock-only')
        created=json.loads(page.locator('html').get_attribute('data-created'))
        assert len(created['recipients'])==1 and created['recipients'][0]['target_uid']=='123456'
        assert created['recipients'][0]['target_sec_uid']=='MS4wLjABmanual'
        assert all(code==200 for _,_,code in calls)
    assert errors==[]
