"""SQLite repository (botup.md §8). WAL mode, Decimals stored as TEXT, schema created here (no Alembic).

Single process; every call happens on the event-loop thread (broker I/O is what runs in worker threads).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from loco_copy.constants import LIVE_STATUSES, CopyStatus, Decision, OrderStatus
from loco_copy.models import Copy, LeaderEvent, LeaderFill

SCHEMA_VERSION = 4  # v3: leader lane on every row (botup.md v3). v4: cap20 trade state, leader positions
#   on events, account equity history (botup.md v4/v5). Each step is upgraded in place, in order.

_FILLS = """
CREATE TABLE IF NOT EXISTS {name} (
    leader TEXT NOT NULL, tid INTEGER NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL, px TEXT NOT NULL,
    sz TEXT NOT NULL, start_position TEXT NOT NULL, time_ms INTEGER NOT NULL, oid INTEGER,
    received_at_ms INTEGER NOT NULL,
    PRIMARY KEY (leader, tid)  -- a trade between two copied leaders has the same tid on both sides
);
"""
_COPIES = """
CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, leader TEXT NOT NULL, coin TEXT NOT NULL, symbol TEXT NOT NULL,
    episode_id INTEGER NOT NULL, side INTEGER NOT NULL, status TEXT NOT NULL, qty_initial TEXT NOT NULL,
    qty_open TEXT NOT NULL, entry_price TEXT, tp_price TEXT, exit_price_avg TEXT,
    exit_qty_total TEXT NOT NULL DEFAULT '0', realized_pnl TEXT NOT NULL DEFAULT '0',
    fees TEXT NOT NULL DEFAULT '0', funding TEXT NOT NULL DEFAULT '0', margin TEXT, opened_at INTEGER,
    closed_at INTEGER, close_reason TEXT, leader_entry_px TEXT NOT NULL, slippage_bps TEXT,
    latency_detect_ms INTEGER, latency_order_ms INTEGER, {v4_copy_cols}
    UNIQUE(leader, coin, episode_id)
);
"""
#: Columns added by v4: cap20 trade state (name, SQL type + default).
V4_COPY_COLS = (("strategy", "TEXT NOT NULL DEFAULT 'first_fill'"), ("ratio_r", "TEXT"), ("cap_notional", "TEXT"),
                ("pending_qty", "TEXT NOT NULL DEFAULT '0'"), ("capped", "INTEGER NOT NULL DEFAULT 0"),
                ("qty_at_cap", "TEXT"), ("leader_pos_at_cap", "TEXT"), ("adds_done", "INTEGER NOT NULL DEFAULT 0"),
                ("cost_notional", "TEXT NOT NULL DEFAULT '0'"), ("leader_pos", "TEXT"))
V4_EVENT_COLS = (("pos_before", "TEXT"), ("pos_after", "TEXT"), ("our_px", "TEXT"))
_COPIES = _COPIES.replace("{v4_copy_cols}", " ".join(f"{n} {d}," for n, d in V4_COPY_COLS))
#: The v2 copy columns (what the v3 migration copies over).
_COPY_COLS = ("id", "coin", "symbol", "episode_id", "side", "status", "qty_initial", "qty_open", "entry_price",
              "tp_price", "exit_price_avg", "exit_qty_total", "realized_pnl", "fees", "funding", "margin",
              "opened_at", "closed_at", "close_reason", "leader_entry_px", "slippage_bps", "latency_detect_ms",
              "latency_order_ms")
_FILL_COLS = ("tid", "coin", "side", "px", "sz", "start_position", "time_ms", "oid", "received_at_ms")

#: v2 state keys that belong to one lane: renamed `<key>:loco` by the v3 migration.
LANE_STATE_KEYS = ("tracker", "last_fill_ms", "leader_snapshot", "leader_peak", "leader_breaker_tripped",
                   "paper_broker", "mode_override", "day_start_date", "day_start_equity")

_SCHEMA = _FILLS.format(name="fills") + _COPIES.format(name="copies") + """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fills_time ON fills(time_ms);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT NOT NULL, episode_id INTEGER NOT NULL, type TEXT NOT NULL,
    side INTEGER NOT NULL, fraction TEXT, leader_px TEXT NOT NULL, leader_time_ms INTEGER NOT NULL,
    detected_at_ms INTEGER NOT NULL, tid INTEGER NOT NULL, decision TEXT NOT NULL, rejection TEXT, detail TEXT,
    leader TEXT NOT NULL DEFAULT 'loco', pos_before TEXT, pos_after TEXT, our_px TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_detected ON events(detected_at_ms);
