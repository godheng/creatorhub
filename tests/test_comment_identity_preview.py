import asyncio
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import text
from app import db, main, douyin_dm_batches as dm
from app.models import KeywordCollectionJob, KeywordCollectionContent, KeywordCollectionComment, AccountActionTask
from app.platforms.douyin.extract import parse_comment, parse_creator_comment
from sqlmodel import select
from test_douyin_dm_batches import active
from test_project_optimizations import local_project, store


def saved_comment(uid='42', platform='douyin'):
    job=store(KeywordCollectionJob(platform=platform, account_id=999))
    content=store(KeywordCollectionContent(job_id=job,platform=platform,aweme_id='video',author_id='999',author_name='作者'))
    comment=store(KeywordCollectionComment(job_id=job,content_id=content,platform=platform,aweme_id='video',comment_id='comment',user_uid=uid,user_sec_uid='MS4wLjABcomment',user_nickname='评论者'))
    return dict(id=comment,job_id=job)


def test_saved_identity_is_local_and_untrusted_pair_ignored(active,monkeypatch):
    ref=saved_comment()
    resolve=AsyncMock(side_effect=AssertionError('no platform request'))
    monkeypatch.setattr(dm,'resolve_profile',resolve)
    monkeypatch.setattr(main,'engine',None)
    body=dm.PreviewIn.model_validate({'account_id':active,'comment_refs':[dict(**ref,target_uid='999'),ref]})
    result=asyncio.run(dm.preview(body))
    assert result['recipients']==[dict(target_uid='42',target_sec_uid='MS4wLjABcomment',target_nick='评论者',conv_id='')]
    assert result['duplicates']==1 and not result['pending'] and not result['invalid']
    resolve.assert_not_called()
    with db.get_session() as s: assert not s.exec(select(AccountActionTask)).all()


def test_history_fallback_and_invalid_refs(active,monkeypatch):
    old=saved_comment(uid='')
    wrong=saved_comment(platform='xhs')
    resolve=AsyncMock(side_effect=dm.PreviewDeferred('等待','2026-12-01T00:00:00Z'))
    monkeypatch.setattr(dm,'resolve_profile',resolve)
    result=asyncio.run(dm.preview(dm.PreviewIn(account_id=active,comment_refs=[old,wrong,dict(id=old['id'],job_id=wrong['job_id']),dict(id=99999,job_id=old['job_id'])])))
    assert len(result['invalid'])==3 and len(result['pending'])==1
    assert result['recipients']==[] and resolve.await_count==1


@pytest.mark.parametrize('parser',[parse_comment,parse_creator_comment])
def test_extract_only_commenter_raw_uid(parser):
    raw={'cid':'c','author':{'uid':'999'},'user':{'uid':'42','sec_uid':'sec','unique_id':'123','nickname':'visitor'}}
    assert parser(raw)['user_uid']=='42'
    raw['user'].pop('uid')
    assert parser(raw)['user_uid']==''
    raw['user']['uid']='not-numeric'
    assert parser(raw)['user_uid']==''


def test_old_comment_schema_migrates_without_fabricating_uid(active):
    ref=saved_comment(uid='')
    with db._engine.begin() as c:
        c.execute(text('ALTER TABLE keywordcollectioncomment DROP COLUMN user_uid'))
    db._auto_migrate(db._engine)
    with db.get_session() as s:
        row=s.get(KeywordCollectionComment,ref['id'])
        assert row.user_uid=='' and row.user_sec_uid=='MS4wLjABcomment'
        assert main._collection_comment_dict(row)['user_uid']==''


