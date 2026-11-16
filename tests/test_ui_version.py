import asyncio
import os
from app import main
from app.ui_version import ui_manifest,version_html
from test_project_optimizations import local_project
from test_access_and_submissions import client


def test_content_hash_changes_not_timestamp(tmp_path):
    (tmp_path/'index.html').write_text('<html><head></head><script src="/static/douyin-dm.js"></script><link href="/static/a.css"></html>')
    (tmp_path/'douyin-dm.js').write_text('old')
    (tmp_path/'a.css').write_text('body{}')
    first=ui_manifest(tmp_path)
    os.utime(tmp_path/'douyin-dm.js',(123,123))
    assert ui_manifest(tmp_path)==first
    (tmp_path/'douyin-dm.js').write_text('new')
    second=ui_manifest(tmp_path)
    assert second['version']!=first['version']
    assert second['assets']['douyin-dm.js']!=first['assets']['douyin-dm.js']
    assert second['assets']['a.css']==first['assets']['a.css']
    html=version_html((tmp_path/'index.html').read_text(),second)
    assert '/static/douyin-dm.js?v='+second['assets']['douyin-dm.js'] in html
    assert 'creatorhub-ui-version' in html


def test_ui_mutation_version_gate(local_project,monkeypatch):
    monkeypatch.setattr(main.licensing,"is_active",lambda:True)
    async def run():
        async with client() as http:
            version=(await http.get('/api/ui-version')).json()['version']
            # Unknown route proves middleware stops stale mutation before route execution.
            r=await http.post('/api/not-a-route',headers={'X-CreatorHub-UI-Version':'old'})
            assert r.status_code==409 and r.headers['X-CreatorHub-UI-Stale']=='1'
            from app import db
            from app.models import AccountActionTask, TaskSubmission, RiskEvent
            from sqlmodel import select
            blocked=await http.post('/api/douyin/dm/batches',headers={'X-CreatorHub-UI-Version':'old','Idempotency-Key':'stale-test'},json={'account_id':1,'recipients':[{'target_uid':'1'}],'content':'test'})
            assert blocked.status_code==409
            with db.get_session() as session:
                for model in (AccountActionTask,TaskSubmission,RiskEvent):
                    assert not session.exec(select(model)).all()
            r=await http.post('/api/not-a-route',headers={'X-CreatorHub-UI-Version':version})
            assert r.status_code==404
            assert (await http.post('/api/not-a-route')).status_code==404
            assert (await http.post('/api/not-a-route',headers={'X-CreatorHub-Actor':'creatorhub-web'})).status_code==409
            html=await http.get('/')
            assert 'no-store' in html.headers['cache-control']
            assert '/static/douyin-dm.js?v=' in html.text
    asyncio.run(run())

def test_packaged_version_changes_without_asset_change(tmp_path,monkeypatch):
    import sys
    web=tmp_path/'web';web.mkdir()
    (web/'index.html').write_text('<head></head>')
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    version=tmp_path/'desktop-version.txt';version.write_text('0.2.0.1117')
    old=ui_manifest(web)
    assert ui_manifest(web)==old
    version.write_text('0.2.0.1118')
    assert ui_manifest(web)['version']!=old['version']
