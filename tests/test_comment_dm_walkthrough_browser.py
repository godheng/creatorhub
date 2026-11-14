import pytest
pytestmark = pytest.mark.local_cdp
import json
from pathlib import Path
from patchright.sync_api import expect
from test_web_appearance_browser import ui, capture

def test_workbench_comment_dm_walkthrough(ui):
    page, errors = ui
    page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "app/web/douyin-dm.js"))
    page.add_script_tag(content="""(() => {
      window.fixturePosts=[]; window.fixtureDeferred=false; window.fixturePreviewCalls=0;
      const old=window.fetch;
      window.fetch=async (url, options={})=>{
        const path=new URL(String(url),location.origin).pathname;
        const response=v=>Promise.resolve(new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}}));
        if(path.includes('/comments') && path.includes('/collections/')) return response({items:[
          {id:1,user_sec_uid:'MS4wLjABdemoA',user_nickname:'评论者小林',text:'请问怎么体验？',source_desc:'露营装备素材',content_id:7},
          {id:2,user_sec_uid:'MS4wLjABdemoA',user_nickname:'评论者小林',text:'补充咨询',source_desc:'露营装备素材',content_id:7},
          {id:3,user_sec_uid:'MS4wLjABdemoB',user_nickname:'评论者小陈',text:'想了解价格',source_desc:'露营装备素材',content_id:7},
          {id:4,user_sec_uid:'12345',user_nickname:'历史用户',text:'只有数字身份',content_id:7}],total:4,page:1,pages:1});
        if(path==='/api/dm/conversations') return response([]);
        if(path==='/api/douyin/dm/preview') {
          document.documentElement.dataset.previewCalls=++window.fixturePreviewCalls;
          const b=JSON.parse(options.body); if((b.links||[]).includes('invalid')) return response({recipients:[],duplicates:0,invalid:[{input:'invalid',error:'无法解析接收人'}]}); const ids=[...(b.comment_sec_uids||[]),...(b.links||[]).map(x=>x.split('/').pop())];
          if(!window.fixtureDeferred && !b.links.length && ids.length>1) {
            window.fixtureDeferred=true;
            return response({recipients:[{target_uid:ids[0].endsWith('A')?'100':'101',target_sec_uid:ids[0],target_nick:ids[0].endsWith('A')?'评论者小林':'评论者小陈'}],invalid:[],duplicates:0,pending:[{input:ids[1],kind:'comment',reason:'尚未达到该操作最小间隔',next_allowed_at:new Date(Date.now()+1000).toISOString()}]});
          }
          return response({recipients:[...new Set(ids)].map((id,i)=>({target_uid:id.endsWith('A')?'100':'101',target_sec_uid:id,target_nick:id.endsWith('A')?'评论者小林':'评论者小陈'})),duplicates:ids.length-new Set(ids).size,invalid:[]});
        }
        if(path.startsWith('/api/douyin/dm/batches')) {
          if(options.method==='POST') {fixturePosts.push(JSON.parse(options.body)); document.documentElement.dataset.posts=fixturePosts.length; document.documentElement.dataset.lastPost=JSON.stringify(fixturePosts.at(-1));}
          return response({batch_id:'mock-only',total:2,counts:{pending:2},tasks:[]});
        }
        return old(url,options);
      };
      COLLECTION_JOB_ID=1;
      showCollectionComments(7);
    })();""")
    modal=page.locator('#collection-comments-modal')
    expect(modal).to_be_visible()
    expect(modal).to_contain_text('历史用户')
    missing=modal.locator('.collection-comment').filter(has_text='历史用户')
    assert missing.locator('[data-dm-add],a').count()==0
    page.locator('#collection-comments-q').fill('体验')
    page.wait_for_timeout(500)
    modal.get_by_role('button',name='批量加入私信名单',exact=True).click()
    expect(modal).to_contain_text('请先选择')
    page.locator('[data-dm-add="MS4wLjABdemoA"]').first.click()
    expect(page.locator('[data-dm-add="MS4wLjABdemoA"]').last).to_have_text('已加入')
    page.locator('[data-dm-select="MS4wLjABdemoA"]').first.check()
    page_control=page.locator('[data-dm-select-page]')
    assert page_control.evaluate('(e)=>e.indeterminate')
    page_control.check()
    expect(page_control).to_be_checked()
    assert page.locator('[data-dm-select]:checked').count()==3
    expect(modal.locator('[data-dm-selected]')).to_have_text('已选 2 人')
    # A selection from another page survives clearing/reselecting the current page.
    page.add_script_tag(content="dyDmSelected.set('MS4wLjABotherPage',{user_sec_uid:'MS4wLjABotherPage'});dyDmSyncComments()")
    modal.get_by_role('button',name='取消本页选择',exact=True).click()
    assert page.locator('[data-dm-select]:checked').count()==0
    expect(modal.locator('[data-dm-selected]')).to_have_text('已选 1 人')
    page_control.check()
    expect(modal.locator('[data-dm-selected]')).to_have_text('已选 3 人')
    page.add_script_tag(content="dyDmSelected.delete('MS4wLjABotherPage');dyDmSyncComments()")
    capture(page,'comment-dm-select-all')
    modal.get_by_role('button',name='批量加入私信名单',exact=True).click()
    expect(modal).to_contain_text('重复 1 人')
    assert page.locator('html').get_attribute('data-posts') in (None, '0')
    capture(page,'comment-dm-selection')
    modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
    dialog=page.locator('#douyin-dm-dialog')
    expect(dialog).to_contain_text('露营装备素材')
    capture(page,'comment-dm-list')
    dialog.get_by_role('button',name='移除',exact=True).first.click()
    dialog.get_by_role('button',name='返回评论',exact=True).click()
    expect(page.locator('[data-dm-add="MS4wLjABdemoA"]').first).to_have_text('加入私信名单')
    page.locator('[data-dm-add="MS4wLjABdemoA"]').first.click()
    modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-error')).to_contain_text('请填写')
    page.locator('#dy-dm-content').fill('您好，您咨询的体验信息已整理好。此为模拟消息。')
    dialog.get_by_role('button',name='返回评论',exact=True).click()
    expect(modal).to_be_visible()
    expect(page.locator('#collection-comments-q')).to_have_value('体验')
    modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
    expect(page.locator('#dy-dm-content')).not_to_be_empty()
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-error')).to_contain_text('等待')
    calls=page.locator('html').get_attribute('data-preview-calls')
    dialog.get_by_role('button',name='关闭',exact=True).click()
    page.wait_for_timeout(1300)
    assert page.locator('html').get_attribute('data-preview-calls')==calls
    assert page.locator('html').get_attribute('data-posts') in (None,'0')
    page.add_script_tag(content='window.fixtureDeferred=false;')
    modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-error')).to_contain_text('等待')
    assert page.locator('#dy-dm-submit').count()==0
    capture(page,'comment-dm-risk-waiting')
    expect(page.locator('#dy-dm-preview')).to_contain_text('有效 2 人', timeout=5000)
    page.locator('#dy-dm-links').fill('invalid')
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-preview')).to_contain_text('无法解析接收人')
    page.locator('#dy-dm-links').fill('https://www.douyin.com/user/MS4wLjABdemoA')
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-preview')).to_contain_text('有效 2 人')
    expect(page.locator('#dy-dm-preview')).to_contain_text('重复 1 项')
    page.locator('#dy-dm-submit').scroll_into_view_if_needed()
    capture(page,'comment-dm-confirmation')
    assert page.locator('html').get_attribute('data-posts') in (None, '0')
    page.locator('#dy-dm-submit').click()
    expect(dialog).to_contain_text('mock-only')
    assert page.locator('html').get_attribute('data-posts') == '1'
    posted=json.loads(page.locator('html').get_attribute('data-last-post'))
    assert {(r['target_sec_uid'],r['target_nick']) for r in posted['recipients']} == {('MS4wLjABdemoA','评论者小林'),('MS4wLjABdemoB','评论者小陈')}
    dialog.get_by_role('button',name='关闭',exact=True).click()
    modal.get_by_role('button',name='查看名单 / 去编辑私信',exact=False).click()
    while dialog.get_by_role('button',name='移除',exact=True).count():
        dialog.get_by_role('button',name='移除',exact=True).first.click()
    expect(dialog).to_contain_text('私信名单为空')
    page.locator('#dy-dm-links').fill('')
    page.locator('#dy-dm-preview-button').click()
    expect(page.locator('#dy-dm-preview')).to_contain_text('有效 0 人')
    expect(page.locator('#dy-dm-submit')).to_be_disabled()
    dialog.get_by_role('button',name='返回评论',exact=True).click()
    # Empty and invalid-only pages disable select-all without discarding other-page users.
    for rows in ([], [{'user_sec_uid':'12345'}, {'user_sec_uid':''}]):
        page.add_script_tag(content="dyDmCommentRows="+json.dumps(rows)+";document.querySelector('#collection-comments-list').innerHTML=dyDmCommentToolbar()+dyDmCommentRows.map(collectionCommentMarkup).join('');dyDmSyncComments()")
        expect(page.locator('[data-dm-select-page]')).to_be_disabled()
        expect(page.locator('[data-dm-select-page]')).not_to_be_checked()
        assert not page.locator('[data-dm-select-page]').evaluate('(e)=>e.indeterminate')
        expect(modal.locator('[data-dm-selected]')).to_have_text('已选 2 人')
    assert errors==[]