def test_recollection_backfills_only_matching_missing_uid(active):
    from app.engine.collection import KeywordCollector
    ref=saved_comment(uid='')
    with db.get_session() as s:
        row=s.get(KeywordCollectionComment,ref['id']); content_id=row.content_id
    def collect(uid,sec='MS4wLjABcomment',aweme='video'):
        return KeywordCollector._persist_comments(ref['job_id'],content_id,'douyin',aweme,[dict(comment_id='comment',user_uid=uid,user_sec_uid=sec)])
    assert collect('43',sec='different')==0
    assert collect('43',aweme='different')==0
    assert collect('not-uid')==0
    with db.get_session() as s: assert s.get(KeywordCollectionComment,ref['id']).user_uid==''
    assert collect('42')==0
    assert collect('99')==0
    with db.get_session() as s:
        assert s.get(KeywordCollectionComment,ref['id']).user_uid=='42'
        assert len(s.exec(select(KeywordCollectionComment)).all())==1


def test_timeout_is_unverified_and_success_is_preserved(active,monkeypatch):
    complete=saved_comment()
    old=saved_comment(uid='')
    resolve=AsyncMock(side_effect=ValueError('curl: (28) Connection timed out'))
    monkeypatch.setattr(dm,'resolve_profile',resolve)
    result=asyncio.run(dm.preview(dm.PreviewIn(account_id=active,comment_refs=[complete,old])))
    assert len(result['recipients'])==1 and result['invalid']==[]
    assert len(result['temporary_failures'])==1
    assert result['temporary_failures'][0]['kind']=='comment'
    resolve.side_effect=None
    resolve.return_value=dm.Recipient(target_uid='42',target_sec_uid='MS4wLjABcomment')
    retried=asyncio.run(dm.preview(dm.PreviewIn(account_id=active,comment_sec_uids=[result['temporary_failures'][0]['input']])))
    assert len(retried['recipients'])==1 and retried['temporary_failures']==[]

def test_http_comment_refs_reach_real_route(active,monkeypatch):
    from test_access_and_submissions import client
    ref=saved_comment()
    monkeypatch.setattr(dm,'resolve_profile',AsyncMock(side_effect=AssertionError('no network')))
    async def scenario():
        async with client() as http:
            r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'comment_refs':[ref]})
            assert r.status_code==200,r.text
            assert len(r.json()['recipients'])==1,r.text
            r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'comment_refs':[{'id':-1,'job_id':-1}]})
            assert r.status_code==200 and len(r.json()['invalid'])==1,r.text
    asyncio.run(scenario())

@pytest.mark.parametrize('pair',[
    {'uid':'42'}, {'sec_uid':'MS4wLjABmanual'},
    {'uid':'abc','sec_uid':'MS4wLjABmanual'},
    {'uid':'42','sec_uid':'123'},
    {'uid':'42','sec_uid':'https://www.douyin.com/user/sec'},
])
def test_direct_pair_invalid_http(active,pair):
    from test_access_and_submissions import client
    async def run():
        async with client() as http:
            r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'direct_recipients':[pair]})
            assert r.status_code==422,r.text
    asyncio.run(run())


def test_direct_pair_local_http_and_source_conflict(active,monkeypatch):
    from test_access_and_submissions import client
    ref=saved_comment()
    resolver=AsyncMock(side_effect=AssertionError('direct identity must not resolve'))
    monkeypatch.setattr(dm,'resolve_profile',resolver)
    monkeypatch.setattr(main,'engine',None)
    async def run():
        async with client() as http:
            pair={'uid':'42','sec_uid':'MS4wLjABcomment','nickname':'手工昵称'}
            r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'direct_recipients':[pair]})
            assert r.status_code==200,r.text
            assert r.json()['recipients'][0]['target_uid']=='42'
            assert r.json()['recipients'][0]['target_nick']=='手工昵称'
            for conflicting in ({**pair,'uid':'99'},{**pair,'sec_uid':'MS4wLjABother'}):
                r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'direct_recipients':[conflicting],'comment_refs':[ref]})
                assert r.status_code==422 and '冲突' in r.text
            r=await http.post('/api/douyin/dm/preview',json={'account_id':active,'direct_recipients':[pair],'comment_refs':[ref]})
            assert r.status_code==200 and len(r.json()['recipients'])==1 and r.json()['duplicates']==1
    asyncio.run(run())
    resolver.assert_not_called()
