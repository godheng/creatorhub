"""Assistant display identity comes from the shared commercial brand."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_system_brand():
    from app.brand import PRODUCT_NAME
    from app.agent.runtime import SYSTEM
    assert PRODUCT_NAME == json.loads((ROOT / "app/brand.json").read_text(encoding="utf-8"))["name"]
    assert SYSTEM.startswith(f"你是 {PRODUCT_NAME} 的本地业务助手，用中文回答。")

def test_frontend_brand_sources():
    source = (ROOT / "frontend/agent.jsx").read_text(encoding="utf-8")
    assert 'import brand from "../app/brand.json"' in source
    assert '"分析中" : brand.name' in source
    assert source.count('请重启 ${brand.name}') == 2
    assert '请重启 CreatorHub' not in source
