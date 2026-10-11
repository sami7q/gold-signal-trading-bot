from dataclasses import replace
from decimal import Decimal
from itertools import pairwise

from loco_copy.constants import EventType
from loco_copy.models import LeaderSnapshot
from loco_copy.tracker import LeaderTracker, chain_order, is_main_perp
from tests.conftest import T0, F


def types(events):
    return [e.type for e in events]


def run(t: LeaderTracker, fills):
    out = []
    for f in fills:
        out += t.on_fill(f)[0]
    return out


# -- the 4 reconstruction cases ported from the research's test_core.py ----------------------------------------

def test_open_add_close():
    ev = run(LeaderTracker(), [F(1, "BTC", "B", 100, 1, 0), F(2, "BTC", "B", 110, 1, 1), F(3, "BTC", "A", 120, 2, 2)])
    assert types(ev) == [EventType.OPEN, EventType.ADD, EventType.CLOSE]
    assert ev[0].side == 1 and ev[0].leader_px == 100 and len({e.episode_id for e in ev}) == 1


def test_flip_and_partial():
    ev = run(LeaderTracker(), [F(1, "ETH", "A", 100, 1, 0), F(2, "ETH", "B", 90, 2, -1),
                               F(3, "ETH", "A", 95, "0.5", 1), F(4, "ETH", "A", 97, "0.5", "0.5")])
    assert types(ev) == [EventType.OPEN, EventType.FLIP, EventType.OPEN, EventType.REDUCE, EventType.CLOSE]
    assert ev[0].side == -1 and ev[1].episode_id == ev[0].episode_id
    assert ev[2].side == 1 and ev[2].episode_id == ev[0].episode_id + 1 and ev[2].leader_px == 90
    assert ev[3].fraction == Decimal("0.5")


def test_skip_unknown_entry_and_spot():
    t = LeaderTracker()
    t.seed(LeaderSnapshot({"SOL": Decimal(2)}, {}, Decimal(0), T0))
    ev = run(t, [F(1, "SOL", "A", 10, 2, 2), F(2, "@107", "B", 1, 5, 0), F(3, "SOL", "B", 11, 1, 0),
                 F(4, "SOL", "A", 12, 1, 1)])
    assert types(ev) == [EventType.OPEN, EventType.CLOSE]
    assert ev[0].coin == "SOL" and ev[0].leader_px == 11


def test_open_position_is_tracked_until_closed():
    t = LeaderTracker()
    run(t, [F(1, "BTC", "B", 100, 1, 0)])
    assert t.position("BTC") == 1 and t.positions() == {"BTC": Decimal(1)}


# -- bot-specific rules -------------------------------------------------------------------------------------

def test_pre_existing_position_never_copied_even_when_added_to_or_reduced():
    t = LeaderTracker()
    t.seed(LeaderSnapshot({"HYPE": Decimal(5)}, {}, Decimal(0), T0))
    ev = run(t, [F(1, "HYPE", "B", 40, 1, 5), F(2, "HYPE", "A", 41, 3, 6)])
    assert ev == [] and not t.is_synced("HYPE")
    ev = run(t, [F(3, "HYPE", "A", 42, 3, 3)])  # flat once -> synced; nothing to close, we never opened it
    assert ev == [] and t.is_synced("HYPE")
    assert types(run(t, [F(4, "HYPE", "B", 43, 1, 0)])) == [EventType.OPEN]


def test_pre_existing_position_flipping_is_not_copied():
    t = LeaderTracker()
    t.seed(LeaderSnapshot({"HYPE": Decimal(5)}, {}, Decimal(0), T0))
    assert run(t, [F(1, "HYPE", "A", 40, 8, 5)]) == []  # 5 -> -3: synced, but this short was never opened by us
    ev = run(t, [F(2, "HYPE", "B", 39, 3, -3)])
    assert types(ev) == [EventType.CLOSE]  # recognised, engine will find no copy for it


def test_duplicate_tid_is_the_callers_job_but_replaying_a_fill_is_caught_as_drift():
    t = LeaderTracker()
    f = F(1, "BTC", "B", 100, 1, 0)
    run(t, [f])
    events, drift = t.on_fill(f)  # same fill again: its start (0) disagrees with our position (1)
    assert events == [] and drift is not None and not t.is_synced("BTC")


def test_out_of_order_fills_never_trade_on_a_guess():
    t = LeaderTracker()
    ev = run(t, [F(1, "BTC", "B", 100, 1, 0), F(3, "BTC", "A", 105, 1, 2), F(2, "BTC", "B", 101, 1, 1)])
    assert types(ev) == [EventType.OPEN]  # the out-of-order fills only cause a resync, no fake close/add
    assert not t.is_synced("BTC") or t.position("BTC") == 0


def test_snapshot_drift_needs_two_snapshots_then_resyncs_and_blocks_copies():
    t = LeaderTracker()
    run(t, [F(1, "BTC", "B", 100, 1, 0)])
    snap = LeaderSnapshot({"BTC": Decimal(3)}, {}, Decimal(0), T0 + 10_000)
    assert t.on_snapshot(snap) == []  # first disagreement: maybe a fill still in flight
    drift = t.on_snapshot(LeaderSnapshot({"BTC": Decimal(3)}, {}, Decimal(0), T0 + 30_000))
    assert len(drift) == 1 and t.position("BTC") == 3 and not t.is_synced("BTC")
    closed = run(t, [F(40_000, "BTC", "A", 99, 3, 3)])  # flat once: our copy of episode 1 must go now
    assert types(closed) == [EventType.CLOSE] and closed[0].episode_id == 1 and t.is_synced("BTC")
    assert types(run(t, [F(41_000, "BTC", "B", 98, 1, 0)])) == [EventType.OPEN]


