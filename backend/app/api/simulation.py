"""Simulation control API (PROJECT_SPEC.md section 9.1 / 15.7).

Backs the dashboard's Simulation page — the only way to exercise the full
pipeline end to end before Telegram/MT5 are connected. Every endpoint is
disabled once the active `TradingSettings.mode` is LIVE (section 16: "These
endpoints must be disabled in LIVE production mode").
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlmodel import Session, delete

from app.accounts.service import AccountService
from app.core.auth_deps import get_current_account
from app.core.constants import TradingMode
from app.core.decimals import CleanDecimal
from app.core.runtime import get_broker, get_paper_broker
from app.database.models.incoming_message import IncomingMessage
from app.database.models.mt5_account import MT5Account
from app.database.models.signal import Signal
from app.database.models.signal_message import SignalMessage
from app.database.models.trade import Trade
from app.database.session import get_session
from app.integrations.broker.base import Broker, BrokerPosition
from app.integrations.broker.paper import PaperBroker
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.trading.service import TradingService

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


def require_not_live(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> None:
    # Runs against whichever account is selected (X-Account-Id), regardless
    # of that account's is_live flag -- LIVE mode is a per-account trading
    # setting, and simulation must stay blocked for that account's own
    # settings even though "is_live" (the global broker-connection flag)
    # is a separate concept in Phase 1.
    settings = SettingsService(session).get_for_account(account.id)  # type: ignore[arg-type]
    if settings.mode == TradingMode.LIVE:
        raise HTTPException(
            status_code=403, detail="Simulation endpoints are disabled in LIVE mode."
        )


class SendMessageIn(BaseModel):
    text: str
    channel_id: str = "sim-channel"
    message_id: str | None = None
    sent_at: datetime | None = None
    #: The *source-native* message_id (not our DB id) this message is
    #: replying to — mirrors how a real Telegram reply is expressed.
    #: Used to disambiguate which signal a details/SL message belongs to
    #: when more than one is waiting (PROJECT_SPEC.md section 32, rule 13;
    #: see app/signals/service.py::_resolve_via_reply).
    reply_to_message_id: str | None = None


class SendMessageOut(BaseModel):
    message_id: str
    duplicate: bool
    classification: str | None
    signal_id: int | None
    signal_status: str | None
    became_ready: bool
    #: True when this message carried signal details that could not be
    #: uniquely attached to one waiting signal — left unattached, no trade.
    ambiguous: bool = False


class MarketPriceIn(BaseModel):
    bid: Decimal | None = None
    delta: Decimal | None = None

    @model_validator(mode="after")
    def _one_of(self) -> MarketPriceIn:
        if (self.bid is None) == (self.delta is None):
            raise ValueError("Provide exactly one of `bid` or `delta`.")
        return self


class PositionOut(BaseModel):
    position_id: str
    symbol: str
    side: str
    lot: CleanDecimal
    entry_price: CleanDecimal
    stop_loss: CleanDecimal | None
    take_profit: CleanDecimal | None
    is_open: bool
    close_price: CleanDecimal | None
    profit: CleanDecimal | None
    close_reason: str | None

    @classmethod
    def from_position(cls, p: BrokerPosition) -> PositionOut:
        return cls(
            position_id=p.position_id,
            symbol=p.symbol,
            side=str(p.side),
            lot=p.lot,
            entry_price=p.entry_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            is_open=p.is_open,
            close_price=p.close_price,
            profit=p.profit,
            close_reason=p.close_reason,
        )


class SimulationStateOut(BaseModel):
    symbol: str
    bid: CleanDecimal
    ask: CleanDecimal
    balance: CleanDecimal
    equity: CleanDecimal
    open_positions: list[PositionOut]


def _state(broker: PaperBroker) -> SimulationStateOut:
    account = broker.get_account()
    return SimulationStateOut(
        symbol=broker.symbol,
        bid=broker.bid,
        ask=broker.ask,
        balance=account.balance,
        equity=account.equity,
        open_positions=[PositionOut.from_position(p) for p in broker.open_positions()],
    )


@router.get("/state", response_model=SimulationStateOut, dependencies=[Depends(require_not_live)])
def get_state(broker: Annotated[PaperBroker, Depends(get_paper_broker)]) -> SimulationStateOut:
    return _state(broker)


@router.post("/messages", response_model=SendMessageOut, dependencies=[Depends(require_not_live)])
def send_mock_message(
    payload: SendMessageIn,
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[Broker, Depends(get_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> SendMessageOut:
    message_id = payload.message_id or str(uuid4())
    data = IncomingMessageData(
        source="mock",
        channel_id=payload.channel_id,
        message_id=message_id,
        text=payload.text,
        sent_at=payload.sent_at or datetime.now(UTC),
        received_at=datetime.now(UTC),
        reply_to_message_id=payload.reply_to_message_id,
    )
    service = TradingService(session, broker, account_id=account.id)  # type: ignore[arg-type]
    result = service.process_incoming_message(data)
    return SendMessageOut(
        message_id=message_id,
        duplicate=result.duplicate,
        classification=result.parsed.classification.value if result.parsed else None,
        signal_id=result.signal.id if result.signal else None,
        signal_status=result.signal.status.value if result.signal else None,
        became_ready=result.became_ready,
        ambiguous=result.ambiguous,
    )


@router.post(
    "/market-price", response_model=SimulationStateOut, dependencies=[Depends(require_not_live)]
)
def set_market_price(
    payload: MarketPriceIn,
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[PaperBroker, Depends(get_paper_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> SimulationStateOut:
    if payload.bid is not None:
        closed = broker.set_price(payload.bid)
    else:
        assert payload.delta is not None  # enforced by MarketPriceIn._one_of
        closed = broker.move_price(payload.delta)
    TradingService(session, broker, account_id=account.id).on_positions_closed(  # type: ignore[arg-type]
        closed
    )
    return _state(broker)


@router.post(
    "/trigger-tp", response_model=SimulationStateOut, dependencies=[Depends(require_not_live)]
)
def trigger_take_profit(
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[PaperBroker, Depends(get_paper_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> SimulationStateOut:
    open_positions = broker.open_positions()
    if not open_positions:
        raise HTTPException(status_code=400, detail="No open position to trigger.")
    target = open_positions[0]
    if target.take_profit is None:
        raise HTTPException(status_code=400, detail="Open position has no Take Profit set.")
    closed = broker.set_price(target.take_profit)
    TradingService(session, broker, account_id=account.id).on_positions_closed(  # type: ignore[arg-type]
        closed
    )
    return _state(broker)


@router.post(
    "/trigger-sl", response_model=SimulationStateOut, dependencies=[Depends(require_not_live)]
)
def trigger_stop_loss(
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[PaperBroker, Depends(get_paper_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> SimulationStateOut:
    open_positions = broker.open_positions()
    if not open_positions:
        raise HTTPException(status_code=400, detail="No open position to trigger.")
    target = open_positions[0]
    if target.stop_loss is None:
        raise HTTPException(status_code=400, detail="Open position has no Stop Loss set.")
    closed = broker.set_price(target.stop_loss)
    TradingService(session, broker, account_id=account.id).on_positions_closed(  # type: ignore[arg-type]
        closed
    )
    return _state(broker)


class ResetIn(BaseModel):
    #: "account" resets the paper broker only; "trades" wipes signal/trade/message
    #: history; "all" (default) does both.
    scope: str = "all"


@router.post("/reset", dependencies=[Depends(require_not_live)])
def reset_simulation(
    payload: ResetIn,
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[PaperBroker, Depends(get_paper_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> dict:
    if payload.scope not in ("account", "trades", "all"):
        raise HTTPException(status_code=400, detail="scope must be one of: account, trades, all")

    if payload.scope in ("trades", "all"):
        # Signal/IncomingMessage/SignalMessage are shared across every
        # account (same channel data, see the multi-account plan) -- unlike
        # Trade there is no account_id to scope the wipe to. Refuse whenever
        # a process is currently connected to a *different* account
        # (concurrent-execution "Phase 2": several accounts can each have
        # their own real worker process connected at once now, not just a
        # single global "live" one -- see AccountService.list_connected),
        # so a simulation cleanup run as one account can never reach into
        # another currently-connected (possibly real-money) account's
        # signal history.
        other_connected = [
            a for a in AccountService(session).list_connected() if a.id != account.id
        ]
        if other_connected:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot reset shared signal history while another account "
                    "has a process connected to it right now."
                ),
            )

    if payload.scope in ("account", "all"):
        broker.reset()

    if payload.scope in ("trades", "all"):
        session.exec(delete(Trade).where(Trade.account_id == account.id))  # type: ignore[arg-type]
        session.exec(delete(SignalMessage))
        session.exec(delete(Signal))
        session.exec(delete(IncomingMessage))
        session.commit()

    return {"reset": payload.scope}
