"""Upgrade an actual commercial-base database, generated from pinned Git sources."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from sqlalchemy import inspect
from app import db
from app.models import DouyinAccount, KeywordCollectionJob, AppSetting

BASE = '8de6a9d313d65fd0bbd1b102a9d04c8cd0aba4d9'


def test_commercial_database_upgrade_preserves_records_and_settings(tmp_path):
    root = Path(__file__).resolve().parents[1]
    legacy = tmp_path / 'legacy'
    package = legacy / 'app'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    for name in ('models.py', 'db.py'):
        source = subprocess.check_output(['git', '-c', 'safe.directory=*', 'show', f'{BASE}:app/{name}'], cwd=root)
        (package / name).write_bytes(source)
    path = tmp_path / 'commercial.db'
    script = '''
from app.db import init_db, get_session
from app.models import DouyinAccount, KeywordCollectionJob, AppSetting
import sys
init_db(sys.argv[1])
with get_session() as s:
    s.add(DouyinAccount(id=17, nickname='Legacy account', platform='douyin', status='active'))
    s.add(KeywordCollectionJob(id=23, account_id=17, keywords='legacy keyword', status='pending'))
    s.add(AppSetting(key='device_license_credential', value='fixture credential'))
    s.add(AppSetting(key='ai_enabled', value='false'))
    s.commit()
'''
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    subprocess.run([sys.executable, '-c', script, str(path)], cwd=legacy, env=env, check=True)
    with sqlite3.connect(path) as connection:
        names = {r[0] for r in connection.execute("select name from sqlite_master where type='table'")}
        assert 'agentrun' not in names and 'agentworkflow' not in names
    previous = db._engine
    try:
        db.init_db(str(path))
        assert {'agentrun', 'agentworkflow'} <= set(inspect(db._engine).get_table_names())
        with db.get_session() as s:
            assert s.get(DouyinAccount,17).nickname == 'Legacy account'
            job = s.get(KeywordCollectionJob,23)
            assert job.account_id == 17 and job.keywords == 'legacy keyword' and job.status == 'pending'
            assert s.get(AppSetting,'device_license_credential').value == 'fixture credential'
            assert s.get(AppSetting,'ai_enabled').value == 'false'
        from app.agent.config import get_settings
        assert not get_settings().enabled and not get_settings().mcp_enabled
    finally:
        db._engine.dispose()
        db._engine = previous

