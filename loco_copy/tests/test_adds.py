from decimal import Decimal

from loco_copy.constants import CopyStatus, Decision
from tests.conftest import F


async def test_loco_add_is_logged_never_traded(harness):
    h = harness()
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.COPIED]
    before = h.store.get_copy("HYPE", 1)
    orders_before = h.paper.positions()
    h.paper.set_price("HYPEUSDT", Decimal("39"))
    assert await h.fill(F(1000, "HYPE", "B", 39, 30, 10)) == [Decision.IGNORED_ADD]
    after = h.store.get_copy("HYPE", 1)
    assert after.qty_open == before.qty_open and after.tp_price == before.tp_price
    assert after.status == CopyStatus.OPEN
    assert h.paper.positions()[0].qty == orders_before[0].qty
    ev = h.store.events_since(0)[-1]
    assert ev["decision"] == "IGNORED_ADD" and ev["type"] == "ADD"


async def test_follow_adds_true_is_refused_at_startup(tmp_path):
    import pytest

    from tests.conftest import make_settings

    with pytest.raises(ValueError, match="FOLLOW_ADDS"):
        make_settings(tmp_path, follow_adds=True)