# -- 2026-10-11, BTC: one short order filled in 15 pieces, delivered scrambled; the close was then missed --------

# (tid, startPosition, sz) exactly as Hyperliquid sent them, in the (time, tid) order the bot used to process them
BTC_OPEN_672 = [(49315850254644, "-0.01936", "0.003"), (51531519551723, "-0.03036", "0.003"),
                (233538180697405, "0.0", "0.00566"), (498961892425840, "-0.01366", "0.0057"),
                (623619207145292, "-0.00566", "0.008"), (636056239837631, "-0.02236", "0.008")]
BTC_OPEN_819 = [(257965078715348, "-0.04556", "0.003"), (270508563871242, "-0.03336", "0.0062"),
                (304823110836511, "-0.07346", "0.003"), (362271003012298, "-0.05156", "0.0189"),
                (449348291469194, "-0.04256", "0.003"), (581768322782115, "-0.07046", "0.003"),
                (750298189534454, "-0.07646", "0.42354"), (765598178810024, "-0.04856", "0.003"),
                (888818450628965, "-0.03956", "0.003")]


def _burst(t, rows, oid=570582805792):
    return [replace(F(t, "BTC", "A", 83933, sz, start, tid=tid), oid=oid) for tid, start, sz in rows]


def test_chain_order_restores_one_order_split_into_scrambled_fills():
    ordered = chain_order(_burst(819, BTC_OPEN_819) + _burst(672, BTC_OPEN_672))
    assert ordered[0].start_position == 0 and ordered[-1].new_position == Decimal("-0.5")
    assert all(a.new_position == b.start_position for a, b in pairwise(ordered))


def test_real_btc_short_is_opened_and_closed_with_loco():
    t = LeaderTracker()
    events, drifts = [], []
    close = [F(1_282_694, "BTC", "B", 83772, "0.5", "-0.5")]
    for burst in (_burst(672, BTC_OPEN_672), _burst(819, BTC_OPEN_819), close):
        for f in chain_order(burst):
            ev, drift = t.on_fill(f)
            events += ev
            drifts += [drift] if drift else []
    assert drifts == []
    # the other 14 pieces belong to the opening order: no ADD noise, just the open and the close
    assert types(events) == [EventType.OPEN, EventType.CLOSE]
    assert events[-1].episode_id == events[0].episode_id and events[-1].leader_px == 83772


def test_a_later_order_in_the_same_direction_is_still_an_add():
    t = LeaderTracker()
    later = replace(F(5000, "BTC", "A", 84000, "0.1", "-0.03336"), oid=999)
    ev = run(t, [*chain_order(_burst(672, BTC_OPEN_672)), later])
    assert types(ev) == [EventType.OPEN, EventType.ADD]


def test_close_is_followed_even_after_drift_mid_episode():
    # what happened live: the (time, tid) order caused drift right after the open -> coin unsynced -> the close
    # produced no event and the reconciler had to close our copy 61 s later
    t = LeaderTracker()
    events = run(t, sorted(_burst(672, BTC_OPEN_672), key=lambda f: f.tid) + sorted(_burst(819, BTC_OPEN_819),
                                                                                    key=lambda f: f.tid))
    assert types(events) == [EventType.OPEN] and not t.is_synced("BTC")
    t.on_snapshot(LeaderSnapshot({"BTC": Decimal("-0.5")}, {}, Decimal(0), T0 + 40_000))
    t.on_snapshot(LeaderSnapshot({"BTC": Decimal("-0.5")}, {}, Decimal(0), T0 + 60_000))
    closed = run(t, [F(1_282_694, "BTC", "B", 83772, "0.5", "-0.5")])
    assert types(closed) == [EventType.CLOSE] and closed[0].episode_id == events[0].episode_id


def test_snapshot_skips_coins_with_fills_after_the_request():
    t = LeaderTracker()
    run(t, [F(5_000, "BTC", "B", 100, 1, 0)])
    stale = LeaderSnapshot({}, {}, Decimal(0), T0 + 4_000)  # sent before that fill arrived
    assert t.on_snapshot(stale) == [] and t.on_snapshot(stale) == [] and t.position("BTC") == 1


def test_missed_close_detected_by_snapshot():
    t = LeaderTracker()
    run(t, [F(1, "BTC", "B", 100, 1, 0)])
    t.on_snapshot(LeaderSnapshot({}, {}, Decimal(0), T0 + 10_000))
    drift = t.on_snapshot(LeaderSnapshot({}, {}, Decimal(0), T0 + 30_000))
    assert drift[0].snapshot_position == 0 and t.position("BTC") == 0


def test_state_roundtrip():
    t = LeaderTracker()
    run(t, [F(1, "HYPE", "B", 40, "1.5", 0)])
    t2 = LeaderTracker.from_dict(t.to_dict())
    assert t2.position("HYPE") == Decimal("1.5") and t2.state("HYPE").episode_id == 1
    assert types(t2.on_fill(F(2, "HYPE", "A", 41, "1.5", "1.5"))[0]) == [EventType.CLOSE]


def test_market_filter():
    assert is_main_perp("HYPE") and is_main_perp("kPEPE")
    assert not is_main_perp("@107") and not is_main_perp("PURR/USDC") and not is_main_perp("xyz:TSLA")
