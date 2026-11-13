import base64,json,sqlite3
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_service.core import Store,create_app

def payload(r):
    value=r.json()['payload'];return json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))

def test_name_lifecycle_and_legacy_requests(tmp_path):
    now=[1000];s=Store(tmp_path/'db',Ed25519PrivateKey.generate(),lambda:now[0]);c=TestClient(create_app(s));i=s.create(5000)
    body=dict(code=i['code'],device='a'*64,nonce='nonce-123456789012345')
    first=payload(c.post('/v1/activate',json={**body,'device_name':'电脑甲'}));credential=first['credential']
    check=dict(credential=credential,device='a'*64,nonce=body['nonce'])
    assert s.list_licenses()[0]['device_name']=='电脑甲'
    c.post('/v1/check',json=check)
    assert s.list_licenses()[0]['device_name']=='电脑甲'
    special='<script>alert(1)</script>'+'名'*100
    assert c.post('/v1/check',json={**check,'device_name':special}).status_code==200
    assert s.list_licenses()[0]['device_name']==special
    assert payload(c.post('/v1/check',json={**check,'device':'b'*64,'device_name':'冒名'}))['status']=='device_mismatch'
    assert payload(c.post('/v1/activate',json={**body,'device':'b'*64,'device_name':'冒名'}))['status']=='bound'
    assert s.list_licenses()[0]['device_name']==special
    assert c.post('/v1/check',json={**check,'device_name':'x'*129}).status_code==422
    now[0]=5001;c.post('/v1/check',json={**check,'device_name':'过期覆盖'})
    assert s.list_licenses()[0]['device_name']==special
    now[0]=1000;s.admin('unbind',i['license_id'])
    assert s.list_licenses()[0]['device_name']==''
    assert payload(c.post('/v1/check',json={**check,'device_name':'旧设备'}))['status']=='revoked'
    new=payload(c.post('/v1/activate',json={**body,'device':'b'*64,'device_name':'电脑乙'}))
    assert s.list_licenses()[0]['device_name']=='电脑乙'
    assert payload(c.post('/v1/check',json={**check,'device_name':'旧设备覆盖'}))['status']=='revoked'
    assert payload(c.post('/v1/check',json={**check,'credential':'invalid','device_name':'无效凭证覆盖'}))['status']=='revoked'
    assert s.list_licenses()[0]['device_name']=='电脑乙'
    s.admin('disable',i['license_id']);c.post('/v1/check',json={**check,'credential':new['credential'],'device':'b'*64,'device_name':'禁用覆盖'})
    assert s.list_licenses()[0]['device_name']=='电脑乙'

def test_old_schema_migrates_name_empty(tmp_path):
    path=tmp_path/'db';db=sqlite3.connect(path)
    db.execute('CREATE TABLE licenses (id TEXT PRIMARY KEY,code_hash TEXT UNIQUE,expires REAL,enabled INTEGER,device TEXT,version INTEGER,credential TEXT,note TEXT NOT NULL DEFAULT "")')
    db.execute('INSERT INTO licenses VALUES ("old","hash",5000,1,"digest",1,"credential","客户")');db.commit();db.close()
    s=Store(path,Ed25519PrivateKey.generate(),lambda:1000)
    row=s.list_licenses()[0];assert row['device_name']=='';assert row['device']=='digest';assert row['note']=='客户'
    s.respond('check','credential','digest','nonce-1234567890123','迁移设备')
    assert s.list_licenses()[0]['device_name']=='迁移设备'
