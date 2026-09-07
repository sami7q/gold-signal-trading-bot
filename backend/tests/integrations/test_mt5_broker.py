"""MT5Broker.get_position() — the fix for a real bug found while verifying
end-to-end trade closure (PROJECT_SPEC.md Phase 12/13).

`mt5.positions_get()` only ever returns *currently open* positions — the
instant MT5 closes one server-side (SL, TP, or a stop-out), it disappears
from that list and moves into deal history. Before this fix,
`get_position()` returned `None` in that case, which `ReconciliationService`
reads as "the broker has no record of this position" and parks the trade as
`UNKNOWN` instead of resolving it to CLOSED_TP/CLOSED_SL with the real
profit — a real trade that really closed would sit looking unresolved
forever. These tests exercise `get_position()`/`_closed_position_from_history`
directly against a fake `mt5` module (mirroring the real MetaTrader5 API
shape), no real terminal needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.integrations.broker.mt5 import BOT_MAGIC_NUMBER, MT5Broker


def _settings() -> Settings:
    return Settings(
        mt5_enabled=True,
        mt5_account="999001",
        mt5_password="fake-not-real",
        mt5_server="Test-Server",
        mt5_symbol="XAUUSD",
    )


@dataclass
class FakePosition:
    ticket: int
    symbol: str
    type: int  # noqa: A003
    volume: float
    price_open: float
    sl: float
    tp: float


@dataclass
class FakeDeal:
    entry: int
    reason: int
    type: int  # noqa: A003
    volume: float
    price: float
    profit: float
    swap: float
    commission: float
    time: int
    symbol: str


class FakeMT5:
    """Mirrors just the subset of the real `MetaTrader5` module surface
    `MT5Broker` touches -- constants plus the two lookup functions under
    test."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_REASON_CLIENT = 0
    DEAL_REASON_MOBILE = 1
    DEAL_REASON_EXPERT = 3
    DEAL_REASON_SL = 4
    DEAL_REASON_TP = 5

    def __init__(self):
        self.open_positions: dict[int, FakePosition] = {}
        self.deals_by_position: dict[int, list[FakeDeal]] = {}

    def positions_get(self, ticket=None):  # noqa: ANN001
        if ticket is not None and ticket in self.open_positions:
            return (self.open_positions[ticket],)
        return ()

    def history_deals_get(self, position=None):  # noqa: ANN001
        if position is not None:
            return tuple(self.deals_by_position.get(position, ()))
        return ()


def _broker_with_fake(fake: FakeMT5) -> MT5Broker:
    broker = MT5Broker(_settings())
    broker._mt5 = fake  # noqa: SLF001 - bypassing connect() for isolated unit tests
    broker._connected = True  # noqa: SLF001
    return broker


def test_open_position_reported_as_open():
    fake = FakeMT5()
    fake.open_positions[555] = FakePosition(
        ticket=555, symbol="XAUUSD", type=0, volume=1.0, price_open=4616.22, sl=4602.0, tp=4626.22
    )
    broker = _broker_with_fake(fake)

    position = broker.get_position("555")

    assert position is not None
    assert position.is_open is True
    assert position.entry_price == Decimal("4616.22")
    assert position.stop_loss == Decimal("4602")
    assert position.take_profit == Decimal("4626.22")


def test_closed_by_take_profit_resolves_with_real_profit_and_reason():
    fake = FakeMT5()
    fake.deals_by_position[555] = [
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_IN,
            reason=FakeMT5.DEAL_REASON_EXPERT,
            type=0,
            volume=1.0,
            price=4616.22,
            profit=0.0,
            swap=0.0,
            commission=0.0,
            time=1_700_000_000,
            symbol="XAUUSD",
        ),
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_OUT,
            reason=FakeMT5.DEAL_REASON_TP,
            type=1,
            volume=1.0,
            price=4626.22,
            profit=1000.0,
            swap=-2.5,
            commission=-1.0,
            time=1_700_001_000,
            symbol="XAUUSD",
        ),
    ]
    broker = _broker_with_fake(fake)

    position = broker.get_position("555")

    assert position is not None
    assert position.is_open is False
    assert position.close_reason == "TP"
    assert position.close_price == Decimal("4626.22")
    assert position.profit == Decimal("996.5")  # 1000 - 2.5 - 1.0, summed across both deals
    assert position.entry_price == Decimal("4616.22")
    assert position.symbol == "XAUUSD"


