import pytest
from app import licensing
from commercial_fixtures import empty_schema


@pytest.fixture(autouse=True)
def no_live_license_transport(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("Live authorization service is disabled in tests")

    monkeypatch.setattr(licensing._client, "request", unavailable)


@pytest.fixture(autouse=True)
def agent_test_license(request, monkeypatch):
    # Upstream AI tests exercise model behavior under an active commercial license.
    module = request.module.__name__.split('.')[-1]
    if module.startswith('test_agent') or module == 'test_project_optimizations':
        monkeypatch.setattr(licensing, 'is_active', lambda: True)
