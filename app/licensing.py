from __future__ import annotations
import asyncio
import base64
import json
import os
import secrets
import socket
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from .settings import get_setting, set_setting
from .license_platform import device_digest, clock

KEY = 'device_license_credential'
PUBLIC_KEY = Path(__file__).with_name('license_public.pem')
SERVICE_URL = os.environ.get('CREATORHUB_LICENSE_URL', 'http://3.38.149.16:8088').rstrip('/')

def transport(path, body):
    req = urllib.request.Request(SERVICE_URL + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read(32768))


def device_name():
    try:
        return ''.join(c for c in socket.gethostname().strip() if c.isprintable())[:128]
    except Exception:
        return ''


class LicenseClient:
    def __init__(self, device=device_digest, timer=clock, request=transport, public_key=None, read=get_setting, write=set_setting):
        self.device, self.timer, self.request = device, timer, request
        self.public_key, self.read, self.write = public_key, read, write
        self.lock = threading.RLock()
        self.validation_lock = threading.Lock()
        self.generation = 0
        self.resume_thread = None
        self.deadline = 0
        self.last = None
        self.next_check = 0
        self.needs_resume = False
        self.authorized_device = None
        self.data = dict(status='inactive', valid=False, expires_at=None, customer='', license_id='', device_name='', device_digest='', reason='启动时需要联网校验')
        self.event = threading.Event()
        self.thread = None

    def _time(self):
        current = self.timer()
        if self.last is not None:
            elapsed, awake = current[0]-self.last[0], current[1]-self.last[1]
            if elapsed < 0 or awake < 0 or abs(elapsed-awake) > 1:
                self.deadline = 0
                self.needs_resume = True
                self.generation += 1
                self.data.update(status='offline', reason='休眠恢复或计时状态改变，请联网重新校验')
        self.last = current
        return current[0]

    def status(self):
        with self.lock:
            self.data['device_name'] = device_name()
            if not self.data['license_id']:
                try:
                    saved = json.loads(self.read(KEY) or '{}')
                    lid = saved.get('license_id', '')
                    if isinstance(lid, str): self.data['license_id'] = lid[:128]
                except Exception:
                    pass
            try:
                now = self._time()
                if self.device is not device_digest:
                    self.data['device_digest'] = self.device()
                self.data['valid'] = now < self.deadline and self.data['device_digest'] == self.authorized_device
            except Exception:
                self.deadline = 0
                self.data.update(valid=False, status='invalid', reason='无法读取设备标识或计时器')
            if not self.data['valid'] and self.data['status'] == 'active':
                self.data.update(status='expired', reason='授权或离线宽限已结束，请重新校验')
            return dict(self.data)

    def validate(self, code=None):
        with self.validation_lock:
            try:
                with self.lock:
                    if code is not None:
                        self.deadline = 0
                    start = self._time()
                    generation = self.generation
                device = self.device()
                with self.lock:
                    self.data['device_digest'] = device
                nonce = secrets.token_urlsafe(32)
                saved = {} if code is not None else json.loads(self.read(KEY) or '{}')
                credential = saved.get('credential', '')
                body = dict(device=device, nonce=nonce, device_name=device_name())
                body.update(code=code) if code is not None else body.update(credential=credential)
                response = self.request('/v1/activate' if code is not None else '/v1/check', body)
                raw = base64.urlsafe_b64decode(response['payload'] + '=' * (-len(response['payload']) % 4))
                sig = base64.urlsafe_b64decode(response['signature'] + '=' * (-len(response['signature']) % 4))
                key = self.public_key or load_pem_public_key(PUBLIC_KEY.read_bytes())
                key.verify(sig, raw)
                data = json.loads(raw)
                if data['protocol'] != 1 or data['nonce'] != nonce or data['device'] != device:
                    raise ValueError('响应与请求或设备不匹配')
                with self.lock:
                    self._time()
                    if generation != self.generation:
                        return self.status()
                    if data['status'] != 'active':
                        self.deadline = 0
                        reasons = {'bound': '授权码已绑定其他设备，请联系管理员解绑后换机', 'revoked': '设备绑定已撤销，请联系管理员', 'disabled': '授权已禁用，请联系管理员', 'expired': '授权已到期，请联系管理员续期', 'device_mismatch': '设备与授权绑定不匹配', 'invalid': '授权码无效，请检查后重试', 'unbound': '设备已解绑，请重新激活'}
                        self.data.update(status=data['status'], reason=reasons.get(data['status'], '授权校验未通过，请联系管理员'))
                    else:
                        server, expiry, until = (data[k] for k in ('server_time','expires_at','valid_until'))
                        if any(type(v) not in (int,float) for v in (server,expiry,until)) or not server < until <= min(expiry, server+1800) or type(data['binding_version']) is not int or not data['license_id'] or not data['credential']:
                            raise ValueError('授权响应范围无效')
                        if code is None and any(data[k] != saved.get(k) for k in ('license_id', 'binding_version', 'credential')):
                            raise ValueError('绑定凭证不匹配')
                        self.write(KEY, json.dumps({k: data[k] for k in ('license_id', 'binding_version', 'credential')}))
                        self.needs_resume = False
                        self.authorized_device = device
                        self.deadline = start + until-server
                        self.next_check = start + 600
                        self.data.update(status='active', reason='', license_id=data['license_id'], expires_at=datetime.fromtimestamp(expiry, timezone.utc).isoformat())
            except Exception as exc:
                with self.lock:
                    self.data.update(status='offline', reason='联网或签名校验失败，请重试')
            return self.status()

    def allowed(self):
        state = self.status()
        if self.needs_resume:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                state = self.validate()
            else:
                with self.lock:
                    if not self.resume_thread or not self.resume_thread.is_alive():
                        self.resume_thread = threading.Thread(target=self.validate, daemon=True, name='license-resume')
                        self.resume_thread.start()
                return False
        return state['valid']

    def start(self):
        self.deadline = 0
        self.validate()
        self.event.clear()
        def run():
            while not self.event.wait(1):
                with self.lock:
                    due = self._time() >= self.next_check
                if due:
                    self.validate()
                    with self.lock:
                        self.next_check = self._time() + 600
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=run, daemon=True, name='license-refresh')
            self.thread.start()

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=12)


_client = LicenseClient()
def status(): return _client.status()
def revalidate(): return _client.validate()
def activate(code):
    result = _client.validate(code.strip())
    if not result['valid']: raise ValueError(result['reason'])
    return result
def start(): _client.start()
def stop(): _client.stop()
def is_active(): return _client.allowed()
class LicenseRequired(PermissionError): pass
def require_active():
    if not is_active(): raise LicenseRequired(status()['reason'] or '授权无效，请重新校验')