def test_closed_by_stop_loss_resolves_as_a_loss():
    fake = FakeMT5()
    fake.deals_by_position[777] = [
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_IN,
            reason=FakeMT5.DEAL_REASON_EXPERT,
            type=0,
            volume=1.0,
            price=4616.22,
            profit=0.0,
            swap=0.0,
            commission=0.0,
            time=1_700_000_000,
            symbol="XAUUSD",
        ),
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_OUT,
            reason=FakeMT5.DEAL_REASON_SL,
            type=1,
            volume=1.0,
            price=4602.0,
            profit=-1422.0,
            swap=0.0,
            commission=0.0,
            time=1_700_002_000,
            symbol="XAUUSD",
        ),
    ]
    broker = _broker_with_fake(fake)

    position = broker.get_position("777")

    assert position is not None
    assert position.close_reason == "SL"
    assert position.profit == Decimal("-1422")


def test_no_open_position_and_no_history_is_genuinely_none():
    fake = FakeMT5()
    broker = _broker_with_fake(fake)

    assert broker.get_position("999999") is None


def test_only_the_opening_leg_in_history_does_not_guess_a_close():
    """If history only has the IN deal (e.g. a truncated history window),
    the position must not be reported as closed with fabricated data."""
    fake = FakeMT5()
    fake.deals_by_position[42] = [
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_IN,
            reason=FakeMT5.DEAL_REASON_EXPERT,
            type=0,
            volume=1.0,
            price=4616.22,
            profit=0.0,
            swap=0.0,
            commission=0.0,
            time=1_700_000_000,
            symbol="XAUUSD",
        )
    ]
    broker = _broker_with_fake(fake)

    assert broker.get_position("42") is None


def _capture_buy_order(broker_settings=None) -> tuple[dict, object]:
    """Sends one BUY through a fake `MetaTrader5` module and hands back the
    exact order_request dict the adapter built, plus the result -- lets a
    test assert on what MT5 would actually receive without a real order
    going anywhere. The fake symbol is 2-digit Gold (digits=2, point=0.01),
    matching the real broker metadata app/trading/pips.py is calibrated
    against, so 1 pip = $0.10 here."""
    fake = FakeMT5()
    captured: dict = {}

    def fake_order_send(request):  # noqa: ANN001
        captured.update(request)

        @dataclass
        class Result:
            retcode: int = 10009
            order: int = 1
            price: float = 4616.22
            volume: float = 1.0

        return Result()

    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TIME_GTC = 0
    fake.ORDER_FILLING_IOC = 1
    fake.TRADE_RETCODE_DONE = 10009
    fake.order_send = fake_order_send
    fake.symbol_info_tick = lambda symbol: type("T", (), {"bid": 4616.20, "ask": 4616.22})()

    class FakeSymbolInfo:
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        digits = 2
        point = 0.01
        trade_contract_size = 100.0

    fake.symbol_info = lambda symbol: FakeSymbolInfo()

    broker = MT5Broker(broker_settings or _settings())
    broker._mt5 = fake  # noqa: SLF001 - bypassing connect() for isolated unit tests
    broker._connected = True  # noqa: SLF001

    from app.core.constants import Side
    from app.integrations.broker.base import OpenTradeRequest

    result = broker.open_buy(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.BUY,
            lot=Decimal("1.0"),
            stop_loss=Decimal("4602"),
            take_profit=Decimal("4626.22"),
            client_reference="1:v1",
        )
    )
    return captured, result


