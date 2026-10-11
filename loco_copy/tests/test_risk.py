from decimal import Decimal

from loco_copy.constants import Rejection
from loco_copy.notify import Notifier
from loco_copy.risk import RiskGuard
from loco_copy.store import Store
from tests.conftest import T0, make_settings

DAY = 86_400_000


def guard(tmp_path, now, **kw):
    store = Store(":memory:")
    return RiskGuard(store, make_settings(tmp_path, **kw), Notifier(store, echo=False), clock=lambda: now[0])


def test_daily_loss_limit_and_reset_next_day(tmp_path):
    now = [T0]
    g = guard(tmp_path, now)
    g.on_frame()
    g.on_equity(Decimal("10000"))
    assert g.open_block(Decimal("8600")) is None
    assert g.open_block(Decimal("8400")) == Rejection.DAILY_LOSS_LIMIT
    now[0] = T0 + DAY  # next UTC day: the old day start no longer applies, even before anything refreshes it
    g.on_frame()
    assert g.open_block(Decimal("8400")) is None


def test_leader_breaker_trips_and_resume_resets(tmp_path):
    now = [T0]
    g = guard(tmp_path, now)
    g.on_frame()
    g.on_leader_account_value(Decimal("1000000"))
    g.on_leader_account_value(Decimal("850000"))
    assert g.open_block(None) is None
    g.on_leader_account_value(Decimal("790000"))
    assert g.open_block(None) == Rejection.LEADER_BREAKER
    g.release()
    assert g.open_block(None) is None


def test_zero_leader_account_value_is_ignored(tmp_path):
    g = guard(tmp_path, [T0])
    g.on_frame()
    g.on_leader_account_value(Decimal("1000"))
    g.on_leader_account_value(Decimal("0"))
    assert not g.leader_breaker_tripped()


def test_latency_breaker(tmp_path):
    now = [T0]
    g = guard(tmp_path, now)
    assert g.latency_breaker()  # no frame yet
    g.on_frame()
    assert not g.latency_breaker()
    now[0] += 61_000
    assert g.open_block(None) == Rejection.LATENCY_BREAKER
    g.on_frame()
    for _ in range(3):
        g.on_snapshot_result(False)
    assert g.latency_breaker()
    g.on_snapshot_result(True)
    assert not g.latency_breaker()


def test_stop_file(tmp_path):
    g = guard(tmp_path, [T0])
    g.on_frame()
    (tmp_path / "STOP").touch()
    assert g.open_block(None) == Rejection.KILL_SWITCH
    g.release()
    assert not (tmp_path / "STOP").exists()


def test_live_requires_confirm_phrase(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="LIVE_CONFIRM"):
        make_settings(tmp_path, broker="bybit", broker_env="live")
    s = make_settings(tmp_path, broker="bybit", broker_env="live", live_confirm="I ACCEPT REAL MONEY RISK")
    assert s.broker_env == "live"
