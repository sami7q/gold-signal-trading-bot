"""botup.md v4 §6 test_cap20_preexisting: a coin open at start is ignored until the leader is flat."""

from decimal import Decimal

from loco_copy.constants import Decision
from loco_copy.models import LeaderSnapshot
from tests.conftest import T0, F


async def test_a_position_held_at_start_is_ignored_until_flat_then_copied(cap20):
    h = cap20()
    h.tracker.seed(LeaderSnapshot({"HYPE": Decimal(1000)}, {}, Decimal(0), T0))
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 1000)) == []  # an add to what he already held
    assert await h.fill(F(1000, "HYPE", "A", 40, 500, 2000)) == []
    assert await h.fill(F(2000, "HYPE", "A", 40, 1500, 1500)) == []  # flat: from now on, normal
    assert h.exchange.positions() == [] and h.store.copies() == []
    assert await h.fill(F(3000, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    assert h.copy("HYPE", h.tracker.state("HYPE").episode_id).qty_open == 24