def test_zero_fill_price_is_read_back_from_the_position():
    """A Market-Execution broker can report the order DONE with `price`
    still 0 (seen on Axi for real). Recording that 0 would make the entry
    price -- and every distance measured from it -- nonsense, so the
    adapter asks the broker what actually filled."""
    fake = FakeMT5()

    def fake_order_send(request):  # noqa: ANN001
        @dataclass
        class Result:
            retcode: int = 10009
            order: int = 555
            price: float = 0.0  # the broker never filled this field in
            volume: float = 1.0

        return Result()

    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TIME_GTC = 0
    fake.ORDER_FILLING_IOC = 1
    fake.TRADE_RETCODE_DONE = 10009
    fake.order_send = fake_order_send
    fake.symbol_info_tick = lambda symbol: type("T", (), {"bid": 4616.20, "ask": 4616.22})()
    fake.open_positions[555] = FakePosition(
        ticket=555, symbol="XAUUSD", type=1, volume=1.0, price_open=4616.20, sl=4626.0, tp=4610.0
    )

    class FakeSymbolInfo:
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        digits = 2
        point = 0.01
        trade_contract_size = 100.0

    fake.symbol_info = lambda symbol: FakeSymbolInfo()

    broker = _broker_with_fake(fake)

    from app.core.constants import Side
    from app.integrations.broker.base import OpenTradeRequest

    execution = broker.open_sell(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.SELL,
            lot=Decimal("1.0"),
            stop_loss=Decimal("4626"),
            take_profit=Decimal("4610"),
            client_reference="1:v1",
        )
    )

    assert execution.success is True
    assert execution.entry_price == Decimal("4616.20")


def test_open_order_carries_the_bot_magic_number():
    """Distinguishes bot-placed orders from manual trades on the same
    account -- proven directly against the order_request dict `open_buy`
    sends, without needing a real order to actually go through."""
    captured, result = _capture_buy_order()

    assert result.success is True  # type: ignore[attr-defined]
    assert captured["magic"] == BOT_MAGIC_NUMBER


def test_open_order_tolerates_the_configured_slippage():
    """2026-09-05 operator request: a market that moved between reading the
    quote and the server filling it must still be traded, not lost to a
    requote. MT5 takes that allowance in *points*, so the pips setting is
    converted -- 10 pips on 2-digit Gold (1 pip = $0.10, 1 point = $0.01)
    is $1.00, i.e. 100 points."""
    captured, _ = _capture_buy_order()

    assert captured["deviation"] == 100


def test_slippage_of_zero_means_no_drift_tolerated():
    """The pre-2026-09-05 behavior stays reachable: 0 pips sends MT5 the
    same 0 deviation it defaults to, refusing any fill off the quoted
    price."""
    settings = _settings()
    settings.mt5_max_slippage_pips = Decimal("0")
    captured, _ = _capture_buy_order(settings)

    assert captured["deviation"] == 0


@pytest.mark.parametrize("bad_reason", [FakeMT5.DEAL_REASON_CLIENT, FakeMT5.DEAL_REASON_MOBILE])
def test_manual_or_mobile_close_falls_back_to_manual_not_tp_or_sl(bad_reason):
    """A close reason that isn't SL or TP (e.g. the account owner manually
    closed it from the phone app) must never be mislabeled as TP/SL."""
    fake = FakeMT5()
    fake.deals_by_position[1] = [
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_IN,
            reason=FakeMT5.DEAL_REASON_EXPERT,
            type=0,
            volume=1.0,
            price=4616.22,
            profit=0.0,
            swap=0.0,
            commission=0.0,
            time=1_700_000_000,
            symbol="XAUUSD",
        ),
        FakeDeal(
            entry=FakeMT5.DEAL_ENTRY_OUT,
            reason=bad_reason,
            type=1,
            volume=1.0,
            price=4610.0,
            profit=-620.0,
            swap=0.0,
            commission=0.0,
            time=1_700_003_000,
            symbol="XAUUSD",
        ),
    ]
    broker = _broker_with_fake(fake)

    position = broker.get_position("1")

    assert position is not None
    assert position.close_reason == "MANUAL"
