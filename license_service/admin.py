import hashlib, secrets, time, json, getpass, os
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

MAX_PASSWORD_LENGTH = 1024

def set_password(root):
    password=getpass.getpass(f'New administrator password (8-{MAX_PASSWORD_LENGTH} characters): ')
    if not 8 <= len(password) <= MAX_PASSWORD_LENGTH: raise ValueError(f'Use 8-{MAX_PASSWORD_LENGTH} characters')
    if password!=getpass.getpass('Confirm password: '): raise ValueError('Passwords differ')
    salt=secrets.token_hex(16)
    value={'salt':salt,'hash':hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()}
    path=Path(root)/'admin-password.json'
    path.write_text(json.dumps(value));os.chmod(path,0o600)

def create_admin_app(store, root):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    sessions={}; attempts=[]
    password_file=Path(root)/'admin-password.json'
    def config():
        if not password_file.exists(): raise HTTPException(503,'Administrator is not configured')
        return password_file.read_text()
    def auth(request):
        cfg=config(); session=sessions.get(request.cookies.get('admin_session',''))
        if not session or session['until']<time.time() or session['config']!=cfg: raise HTTPException(401,'Please log in')
        if request.method!='GET' and not secrets.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']): raise HTTPException(403,'Invalid CSRF token')
        return session
    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
        return response
    @app.get('/',response_class=HTMLResponse)
    def page(request:Request):
        config()
        try: auth(request);name='admin.html'
        except HTTPException: name='login.html'
        return (Path(__file__).parent/name).read_text(encoding='utf-8')
    @app.post('/login')
    async def login(request:Request):
        cfg=config()
        if request.headers.get('x-admin-login')!='1': raise HTTPException(403,'Invalid login request')
        now=time.time();attempts[:]=[x for x in attempts if now-x<60]
        if len(attempts)>=10: raise HTTPException(429,'Wait one minute before retrying')
        attempts.append(now);body=await request.json();record=json.loads(cfg)
        password=body.get('password','')
        if not isinstance(password,str) or len(password)>MAX_PASSWORD_LENGTH: raise HTTPException(400,'Invalid password')
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(record['salt']),n=16384,r=8,p=1).hex()
        if not secrets.compare_digest(actual,record['hash']): raise HTTPException(401,'Incorrect password')
        token=secrets.token_urlsafe(32);sessions[token]={'csrf':secrets.token_urlsafe(32),'until':now+28800,'config':cfg}
        response=JSONResponse({'ok':True});response.set_cookie('admin_session',token,httponly=True,samesite='strict',max_age=28800);return response
    @app.get('/api/licenses')
    def listing(request:Request):
        session=auth(request)
        return {'licenses':store.list_licenses(),'csrf':session['csrf'],'now':store.clock()}
    @app.post('/logout')
    def logout(request:Request):
        auth(request);sessions.pop(request.cookies.get('admin_session'),None)
        response=JSONResponse({'ok':True});response.delete_cookie('admin_session');return response
    @app.post('/api/licenses')
    async def create(request:Request):
        auth(request);body=await request.json()
        try:
            expires=float(body['expires']);note=body.get('note','');rid=body['request_id']
            if not store.clock()<expires<253402300799 or not isinstance(note,str) or len(note)>500 or not isinstance(rid,str) or not 16<=len(rid)<=128: raise ValueError('Invalid input')
            return store.create(expires,note,rid)
        except (KeyError,TypeError,ValueError) as e: raise HTTPException(400,str(e))
    @app.post('/api/licenses/{lid}/{action}')
    async def action(lid:str,action:str,request:Request):
        auth(request)
        if action not in ('renew','unbind','disable'): raise HTTPException(404)
        body=await request.json()
        try:
            expires=float(body['expires']) if action=='renew' else None
            if expires is not None and not store.clock()<expires<253402300799: raise ValueError('Invalid expiry')
            store.admin(action,lid,expires);return {'ok':True}
        except (KeyError,TypeError,ValueError) as e: raise HTTPException(400,str(e))
    return app
