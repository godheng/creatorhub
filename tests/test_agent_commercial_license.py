"""Commercial boundaries, offline models and isolated project databases."""
import asyncio
import pytest
from app import licensing
from app.agent import runtime
from app.agent.providers import Turn, ToolCall
from app.agent.workflows import save_plan, validate_plan, apply_plan
from test_agent import client, enabled, account, plan_args, store
from test_project_optimizations import local_project
from app.models import AgentRun


def test_denied_operations_preserve_history_and_stop(local_project, monkeypatch):
    enabled()
    store(AgentRun(id='existing-analysis', request_hash='fixture', status='running', partial_answer='已收到的内容'))
    monkeypatch.setattr(licensing, 'is_active', lambda: False)
    async def scenario():
        async with client() as http:
            for path, body in [('/runs', {'prompt':'分析'}), ('/test', {}), ('/models', {}),
                               ('/workflows/missing/apply', {'confirmed':True,'plan_hash':'a'*64})]:
                assert (await http.post('/api/agent'+path,json=body)).status_code == 403
            history = await http.get('/api/agent/runs/existing-analysis')
            assert history.status_code == 200
            assert history.json()['partial_answer'] == '已收到的内容'
            assert (await http.post('/api/agent/runs/existing-analysis/cancel')).status_code == 200
    asyncio.run(scenario())


def test_queued_license_loss_never_calls_model(local_project, monkeypatch):
    enabled()
    calls=[]
    class Model:
        def __init__(self,*args): calls.append(args)
    monkeypatch.setattr(runtime,'ModelSession',Model)
    async def scenario():
        row=runtime.start_run(runtime.RunInput(prompt='分析'))
        monkeypatch.setattr(licensing,'is_active',lambda:False)
        await runtime.RUNNING[row['id']]
        result=runtime.get_run(row['id'])
        assert result['status']=='failed' and '授权' in result['error']
        assert calls == []
    asyncio.run(scenario())


def test_next_round_denial_preserves_output_usage(local_project, monkeypatch):
    enabled()
    calls=[]
    class Model:
        def __init__(self,*args): pass
        async def complete(self,tools):
            calls.append(1)
            self.on_progress('已收到的内容', {'usage':{'input_tokens':7,'output_tokens':3}})
            monkeypatch.setattr(licensing,'is_active',lambda:False)
            return Turn(text='已收到的内容',calls=[ToolCall('c1','get_metrics',{'days':1})],input_tokens=7,output_tokens=3)
        def add_results(self,results): pass
    monkeypatch.setattr(runtime,'ModelSession',Model)
    async def scenario():
        row=runtime.start_run(runtime.RunInput(prompt='分析'))
        await runtime.RUNNING[row['id']]
        result=runtime.get_run(row['id'])
        assert len(calls)==1 and result['status']=='failed'
        assert result['partial_answer']=='已收到的内容'
        assert result['input_tokens']==7 and result['output_tokens']==3
    asyncio.run(scenario())


def test_workflow_loss_between_steps_and_confirmed_resume(local_project, monkeypatch):
    acc=account()
    args=plan_args(acc)
    args['steps'].append({**args['steps'][0], 'keywords':['第二步']})
    plan=save_plan('fixture-run',validate_plan(args))
    calls=[]
    async def execute(payload,request):
        calls.append(request.headers['idempotency-key'])
        if len(calls)==1: monkeypatch.setattr(licensing,'is_active',lambda:False)
        return {'id':len(calls),'status':'queued'}
    async def scenario():
        first=await apply_plan(plan['id'],plan['plan_hash'],{'create_collection':execute})
        assert first['status']=='partial' and len(first['results'])==1
        monkeypatch.setattr(licensing,'is_active',lambda:True)
        final=await apply_plan(plan['id'],plan['plan_hash'],{'create_collection':execute})
        assert final['status']=='applied' and len(final['results'])==2
        await apply_plan(plan['id'],plan['plan_hash'],{'create_collection':execute})
        assert len(calls)==2 and len(set(calls))==2
    asyncio.run(scenario())


@pytest.mark.parametrize('with_key', [False, True])
def test_direct_submission_denies_new_but_replays_receipt(local_project, monkeypatch, with_key):
    from app.db import get_session
    from app.submissions import submit_once
    from starlette.requests import Request
    calls=[]
    def create():
        calls.append(1)
        return {'id':123}
    request=Request({'type':'http','headers':[(b'idempotency-key', b'fixture-submission-123')] if with_key else []})
    monkeypatch.setattr(licensing,'is_active',lambda:False)
    with get_session() as session:
        with pytest.raises(licensing.LicenseRequired):
            submit_once(session, request=request, scope='fixture', body={}, create=create)
        session.rollback()
    assert calls == []
    monkeypatch.setattr(licensing,'is_active',lambda:True)
    with get_session() as session:
        assert submit_once(session, request=request, scope='fixture', body={}, create=create)[1]
    monkeypatch.setattr(licensing,'is_active',lambda:False)
    if with_key:
        with get_session() as session:
            receipt,created=submit_once(session, request=request, scope='fixture', body={}, create=create)
            assert not created and receipt['replayed']
    assert len(calls)==1

def test_license_loss_after_middleware_returns_403(local_project, monkeypatch):
    enabled()
    checks=[]
    def active():
        checks.append(1)
        return len(checks)==1
    monkeypatch.setattr(licensing,'is_active',active)
    async def scenario():
        async with client() as http:
            response=await http.post('/api/agent/runs',json={'prompt':'分析'})
            assert response.status_code==403
            assert response.json()['code']=='license_required'
    asyncio.run(scenario())
    assert len(checks)>=2


@pytest.mark.parametrize('operation',['provider','catalog'])
def test_direct_model_boundaries_never_reach_network(local_project, monkeypatch, operation):
    from app.agent import providers, catalog
    from app.agent.config import AgentSettings, ConnectionSettings
    calls=[]
    def forbidden_client(*args,**kwargs):
        calls.append(1)
        raise AssertionError('network client must not be constructed')
    monkeypatch.setattr(providers.httpx,'AsyncClient',forbidden_client)
    monkeypatch.setattr(licensing,'is_active',lambda:False)
    async def scenario():
        with pytest.raises(licensing.LicenseRequired):
            if operation=='provider':
                await providers.ModelSession(AgentSettings(base_url='https://fixture.invalid/v1',model='fixture'),'s','p').complete([])
            else:
                await catalog.discover_models(ConnectionSettings(base_url='https://fixture.invalid/v1'))
    asyncio.run(scenario())
    assert calls==[]


@pytest.mark.parametrize('operation',['collection','monitor'])
def test_direct_business_creation_requires_license(local_project, monkeypatch, operation):
    from app import main, db
    from app.models import KeywordCollectionJob, MonitorTarget, TaskSubmission
    from sqlmodel import select
    monkeypatch.setattr(licensing,'is_active',lambda:False)
    async def scenario():
        with pytest.raises(licensing.LicenseRequired):
            if operation=='collection':
                await main.create_keyword_collection(main.KeywordCollectionIn.model_construct())
            else:
                await main.add_monitor(main.TargetIn.model_construct())
    asyncio.run(scenario())
    with db.get_session() as session:
        for model in (KeywordCollectionJob,MonitorTarget,TaskSubmission):
            assert session.exec(select(model)).all()==[]
