import json
from app.licensing import LicenseClient, KEY

def test_signed_id_persists_without_restoring_permissions(tmp_path):
    from license_service.core import Store
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=Ed25519PrivateKey.generate();now=[1000];saved={}
    s=Store(tmp_path/'db',key,lambda:now[0]);issued=s.create(2000)
    def request(path,body):
        return s.respond('activate' if path.endswith('activate') else 'check',body.get('code',body.get('credential')),body['device'],body['nonce'],body.get('device_name'))
    def client():
        return LicenseClient(device=lambda:'a'*64,timer=lambda:(100,100),request=request,public_key=key.public_key(),read=lambda k:saved.get(k,''),write=lambda k,v:saved.update({k:v}))
    c=client();assert c.validate(issued['code'])['valid'];assert c.status()['license_id']==issued['license_id']
    restarted=client();assert restarted.status()['license_id']==issued['license_id'];assert not restarted.allowed()
    now[0]=2001;state=restarted.validate();assert state['status']=='expired';assert not state['valid'];assert state['license_id']==issued['license_id']
    s.admin('disable',issued['license_id']);state=c.validate();assert state['status']=='disabled';assert not state['valid'];assert state['license_id']==issued['license_id']

def test_saved_id_display_does_not_authorize():
    c=LicenseClient(device=lambda:'a'*64,timer=lambda:(100,100),read=lambda k:json.dumps({'license_id':'known-id','credential':'x'}),write=lambda *a:None,request=lambda *a:(_ for _ in ()).throw(OSError()))
    assert c.status()['license_id']=='known-id'
    assert not c.status()['valid']
    assert c.validate()['license_id']=='known-id'
    assert not c.status()['valid']

def test_hostname_bounded_and_failure(monkeypatch):
    from app import licensing
    monkeypatch.setattr(licensing.socket,'gethostname',lambda:'a'*200)
    assert len(licensing.device_name())==128
    monkeypatch.setattr(licensing.socket,'gethostname',lambda:(_ for _ in ()).throw(OSError()))
    assert licensing.device_name()==''

def test_device_name_sent_without_identity_change(monkeypatch):
    from app import licensing
    calls=[]
    c=LicenseClient(device=lambda:'b'*64,timer=lambda:(100,100),read=lambda k:'',write=lambda *a:None,request=lambda path,body:calls.append(body))
    monkeypatch.setattr(licensing.socket,'gethostname',lambda:'first')
    c.validate('code')
    monkeypatch.setattr(licensing.socket,'gethostname',lambda:'renamed')
    c.validate()
    assert [b['device_name'] for b in calls]==['first','renamed']
    assert all(b['device']=='b'*64 for b in calls)
