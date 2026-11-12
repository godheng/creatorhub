import hashlib
import plistlib
from types import SimpleNamespace
import pytest
from app import license_platform as p

@pytest.fixture(autouse=True)
def clear_cache():
    p.device_digest.cache_clear()
    yield
    p.device_digest.cache_clear()

@pytest.mark.parametrize('platform,reader,value,prefix', [
    ('win32','_windows_id',' AbC-123 ', 'creatorhub-device-v1:'),
    ('darwin','_mac_id','ABC-123','creatorhub-device-v1:macos:'),
    ('linux','_linux_id','ABCD','creatorhub-device-v1:linux:'),
])
def test_identity_compatibility_and_cache(monkeypatch, platform, reader, value, prefix):
    monkeypatch.setattr(p.sys,'platform',platform)
    calls=[]
    monkeypatch.setattr(p,reader,lambda: calls.append(1) or value)
    assert p.device_digest()==hashlib.sha256((prefix+value.strip().lower()).encode()).hexdigest()
    assert p.device_digest()==p.device_digest()
    assert calls==[1]

def test_failure_not_cached(monkeypatch):
    monkeypatch.setattr(p.sys,'platform','win32')
    monkeypatch.setattr(p,'_windows_id',lambda: '')
    with pytest.raises(ValueError): p.device_digest()
    monkeypatch.setattr(p,'_windows_id',lambda: 'valid')
    assert len(p.device_digest())==64

def test_linux_fallback_and_invalid(monkeypatch):
    seen=[]
    def read(path, **kw):
        seen.append(path.as_posix())
        if path.as_posix()=='/etc/machine-id': raise FileNotFoundError()
        return 'a'*32+'\n'
    monkeypatch.setattr(p.Path,'read_text',read)
    assert p._linux_id()=='a'*32
    assert seen==['/etc/machine-id','/var/lib/dbus/machine-id']
    monkeypatch.setattr(p.Path,'read_text',lambda *a,**k:'0'*32)
    with pytest.raises(ValueError): p._linux_id()

def test_mac_identity_plist(monkeypatch):
    uuid='12345678-1234-1234-1234-123456789ABC'
    def output(args,timeout):
        assert args==['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice','-a']
        assert timeout==5
        return plistlib.dumps([{'IOPlatformUUID':uuid}])
    monkeypatch.setattr(p.subprocess,'check_output',output)
    assert p._mac_id()==uuid
    monkeypatch.setattr(p.subprocess,'check_output',lambda *a,**k:b'invalid')
    with pytest.raises(Exception): p._mac_id()

def test_linux_clocks_distinct_and_suspend(monkeypatch):
    monkeypatch.setattr(p.sys,'platform','linux')
    monkeypatch.setattr(p.time,'CLOCK_BOOTTIME',7,raising=False)
    monkeypatch.setattr(p.time,'CLOCK_MONOTONIC',1,raising=False)
    values={7:100,1:90}
    monkeypatch.setattr(p.time,'clock_gettime',lambda key: values[key],raising=False)
    before=p.clock(); values[7]+=60
    after=p.clock()
    assert after[0]-before[0]==60 and after[1]-before[1]==0

def test_mac_clock_timebase(monkeypatch):
    class Fn:
        def __init__(self,fn): self.fn=fn
        def __call__(self,*args): return self.fn(*args)
    def base(ptr):
        ptr._obj.numer=125;ptr._obj.denom=3
        return 0
    lib=SimpleNamespace(mach_timebase_info=Fn(base),mach_continuous_time=Fn(lambda:6000000000),mach_absolute_time=Fn(lambda:3000000000))
    monkeypatch.setattr(p.ctypes,'CDLL',lambda path:lib)
    assert p._mac_clock()==pytest.approx((250,125))
    lib.mach_timebase_info=Fn(lambda ptr:1)
    with pytest.raises(OSError):p._mac_clock()

def test_native_device_and_clock():
    first=p.clock(); second=p.clock()
    assert second[0]>=first[0] and second[1]>=first[1]
    assert len(p.device_digest())==64


def test_unsupported_fails_closed(monkeypatch):
    monkeypatch.setattr(p.sys,'platform','unknown')
    with pytest.raises(ValueError):p.device_digest()
    with pytest.raises(OSError):p.clock()

