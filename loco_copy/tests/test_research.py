"""botup.md §V9: the research export is imported (`loco-copy research-import`) and served to the dashboard."""

from __future__ import annotations

import argparse
import json

import pytest
from fastapi.testclient import TestClient

from loco_copy.auth import hash_password
from loco_copy.cli import cmd_research_import
from loco_copy.store import Store
from loco_copy.web import create_app
from tests.conftest import make_settings
from tests.test_web import ORIGIN, login


def test_import_and_serve(tmp_path):
    s = make_settings(tmp_path, api_cookie_secure=False)
    store = Store(s.db_path)
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    app = create_app(s)
    with TestClient(app, headers={"origin": ORIGIN}) as c:
        login(c)
        assert c.get("/api/research").json() == {"available": False}
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        cmd_research_import(s, argparse.Namespace(file=str(bad)))
    good = tmp_path / "research_latest.json"
    good.write_text(json.dumps({"generated": "2026-10-14 20:00 UTC", "top10": [{"rank": 1, "label": "8bae"}],
                                "mixes": [], "database": {"wallets": 3720}}), encoding="utf-8")
    cmd_research_import(s, argparse.Namespace(file=str(good)))
    with TestClient(app, headers={"origin": ORIGIN}) as c:
        login(c)
        d = c.get("/api/research").json()
    assert d["available"] and d["top10"][0]["label"] == "8bae" and d["history"] == ["research_2026-10-14.json"]
