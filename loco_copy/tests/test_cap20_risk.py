"""botup.md v4 §6 test_cap20_risk + v5 account kill: exposure 6x and feed outage block opens / adds, never exits."""

from decimal import Decimal

from loco_copy.constants import Decision
from tests.conftest import F


async def test_exposure_limit_blocks_adds_and_new_trades_never_exits(cap20):
    h = cap20(max_total_notional_x_equity=Decimal("0.3"))  # 3000 USDT of notional at 10,000 equity
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))  # 24 HYPE = 960
    # spec §7: an add that would take the total past the limit is blocked (and logged), never shrunk
    assert await h.fill(F(1000, "HYPE", "B", 40, 5000, 1000)) == [Decision.REJECTED]  # +4800 -> 5760 > 3000
    assert h.last_event()["rejection"] == "EXPOSURE_LIMIT" and h.copy("HYPE").qty_open == 24
    assert any(r["type"] == "EXPOSURE_LIMIT" for r in h.store.logs_since(0))
    assert await h.fill(F(2000, "HYPE", "B", 40, 1000, 6000)) == [Decision.ADDED]  # +960 -> 1920: fits
    assert h.copy("HYPE").qty_open == 48 and not h.copy("HYPE").capped
    # a new trade past the limit is skipped as a whole: its later adds find no copy
    assert await h.fill(F(3000, "BTC", "B", 100000, 1, 0)) == [Decision.REJECTED]  # 2400 more -> 4320 > 3000
    assert h.copy("BTC").close_reason == "EXPOSURE_LIMIT" and h.exchange.position("BTCUSDT") is None
    assert await h.fill(F(4000, "BTC", "B", 100000, 1, 1)) == [Decision.NO_MATCHING_COPY]
    assert await h.fill(F(5000, "HYPE", "A", 40, 7000, 7000)) == [Decision.CLOSED]  # exits always pass
    assert h.exchange.positions() == []


async def test_feed_outage_blocks_opens_and_adds_never_exits(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    h.feed_alive = False
    assert await h.fill(F(121_000, "HYPE", "B", 40, 1000, 1000)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "FEED_OUTAGE"
    assert await h.fill(F(122_000, "BTC", "B", 100000, 1, 0)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "FEED_OUTAGE"
    assert await h.fill(F(123_000, "HYPE", "A", 40, 2000, 2000)) == [Decision.CLOSED]
    assert h.exchange.positions() == []
    h.risk.on_frame()  # contact again: a WS frame is enough
    assert await h.fill(F(124_000, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]


async def test_account_kill_blocks_new_risk_but_never_exits(cap20):
    h = cap20(guard=True, account_kill_dd_pct=Decimal("0.25"))
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    guard = h.engine.real.guard
    assert guard.peak() == 10000
    h.exchange.cash -= Decimal(3000)  # a 30 % loss elsewhere on the account
    guard.on_equity(h.exchange.equity())
    assert guard.tripped() and any(r["type"] == "ACCOUNT_KILL_TRIPPED" for r in h.store.logs_since(0))
    assert await h.fill(F(1000, "HYPE", "B", 40, 1000, 1000)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "ACCOUNT_KILL"
    assert await h.fill(F(2000, "BTC", "B", 100000, 1, 0)) == [Decision.REJECTED]
    assert await h.fill(F(3000, "HYPE", "A", 40, 2000, 2000)) == [Decision.CLOSED]
    guard.release()  # `resume`: the peak restarts from the current equity
    assert await h.fill(F(4000, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    assert not guard.tripped() and guard.peak() == h.exchange.equity()


def test_account_kill_dd_reads_percent_or_fraction(tmp_path):
    from tests.conftest import make_settings

    assert make_settings(tmp_path, account_kill_dd_pct="25").account_kill_dd_pct == Decimal("0.25")
    assert make_settings(tmp_path, account_kill_dd_pct="0.25").account_kill_dd_pct == Decimal("0.25")
    assert make_settings(tmp_path, account_kill_dd_pct="").account_kill_dd_pct is None


def test_cap20_turns_the_v3_rules_off_and_refuses_a_take_profit(tmp_path):
    from tests.conftest import make_settings

    s = make_settings(tmp_path, strategy="cap20_cross", margin_mode="CROSS", follow_adds=True, leverage=3)
    assert s.take_profit_roi is None and s.daily_loss_limit_pct is None and s.leader_dd_breaker_pct is None
    assert s.max_entry_drift is None and s.max_signal_age_ms is None and s.max_open_positions is None
    try:
        make_settings(tmp_path, strategy="cap20_cross", margin_mode="CROSS", follow_adds=True,
                      take_profit_roi=Decimal("0.5"))
        raise AssertionError("a take profit must be refused under cap20_cross")
    except ValueError as exc:
        assert "TAKE_PROFIT_ROI" in str(exc)
    v3 = make_settings(tmp_path)  # first_fill keeps its v3 defaults
    assert v3.take_profit_roi == Decimal("0.50") and v3.max_open_positions == 10
    assert v3.daily_loss_limit_pct == Decimal("0.15")


def test_leverage_outside_3_or_5_is_warned(tmp_path):
    from loco_copy.config import strategy_warnings
    from tests.conftest import make_settings

    base = dict(strategy="cap20_cross", margin_mode="CROSS", follow_adds=True)
    assert strategy_warnings(make_settings(tmp_path, **base, leverage=3)) == []
    assert strategy_warnings(make_settings(tmp_path, **base, leverage=10))


def test_copy_ratio_k_is_read_as_size_k(tmp_path):
    from loco_copy.config import LOCO_ADDRESS, Settings, read_lane_overrides

    env = tmp_path / ".env"
    env.write_text(f"COPY_RATIO_K=2.4\nLEVERAGE=3\nSTRATEGY=cap20_cross\nMARGIN_MODE=CROSS\nFOLLOW_ADDS=true\n"
                   f"LEADERS=loco:{LOCO_ADDRESS},x1:0x8bae3527e5a33fa0cf184f37bc112d071463ab6d\nCOPY_RATIO_K__X1=2\n",
                   encoding="utf-8")
    s = Settings(_env_file=env, data_dir=tmp_path)
    assert s.size_k == Decimal("2.4") and s.k == Decimal("2.4")
    s = s.model_copy(update={"lane_overrides": read_lane_overrides(s.labels, env)})
    assert s.for_lane("x1").k == Decimal(2)
