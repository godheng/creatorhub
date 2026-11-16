"""Real ASGI version changes with a retained browser draft; no task execution."""
import asyncio
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from patchright.sync_api import expect
from app import main
from app.ui_version import ui_manifest
from test_project_optimizations import local_project
from test_access_and_submissions import client
from test_web_appearance_browser import ui, capture
pytestmark=pytest.mark.local_cdp

def test_workbench_update_preserves_draft_and_blocks_submission(ui,local_project,monkeypatch,tmp_path):
    page,errors=ui
    web=tmp_path/'version-web'
    shutil.copytree(main.WEB_DIR,web)
    monkeypatch.setattr(main,'WEB_DIR',web)
    original=ui_manifest(web)
    seen=[]
    async def call(path,method,headers,body):
        async with client() as http:
            response=await http.request(method,path,headers=headers,content=body or None)
            seen.append((path,method,response.status_code))
            return {'status':response.status_code,'text':response.text,'headers':dict(response.headers)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        page.expose_function('versionAsgi',lambda path,method,headers,body:pool.submit(asyncio.run,call(path,method,headers,body)).result())
        page.add_script_tag(content="""(() => {
          const old=fetch;
          window.fetch=async(path,options={})=>{
            if(String(path).startsWith('/api/ui-version')||String(path).startsWith('/api/douyin/dm/batches')){
              const r=await versionAsgi(String(path),options.method||'GET',Object.fromEntries(new Headers(options.headers)),options.body||'');
              return new Response(r.text,{status:r.status,headers:r.headers});
            } return old(path,options);
          };
          const meta=document.createElement('meta');meta.name='creatorhub-ui-version';meta.content='VERSION';document.head.appendChild(meta);
          const draft=document.createElement('textarea');draft.id='retained-test-draft';draft.value='保留评论私信草稿';document.body.appendChild(draft);
        })();""".replace('VERSION',original['version']))
        page.add_script_tag(path=str(web/'douyin-dm.js'))
        page.add_script_tag(content="dyDmDraft.set('MS4wLjABsaved',{id:1,job_id:1,user_sec_uid:'MS4wLjABsaved',user_nickname:'合成评论者',text:'升级前保留的评论'});openDouyinDm(true);")
        page.locator('#dy-dm-content').fill('保留评论私信草稿')
        page.add_script_tag(path=str(web/'ui-version.js'))
        page.add_script_tag(content="CreatorHubUiVersion.check().then(v=>document.documentElement.dataset.sameVersion=String(v));")
        expect(page.locator('html')).to_have_attribute('data-same-version','true')
        with (web/'douyin-dm.js').open('a',encoding='utf-8') as f:f.write('\n// isolated upgrade fixture\n')
        updated=ui_manifest(web)
        assert original['version']!=updated['version']
        assert original['assets']['douyin-dm.js']!=updated['assets']['douyin-dm.js']
        page.add_script_tag(content="CreatorHubUiVersion.check();")
        expect(page.locator('dialog[open] #client-update-notice')).to_be_visible()
        assert page.locator('#client-update-notice').evaluate('(e)=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)===e}')
        expect(page.locator('#retained-test-draft')).to_have_value('保留评论私信草稿')
        expect(page.locator('#dy-dm-content')).to_have_value('保留评论私信草稿')
        expect(page.locator('#dy-dm-draft')).to_contain_text('合成评论者')
        capture(page,'client-update-draft-preserved')
        page.add_script_tag(content="CreatorHubUiVersion.send('/api/douyin/dm/batches',{method:'POST',body:'{}'}).catch(e=>document.documentElement.dataset.blocked=e.message);")
        expect(page.locator('html')).to_have_attribute('data-blocked',__import__('re').compile('.*刷新.*'))
        assert not any(path.endswith('/batches') for path,_,_ in seen)
        rejected=pool.submit(asyncio.run,call('/api/douyin/dm/batches','POST',{'X-CreatorHub-UI-Version':original['version']},'{}')).result()
        assert rejected['status']==409 and rejected['headers']['x-creatorhub-ui-stale']=='1'
        html=pool.submit(asyncio.run,call('/','GET',{},'')).result()['text']
        assert '/static/douyin-dm.js?v='+updated['assets']['douyin-dm.js'] in html
        # User refresh retrieves the real new ASGI HTML, without auto-reloading the draft.
        page.route(page.url.split('#')[0],lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('meta[name="creatorhub-ui-version"]')).to_have_attribute('content',updated['version'])
        expect(page.locator('#client-update-notice')).to_have_count(0)
    assert errors==[]

