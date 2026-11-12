import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_device_license import env


def test_status_and_grace_not_blocked_by_network(env):
    c,s,i,t,_,request=env
    c.validate(i['code'])
    entered,release=threading.Event(),threading.Event()
    def slow(*args):
        entered.set();assert release.wait(3);return request(*args)
    c.request=slow
    with ThreadPoolExecutor(2) as pool:
        running=pool.submit(c.validate)
        assert entered.wait(1)
        try:
            assert pool.submit(c.status).result(timeout=.5)['valid']
            t[:]=[2800,2800]
            assert not pool.submit(c.allowed).result(timeout=.5)
        finally: release.set()
        running.result()


def test_inflight_response_cannot_clear_resume_generation(env):
    c,s,i,t,_,request=env
    c.validate(i['code'])
    entered,release=threading.Event(),threading.Event()
    def slow(*args):
        response=request(*args);entered.set();assert release.wait(3);return response
    c.request=slow
    with ThreadPoolExecutor() as pool:
        running=pool.submit(c.validate);assert entered.wait(1)
        t[:]=[1500,1001]
        assert not c.status()['valid']
        release.set();assert not running.result()['valid']
        assert c.needs_resume
    c.request=request
    assert c.validate()['valid']


@pytest.mark.asyncio
async def test_async_resume_returns_without_waiting_and_deduplicates(env):
    c,s,i,t,_,request=env
    c.validate(i['code']);t[:]=[1500,1001]
    entered,release=threading.Event(),threading.Event();calls=[]
    def slow(*args):
        calls.append(1);entered.set();assert release.wait(3);return request(*args)
    c.request=slow
    try:
        assert not c.allowed()
        assert await asyncio.to_thread(entered.wait,1)
        for _ in range(20): assert not c.allowed()
        await asyncio.sleep(0)
        assert calls==[1]
    finally:
        release.set()
        if c.resume_thread: await asyncio.to_thread(c.resume_thread.join,2)
    assert c.allowed()


def test_validation_requests_serialized(env):
    c,s,i,t,_,request=env
    c.validate(i['code'])
    entered,release=threading.Event(),threading.Event();calls=[]
    def slow(*args):
        calls.append(1)
        if len(calls)==1:
            entered.set();assert release.wait(3)
        return request(*args)
    c.request=slow
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(c.validate);assert entered.wait(1)
        second=pool.submit(c.validate)
        assert len(calls)==1
        release.set();assert first.result()['valid'];assert second.result()['valid']
    assert len(calls)==2