CREATE TABLE IF NOT EXISTS equity_history (
    account TEXT NOT NULL, ts INTEGER NOT NULL, equity TEXT NOT NULL, PRIMARY KEY (account, ts)
);
CREATE INDEX IF NOT EXISTS ix_copies_status ON copies(status);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, copy_id INTEGER REFERENCES copies(id), client_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL, requested_qty TEXT NOT NULL, filled_qty TEXT, avg_price TEXT, fee TEXT, status TEXT NOT NULL,
    error TEXT, sent_at_ms INTEGER NOT NULL, filled_at_ms INTEGER
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT NOT NULL, type TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_log_ts ON log(ts);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pf_seen (
    wallet TEXT NOT NULL, tid INTEGER NOT NULL, time_ms INTEGER NOT NULL, PRIMARY KEY (wallet, tid)
);
CREATE TABLE IF NOT EXISTS pf_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT NOT NULL, tid INTEGER NOT NULL, coin TEXT NOT NULL,
    side TEXT NOT NULL, px REAL NOT NULL, sz REAL NOT NULL, start_pos REAL NOT NULL, leader_time_ms INTEGER NOT NULL,
    detected_at_ms INTEGER NOT NULL, real_px REAL, base_action TEXT NOT NULL, base_qty REAL NOT NULL,
    real_action TEXT NOT NULL, real_qty REAL NOT NULL, actions TEXT
);
CREATE INDEX IF NOT EXISTS ix_pf_events_detected ON pf_events(detected_at_ms);
CREATE TABLE IF NOT EXISTS pf_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ledger TEXT NOT NULL, wallet TEXT NOT NULL, coin TEXT NOT NULL,
    side INTEGER NOT NULL, opened_ms INTEGER, closed_ms INTEGER NOT NULL, pnl REAL, fees REAL NOT NULL,
    max_notional REAL NOT NULL, cap REAL, r REAL, adds INTEGER NOT NULL, capped INTEGER NOT NULL,
    reason TEXT NOT NULL, exit_px REAL
);
CREATE INDEX IF NOT EXISTS ix_pf_trades_closed ON pf_trades(ledger, closed_ms);
CREATE TABLE IF NOT EXISTS pf_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ledger TEXT NOT NULL, wallet TEXT NOT NULL, coin TEXT NOT NULL,
    kind TEXT NOT NULL, qty REAL NOT NULL, px REAL NOT NULL, notional REAL NOT NULL,
    fee REAL NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pf_orders_ledger ON pf_orders(ledger, ts);
CREATE TABLE IF NOT EXISTS pf_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT NOT NULL, coin TEXT NOT NULL, trade_t0 INTEGER NOT NULL,
    ts INTEGER NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL, text TEXT NOT NULL, price REAL, ret REAL
);
CREATE INDEX IF NOT EXISTS ix_pf_alerts_trade ON pf_alerts(wallet, coin, ts);
CREATE TABLE IF NOT EXISTS pf_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wallet TEXT NOT NULL, coin TEXT NOT NULL, trade_t0 INTEGER NOT NULL,
    ts INTEGER NOT NULL, kind TEXT NOT NULL, probability REAL, risk_score REAL, snapshot TEXT NOT NULL, ai TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS ix_pf_analysis_trade ON pf_analysis(wallet, coin, ts);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

_DEC_COPY = ("ratio_r", "cap_notional", "pending_qty", "qty_at_cap", "leader_pos_at_cap", "cost_notional",
             "leader_pos", "qty_initial", "qty_open", "entry_price", "tp_price", "exit_price_avg", "exit_qty_total",
             "realized_pnl", "fees", "funding", "margin", "leader_entry_px", "slippage_bps")


def now_ms() -> int:
    return int(time.time() * 1000)


def lane_key(key: str, lane: str) -> str:
    """State key of one lane: `tracker:3ea`, `mode_override:loco` ..."""
    return f"{key}:{lane}"


def _d(v: Any) -> Decimal | None:
    return None if v is None else Decimal(v)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


