import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from license_service.core import Store,create_app
from license_service.admin import create_admin_app, set_password
from unittest.mock import patch

class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.key=Ed25519PrivateKey.generate()
        self.store=Store(self.root/'db',self.key,lambda:1000)
        self.client=TestClient(create_admin_app(self.store,self.root))
    def login(self):
        salt='ab'*16
        (self.root/'admin-password.json').write_text(json.dumps(dict(salt=salt,hash=hashlib.scrypt(b'test-password-only',salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex())))
        self.assertEqual(self.client.post('/login',headers={'x-admin-login':'1'},json={'password':'test-password-only'}).status_code,200)
        self.headers={'x-csrf-token':self.client.get('/api/licenses').json()['csrf']}
    def test_password_setup_login_boundaries(self):
        path=self.root/'admin-password.json'
        for length in (8,1024):
            password='x'*length
            with patch('license_service.admin.getpass.getpass',return_value=password):
                set_password(self.root)
            self.assertEqual(self.client.post('/login',headers={'x-admin-login':'1'},json={'password':password}).status_code,200)
        previous=path.read_bytes()
        for length in (7,1025):
            with patch('license_service.admin.getpass.getpass',return_value='x'*length):
                with self.assertRaises(ValueError): set_password(self.root)
            self.assertEqual(path.read_bytes(),previous)
        self.assertEqual(self.client.post('/login',headers={'x-admin-login':'1'},json={'password':'x'*1025}).status_code,400)

    def test_auth_csrf_logout_public(self):
        self.assertEqual(self.client.get('/').status_code,503)
        self.login()
        self.assertEqual(self.client.post('/api/licenses',json={}).status_code,403)
        self.assertEqual(self.client.post('/logout',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/licenses').status_code,401)
        public=TestClient(create_app(self.store))
        for path in ['/','/api/licenses','/login']:
            self.assertEqual(public.get(path).status_code,404)
    def test_create_retry_list_and_renew(self):
        self.login();body=dict(expires=5000,note='客户甲',request_id='unique-request-1234')
        r=self.client.post('/api/licenses',json=body,headers=self.headers);self.assertEqual(r.status_code,200)
        lid=r.json()['license_id'];code=r.json()['code']
        self.assertEqual(self.client.post('/api/licenses',json=body,headers=self.headers).status_code,400)
        other=Store(self.root/'db',self.key,lambda:1000)
        with self.assertRaises(ValueError):other.create(5000,'',body['request_id'])
        rows=self.client.get('/api/licenses').json()['licenses'];self.assertEqual(len(rows),1)
        self.assertEqual(set(rows[0]),{'id','expires','enabled','bound','note','device','device_name'})
        self.assertEqual(rows[0]['note'],'客户甲')
        with self.store.connect() as db:self.assertNotIn(code,str(dict(db.execute('select * from licenses').fetchone())))
        self.assertEqual(self.client.post(f'/api/licenses/{lid}/renew',json={'expires':4000},headers=self.headers).status_code,400)
        self.store.admin('disable',lid)
        self.assertEqual(self.client.post(f'/api/licenses/{lid}/renew',json={'expires':6000},headers=self.headers).status_code,200)
        self.assertEqual(self.store.list_licenses()[0]['enabled'],0)
    def test_old_migration_concurrent_client_and_admin(self):
        path=self.root/'legacy';db=sqlite3.connect(path)
        db.execute('CREATE TABLE licenses (id TEXT PRIMARY KEY,code_hash TEXT UNIQUE,expires REAL,enabled INTEGER,device TEXT,version INTEGER,credential TEXT)')
        db.execute('INSERT INTO licenses VALUES (?,?,5000,1,NULL,0,NULL)',('old',hashlib.sha256(b'old-code').hexdigest()));db.commit();db.close()
        old=Store(path,self.key,lambda:1000)
        self.assertEqual(old.list_licenses()[0]['note'],'')
        with ThreadPoolExecutor(4) as pool:
            futures=[pool.submit(old.respond,'activate','old-code','a'*64,'nonce-12345678901234'),pool.submit(old.list_licenses),pool.submit(old.create,6000,'test','concurrent-123456'),pool.submit(old.create,6000,'test','concurrent-123456')]
            results=[]
            for f in futures:
                try:results.append(f.result())
                except ValueError:results.append('duplicate')
        self.assertEqual(results.count('duplicate'),1);self.assertEqual(len(old.list_licenses()),2)
        for row in old.list_licenses():old.admin('disable',row['id'])

if __name__=='__main__':unittest.main()
