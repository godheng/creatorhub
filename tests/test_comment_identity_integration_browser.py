"""Browser -> real ASGI validation -> isolated stored comments; no platform reads."""
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from patchright.sync_api import expect
from sqlmodel import select
from app import db, main, douyin_dm_batches as dm
from app.models import DouyinAccount, KeywordCollectionJob, KeywordCollectionContent, KeywordCollectionComment, AccountActionTask
from test_project_optimizations import local_project, store
from test_access_and_submissions import client
from test_web_appearance_browser import ui, capture
pytestmark=pytest.mark.local_cdp

def test_workbench_three_stored_comments_real_asgi(ui, local_project, monkeypatch):
    page, errors=ui
    monkeypatch.setattr(main.licensing,'is_active',lambda:True)
    def no_platform(*args,**kwargs):
        pytest.fail('Local stored identity must never access platform')
    monkeypatch.setattr(dm,'resolve_profile',no_platform)
    monkeypatch.setattr(main,'DouyinClient',no_platform)
    account=store(DouyinAccount(nickname='隔离发送账号'))
    job=store(KeywordCollectionJob(account_id=account))
    content=store(KeywordCollectionContent(job_id=job,aweme_id='fixture-work',desc='真实数据库合成素材'))
    ids=[]
    for i in range(3):
        ids.append(store(KeywordCollectionComment(job_id=job,content_id=content,aweme_id='fixture-work',comment_id=str(i),text='合成评论 '+str(i),user_uid=str(100+i),user_sec_uid='MS4wLjABlocal'+str(i),user_nickname='本地评论者'+str(i))))
    requests=[]
    async def api_call(path,method,body,headers):
        async with client() as http:
            response=await http.request(method,path,json=json.loads(body) if body else None,headers=headers)
            requests.append((path,body,response.status_code,response.json()))
            return {'status':response.status_code,'text':response.text}
    async def real_index():
        async with client() as http:
            return (await http.get('/')).text
    with ThreadPoolExecutor(max_workers=1) as pool:
        html=pool.submit(asyncio.run,real_index()).result()
        version=re.search(r'<meta name="creatorhub-ui-version" content="([^"]+)"',html)[1]
        page.add_script_tag(content="const vmeta=document.createElement('meta');vmeta.name='creatorhub-ui-version';vmeta.content="+json.dumps(version)+";document.head.appendChild(vmeta);")
        page.expose_function('realAsgi',lambda path,method,body,headers:pool.submit(asyncio.run,api_call(path,method,body,headers)).result())
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/web/douyin-dm.js'))
        page.add_script_tag(content="""(() => {
          const old=window.fetch;
          window.fetch=async(url,options={})=>{
            const u=new URL(String(url),location.origin);
            if(u.pathname.startsWith('/api/collections/') || u.pathname==='/api/douyin/dm/preview' || u.pathname==='/api/ui-version') {
              const r=await window.realAsgi(u.pathname+u.search,options.method||'GET',options.body||'',Object.fromEntries(new Headers(options.headers||{})));
              return new Response(r.text,{status:r.status,headers:{'Content-Type':'application/json'}});
            }
            if(u.pathname==='/api/dm/conversations')return new Response('[]',{headers:{'Content-Type':'application/json'}});
            if(u.pathname.startsWith('/api/douyin/dm/batches'))throw Error('Sending forbidden in this test');
            return old(url,options);
          };
          ACCOUNTS=[{id:ACCOUNT,platform:'douyin',status:'active',nickname:'隔离发送账号'}];
          COLLECTION_JOB_ID=JOB;showCollectionComments(CONTENT);
        })();""".replace('ACCOUNT,',str(account)+',').replace('JOB;',str(job)+';').replace('CONTENT)',str(content)+')'))
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'app/web/ui-version.js'))
        modal=page.locator('#collection-comments-modal')
        expect(modal).to_contain_text('本地评论者2')
        page.locator('[data-dm-select-page]').check()
        modal.get_by_role('button',name='批量加入私信名单',exact=True).click()
        modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
        page.locator('#dy-dm-content').fill('仅预览，不发送')
        page.locator('#dy-dm-preview-button').click()
        expect(page.locator('#dy-dm-preview')).to_contain_text('有效 3 人')
        page.locator('#dy-dm-submit').scroll_into_view_if_needed()
        capture(page,'comment-identity-real-asgi-three')
        previews=[r for r in requests if r[0]=='/api/douyin/dm/preview']
        assert len(previews)==1
        assert {r['id'] for r in json.loads(previews[0][1])['comment_refs']}==set(ids)
        assert {r['target_uid'] for r in previews[0][3]['recipients']}=={'100','101','102'}
        with db.get_session() as session:
            assert not session.exec(select(AccountActionTask)).all()
    assert errors==[]