class Store:
    def __init__(self, path: Path | str, *, cross_thread: bool = False) -> None:
        """cross_thread: the connection may be used from a thread other than its creator's, one at a
        time. FastAPI runs a request's sync dependencies and handler on different pool threads."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=not cross_thread)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._db.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            if row["version"] in (1, 2):
                self._migrate_to_v3()  # v1 -> v2 was additive (users / sessions, created above)
            if self._db.execute("SELECT version FROM schema_version").fetchone()["version"] == 3:
                self._migrate_to_v4()
            version = self._db.execute("SELECT version FROM schema_version").fetchone()["version"]
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"database schema v{version} != code v{SCHEMA_VERSION}")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_events_leader ON events(leader, detected_at_ms)")
        # botup.md §V10 (additive, no version bump): what every simulator group did with a wallet fill, as JSON
        if "actions" not in {r["name"] for r in self._db.execute("PRAGMA table_info(pf_events)")}:
            try:
                self._db.execute("ALTER TABLE pf_events ADD COLUMN actions TEXT")
            except sqlite3.OperationalError as exc:  # the other process (bot / API) added it first
                if "duplicate column" not in str(exc):
                    raise
        self._db.commit()

    def _migrate_to_v4(self) -> None:
        """v3 -> v4: purely additive columns (a copy made by v3 is a first_fill copy)."""
        db = self._db
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION:
                db.commit()
                return
            for table, cols in (("copies", V4_COPY_COLS), ("events", V4_EVENT_COLS)):
                have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                for name, decl in cols:
                    if name not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            db.commit()
        except Exception:
            db.rollback()
            raise

    def _migrate_to_v3(self) -> None:
        """v2 -> v3 in one transaction: everything that exists belongs to lane `loco` (botup.md v3 §5.2).
        SQLite cannot change a primary key or a UNIQUE constraint in place, so `fills` and `copies` are
        rebuilt (copy ids are kept: orders point at them)."""
        db = self._db
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT version FROM schema_version").fetchone()["version"] >= 3:
                db.commit()  # the bot and the API start together: the other one migrated while we waited
                return
            if "leader" not in {r["name"] for r in db.execute("PRAGMA table_info(events)")}:
                db.execute("ALTER TABLE events ADD COLUMN leader TEXT NOT NULL DEFAULT 'loco'")
            cols = ", ".join(_FILL_COLS)
            db.execute(_FILLS.format(name="fills_v3"))
            db.execute(f"INSERT INTO fills_v3 (leader, {cols}) SELECT 'loco', {cols} FROM fills")
            db.execute("DROP TABLE fills")
            db.execute("ALTER TABLE fills_v3 RENAME TO fills")
            db.execute("CREATE INDEX IF NOT EXISTS ix_fills_time ON fills(time_ms)")
            cols = ", ".join(_COPY_COLS)
            db.execute(_COPIES.format(name="copies_v3"))
            db.execute(f"INSERT INTO copies_v3 (leader, {cols}) SELECT 'loco', {cols} FROM copies")
            db.execute("DROP TABLE copies")
            db.execute("ALTER TABLE copies_v3 RENAME TO copies")
            db.execute("CREATE INDEX IF NOT EXISTS ix_copies_status ON copies(status)")
            for key in LANE_STATE_KEYS:
                db.execute("UPDATE state SET key=? WHERE key=? AND NOT EXISTS (SELECT 1 FROM state WHERE key=?)",
                           (f"{key}:loco", key, f"{key}:loco"))
            db.execute("UPDATE schema_version SET version=3")
            db.commit()
        except Exception:
            db.rollback()
            raise

    def close(self) -> None:
        self._db.close()

    def backup(self, target: Path) -> Path:
        """SQLite online backup: consistent even while the bot is writing."""
        target.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(target))
        try:
            self._db.backup(dst)
        finally:
            dst.close()
        return target

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._db
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    # -- fills ------------------------------------------------------------------------------------------------

    def insert_fill(self, f: LeaderFill, *, leader: str = "loco") -> bool:
        """True if this (leader, tid) is new (dedupe key)."""
        with self.tx() as db:
            cur = db.execute(
                f"INSERT OR IGNORE INTO fills (leader, {', '.join(_FILL_COLS)}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (leader, f.tid, f.coin, f.side, str(f.px), str(f.sz), str(f.start_position), f.time_ms, f.oid,
                 f.received_at_ms))
            return cur.rowcount == 1

    def has_fill(self, tid: int, *, leader: str = "loco") -> bool:
        return self._db.execute("SELECT 1 FROM fills WHERE leader=? AND tid=?", (leader, tid)).fetchone() is not None

    def max_fill_time(self, *, leader: str | None = None) -> int | None:
        q, args = "SELECT MAX(time_ms) AS m FROM fills", []
        if leader is not None:
            q, args = q + " WHERE leader=?", [leader]
        row = self._db.execute(q, args).fetchone()
        return row["m"] if row and row["m"] is not None else None

    # -- events -----------------------------------------------------------------------------------------------

    def insert_event(self, e: LeaderEvent, decision: Decision, rejection: str | None = None,
                     detail: str | None = None, *, leader: str = "loco", our_px: Decimal | None = None) -> int:
        """`our_px`: the price our order for this event filled at (or would have, when observing)."""
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO events (coin, episode_id, type, side, fraction, leader_px, leader_time_ms, "
                "detected_at_ms, tid, decision, rejection, detail, leader, pos_before, pos_after, our_px) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.coin, e.episode_id, str(e.type), e.side, _s(e.fraction), str(e.leader_px), e.leader_time_ms,
                 e.detected_at_ms, e.tid, str(decision), rejection, detail, leader, _s(e.pos_before),
                 _s(e.pos_after), _s(our_px)))
            return int(cur.lastrowid or 0)

    def events_since(self, since_ms: int, *, leader: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM events WHERE detected_at_ms >= ?"
        args: list[Any] = [since_ms]
        if leader is not None:
            q, args = q + " AND leader=?", [*args, leader]
        return list(self._db.execute(q + " ORDER BY id", args))

    def recent_events(self, *, limit: int, decision: str | None = None,
                      leader: str | None = None) -> list[sqlite3.Row]:
        where: list[str] = []
        args: list[Any] = []
        if decision:
            where, args = [*where, "decision=?"], [*args, decision]
        if leader:
            where, args = [*where, "leader=?"], [*args, leader]
        q = "SELECT * FROM events" + (f" WHERE {' AND '.join(where)}" if where else "")
        return list(self._db.execute(f"{q} ORDER BY detected_at_ms DESC, id DESC LIMIT ?", [*args, limit]))

    def recent_logs(self, *, limit: int, min_level: str | None = None) -> list[sqlite3.Row]:
        if min_level == "WARNING":
            return list(self._db.execute("SELECT * FROM log WHERE level IN ('WARNING','ERROR','CRITICAL') "
                                         "ORDER BY id DESC LIMIT ?", (limit,)))
        return list(self._db.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)))

    # -- copies -----------------------------------------------------------------------------------------------

    def _row_to_copy(self, r: sqlite3.Row) -> Copy:
        data = dict(r)
        for k in _DEC_COPY:
            data[k] = _d(data[k])
        data["capped"] = bool(data["capped"])
        for k in ("pending_qty", "cost_notional"):
            data[k] = data[k] if data[k] is not None else Decimal(0)
        return Copy(**data)

    def insert_copy(self, c: Copy) -> Copy:
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO copies (leader, coin, symbol, episode_id, side, status, qty_initial, qty_open, "
                "entry_price, tp_price, margin, opened_at, leader_entry_px, latency_detect_ms, strategy, ratio_r, "
                "cap_notional, pending_qty, leader_pos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c.leader, c.coin, c.symbol, c.episode_id, c.side, c.status, str(c.qty_initial), str(c.qty_open),
                 _s(c.entry_price), _s(c.tp_price), _s(c.margin), c.opened_at, str(c.leader_entry_px),
                 c.latency_detect_ms, c.strategy, _s(c.ratio_r), _s(c.cap_notional), str(c.pending_qty),
                 _s(c.leader_pos)))
            c.id = int(cur.lastrowid or 0)
        return c

    def update_copy(self, c: Copy) -> None:
        cols = ("status", "qty_initial", "qty_open", "entry_price", "tp_price", "exit_price_avg", "exit_qty_total",
                "realized_pnl", "fees", "funding", "margin", "opened_at", "closed_at", "close_reason",
                "slippage_bps", "latency_detect_ms", "latency_order_ms", "ratio_r", "cap_notional", "pending_qty",
                "capped", "qty_at_cap", "leader_pos_at_cap", "adds_done", "cost_notional", "leader_pos")
        values = [_s(getattr(c, k)) if isinstance(getattr(c, k), Decimal) else getattr(c, k) for k in cols]
        values = [int(v) if isinstance(v, bool) else v for v in values]
        with self.tx() as db:
            db.execute(f"UPDATE copies SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*values, c.id))

    def get_copy(self, coin: str, episode_id: int, *, leader: str = "loco") -> Copy | None:
        r = self._db.execute("SELECT * FROM copies WHERE leader=? AND coin=? AND episode_id=?",
                             (leader, coin, episode_id)).fetchone()
        return self._row_to_copy(r) if r else None

    def copy_by_id(self, copy_id: int) -> Copy | None:
        r = self._db.execute("SELECT * FROM copies WHERE id=?", (copy_id,)).fetchone()
        return self._row_to_copy(r) if r else None

    # -- read-only lookups for the dashboard's detail views ---------------------------------------------------

    def orders_for_copy(self, copy_id: int) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM orders WHERE copy_id=? ORDER BY id", (copy_id,)))

    def episode_events(self, coin: str, episode_id: int, *, leader: str = "loco") -> list[sqlite3.Row]:
        return list(self._db.execute(
            "SELECT * FROM events WHERE leader=? AND coin=? AND episode_id=? ORDER BY leader_time_ms, id",
            (leader, coin, episode_id)))

    def event_by_id(self, event_id: int) -> sqlite3.Row | None:
        r: sqlite3.Row | None = self._db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return r

    def fill_by_tid(self, tid: int, *, leader: str = "loco") -> sqlite3.Row | None:
        r: sqlite3.Row | None = self._db.execute("SELECT * FROM fills WHERE leader=? AND tid=?",
                                                 (leader, tid)).fetchone()
        return r

    def coin_fills_between(self, coin: str, start_ms: int, end_ms: int, *, leader: str = "loco") -> list[sqlite3.Row]:
        return list(self._db.execute(
            "SELECT * FROM fills WHERE time_ms BETWEEN ? AND ? AND leader=? AND coin=? ORDER BY time_ms",
            (start_ms, end_ms, leader, coin)))

    def record_equity(self, account: str, ts: int, equity: Decimal) -> None:
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO equity_history (account, ts, equity) VALUES (?,?,?)",
                       (account, ts, str(equity)))

    def equity_history(self, account: str, since_ms: int = 0) -> list[tuple[int, Decimal]]:
        return [(r["ts"], Decimal(r["equity"])) for r in self._db.execute(
            "SELECT ts, equity FROM equity_history WHERE account=? AND ts >= ? ORDER BY ts", (account, since_ms))]

    def logs_between(self, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM log WHERE ts BETWEEN ? AND ? ORDER BY id", (start_ms, end_ms)))

    def copies_with_status(self, *statuses: str, leader: str | None = None) -> list[Copy]:
        """`leader=None` means every lane."""
        q = f"SELECT * FROM copies WHERE status IN ({','.join('?' * len(statuses))})"
        args: list[Any] = list(statuses)
        if leader is not None:
            q, args = q + " AND leader=?", [*args, leader]
        return [self._row_to_copy(r) for r in self._db.execute(q + " ORDER BY id", args)]

    def live_copies(self, *, leader: str | None = None) -> list[Copy]:
        return self.copies_with_status(*LIVE_STATUSES, leader=leader)

    def open_copy_for_coin(self, coin: str, *, leader: str = "loco") -> Copy | None:
        r = self._db.execute(
            f"SELECT * FROM copies WHERE leader=? AND coin=? AND status IN ({','.join('?' * len(LIVE_STATUSES))})",
            (leader, coin, *LIVE_STATUSES)).fetchone()
        return self._row_to_copy(r) if r else None

    def copies(self, *, since_ms: int = 0, open_only: bool = False, leader: str | None = None) -> list[Copy]:
        q = "SELECT * FROM copies WHERE COALESCE(opened_at, 0) >= ?"
        args: list[Any] = [since_ms]
        if open_only:
            q += f" AND status IN ({','.join('?' * len(LIVE_STATUSES))})"
            args += list(LIVE_STATUSES)
        if leader is not None:
            q, args = q + " AND leader=?", [*args, leader]
        return [self._row_to_copy(r) for r in self._db.execute(q + " ORDER BY id", args)]

    # -- orders -----------------------------------------------------------------------------------------------

    def get_order(self, client_id: str) -> sqlite3.Row | None:
        r: sqlite3.Row | None = self._db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()
        return r

    def insert_order(self, *, copy_id: int | None, client_id: str, kind: str, requested_qty: Decimal) -> bool:
        """False if the client_id already exists (idempotency: the caller must use the existing order)."""
        try:
            with self.tx() as db:
                db.execute("INSERT INTO orders (copy_id, client_id, kind, requested_qty, status, sent_at_ms) "
                           "VALUES (?,?,?,?,?,?)", (copy_id, client_id, kind, str(requested_qty),
                                                    str(OrderStatus.SENT), now_ms()))
            return True
        except sqlite3.IntegrityError:
            return False

    def finish_order(self, client_id: str, status: OrderStatus, *, filled_qty: Decimal | None = None,
                     avg_price: Decimal | None = None, fee: Decimal | None = None, error: str | None = None,
                     filled_at_ms: int | None = None) -> None:
        with self.tx() as db:
            db.execute("UPDATE orders SET status=?, filled_qty=?, avg_price=?, fee=?, error=?, filled_at_ms=? "
                       "WHERE client_id=?", (str(status), _s(filled_qty), _s(avg_price), _s(fee), error,
                                             filled_at_ms, client_id))

    def orders_with_status(self, status: OrderStatus) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM orders WHERE status=? ORDER BY id", (str(status),)))

    # -- state / log ------------------------------------------------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        r = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def state_keys(self, prefix: str) -> list[str]:
        rows = self._db.execute("SELECT key FROM state WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))
        return [r["key"] for r in rows]

    def set_state(self, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO state(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value, default=str)))

    def log(self, level: str, type_: str, payload: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO log (ts, level, type, payload) VALUES (?,?,?,?)",
                       (now_ms(), level, type_, json.dumps(payload, default=str)))

    def logs_since(self, since_ms: int, *, min_level: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM log WHERE ts >= ?"
        args: list[Any] = [since_ms]
        levels = {"WARNING": ("WARNING", "ERROR", "CRITICAL"), "ERROR": ("ERROR", "CRITICAL")}.get(min_level or "")
        if levels:
            q += f" AND level IN ({','.join('?' * len(levels))})"
            args += list(levels)
        return list(self._db.execute(q + " ORDER BY id", args))


    # -- the «new trio» portfolio simulator (botup.md §V6; tables created above, additive) ------------------

    def pf_mark_seen(self, wallet: str, f: LeaderFill) -> bool:
        """True the first time this wallet fill (tid) is seen."""
        with self.tx() as db:
            cur = db.execute("INSERT OR IGNORE INTO pf_seen (wallet, tid, time_ms) VALUES (?,?,?)",
                             (wallet, f.tid, f.time_ms))
            return cur.rowcount == 1

    def pf_prune_seen(self, before_ms: int) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM pf_seen WHERE time_ms < ?", (before_ms,))

    def pf_insert_event(self, row: dict[str, Any]) -> None:
        cols = ("wallet", "tid", "coin", "side", "px", "sz", "start_pos", "leader_time_ms", "detected_at_ms",
                "real_px", "base_action", "base_qty", "real_action", "real_qty", "actions")
        with self.tx() as db:
            db.execute(f"INSERT INTO pf_events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       tuple(row.get(c) for c in cols))

    def pf_insert_trades(self, rows: list[dict[str, Any]]) -> None:
        cols = ("ledger", "wallet", "coin", "side", "opened_ms", "closed_ms", "pnl", "fees", "max_notional", "cap",
                "r", "adds", "capped", "reason", "exit_px")
        with self.tx() as db:
            db.executemany(f"INSERT INTO pf_trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           [tuple(r.get(c) for c in cols) for r in rows])

    def pf_insert_orders(self, rows: list[dict[str, Any]]) -> None:
        cols = ("ledger", "wallet", "coin", "kind", "qty", "px", "notional", "fee", "ts")
        with self.tx() as db:
            db.executemany(f"INSERT INTO pf_orders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           [tuple(r.get(c) for c in cols) for r in rows])

    def pf_orders_since(self, since_ms: int, *, ledger: str, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM pf_orders WHERE ledger = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
                                     (ledger, since_ms, limit)))

    def pf_events_for(self, wallet: str, coin: str, since_ms: int, limit: int = 2000) -> list[sqlite3.Row]:
        return list(self._db.execute(
            "SELECT * FROM pf_events WHERE wallet = ? AND coin = ? AND leader_time_ms >= ? ORDER BY leader_time_ms, id "
            "LIMIT ?", (wallet, coin, since_ms, limit)))

    def pf_orders_for(self, ledger: str, wallet: str, coin: str, since_ms: int) -> list[sqlite3.Row]:
        return list(self._db.execute(
            "SELECT * FROM pf_orders WHERE ledger = ? AND wallet = ? AND coin = ? AND ts >= ? ORDER BY ts, id",
            (ledger, wallet, coin, since_ms)))

    def pf_insert_alert(self, row: dict[str, Any]) -> None:
        cols = ("wallet", "coin", "trade_t0", "ts", "code", "severity", "text", "price", "ret")
        with self.tx() as db:
            db.execute(f"INSERT INTO pf_alerts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       tuple(row.get(c) for c in cols))

    def pf_alerts_for(self, wallet: str, coin: str, since_ms: int, limit: int = 50) -> list[sqlite3.Row]:
        return list(self._db.execute(
            "SELECT * FROM pf_alerts WHERE wallet = ? AND coin = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
            (wallet, coin, since_ms, limit)))

    def pf_alerts_since(self, since_ms: int, limit: int = 100) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM pf_alerts WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                                     (since_ms, limit)))

    def pf_insert_analysis(self, row: dict[str, Any]) -> None:
        cols = ("wallet", "coin", "trade_t0", "ts", "kind", "probability", "risk_score", "snapshot", "ai", "meta")
        with self.tx() as db:
            db.execute(f"INSERT INTO pf_analysis ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       tuple(row.get(c) for c in cols))

    def pf_latest_analysis(self, wallet: str, coin: str, *, with_ai: bool = False) -> sqlite3.Row | None:
        q = "SELECT * FROM pf_analysis WHERE wallet = ? AND coin = ?" + (" AND ai IS NOT NULL" if with_ai else "")
        return self._db.execute(q + " ORDER BY id DESC LIMIT 1", (wallet, coin)).fetchone()

    def pf_trades_since(self, since_ms: int, *, ledger: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM pf_trades WHERE closed_ms >= ?"
        args: list[Any] = [since_ms]
        if ledger:
            q, args = q + " AND ledger = ?", [*args, ledger]
        return list(self._db.execute(q + " ORDER BY closed_ms DESC", args))

    def pf_events_since(self, since_ms: int, *, limit: int = 100000) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM pf_events WHERE detected_at_ms >= ? ORDER BY id DESC LIMIT ?",
                                     (since_ms, limit)))

    # -- dashboard users / sessions --------------------------------------------------------------------------

    def upsert_user(self, username: str, display_name: str, password_hash: str) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO users (username, display_name, password_hash, created_at) VALUES (?,?,?,?) "
                       "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, "
                       "password_hash=excluded.password_hash", (username, display_name, password_hash, now_ms()))

    def user_by_username(self, username: str) -> sqlite3.Row | None:
        r: sqlite3.Row | None = self._db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return r

    def users(self) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT id, username, display_name FROM users ORDER BY id"))

    def delete_user(self, username: str) -> bool:
        """Removes the login and signs it out everywhere. Returns False if there was no such user."""
        with self.tx() as db:
            row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            db.execute("DELETE FROM users WHERE id=?", (row["id"],))
            return True

    def create_session(self, token_hash: str, user_id: int, expires_at: int) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now_ms(),))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash, user_id, now_ms(), expires_at))

    def session_user(self, token_hash: str) -> sqlite3.Row | None:
        r: sqlite3.Row | None = self._db.execute(
            "SELECT u.id, u.username, u.display_name FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash=? AND s.expires_at >= ?", (token_hash, now_ms())).fetchone()
        return r

    def delete_session(self, token_hash: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def copy_is_closed(c: Copy) -> bool:
    return c.status.startswith("CLOSED_") or c.status in (CopyStatus.FAILED, CopyStatus.UNKNOWN)
