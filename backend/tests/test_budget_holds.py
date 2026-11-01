"""The operator command that settles unknown holds from billing evidence."""

import uuid

import httpx
import pytest
from test_dispatch import dispatch as dispatch_fixture
from test_dispatch import grant, run
from test_memory import owner
from test_studio_images import account, designed, meta, policy, reservation
from test_worker import database as database_fixture

from polymath.budget_holds import HoldError, list_holds, main, settle_hold
from polymath.studio_images import StudioImages

database = database_fixture
dispatch = dispatch_fixture
pytestmark = pytest.mark.integration


def test_an_unknown_picture_is_listed_then_settled_from_evidence(database):
    account(database)
    token = owner(database)
    programme = designed(database, token)

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    transport, _ = meta(timeout)
    images = StudioImages(database, policy(), transport=transport)
    image_id, reservation_id = images.request(token, programme)
    assert images.execute(image_id, reservation_id) == "unknown"
    holds = list_holds(database)
    assert [(hold.reservation_id, hold.kind, hold.status) for hold in holds] == [
        (reservation_id, "image", "unknown")
    ]
    with pytest.raises(HoldError, match="invalid_evidence_key"):
        settle_hold(database, reservation_id, actual_microdollars=10_000, evidence="has spaces")
    with pytest.raises(HoldError, match="invalid_amount"):
        settle_hold(database, reservation_id, actual_microdollars=-1, evidence="dashboard")
    assert (
        settle_hold(database, reservation_id, actual_microdollars=10_000, evidence="meta-dashboard")
        == "image"
    )
    settled = reservation(database, image_id)
    assert settled.status == "settled" and settled.actual_microdollars == 10_000
    assert settled.settlement_key == "operator-reconciled:meta-dashboard"
    assert list_holds(database) == []
    with pytest.raises(HoldError, match="hold_not_unknown"):
        settle_hold(database, reservation_id, actual_microdollars=0, evidence="again")


def test_an_unknown_research_call_is_settled_through_its_journal(dispatch):
    jobs, budget, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    coordinator.mark_unknown(item, reason="timeout")
    [hold] = list_holds(engine)
    assert hold.kind == "research" and hold.status == "unknown"
    assert settle_hold(engine, hold.reservation_id, actual_microdollars=7, evidence="bill-1") == (
        "research"
    )
    assert budget.usage("meta").committed_microdollars == 7
    assert list_holds(engine) == []
    with pytest.raises(HoldError, match="hold_not_found"):
        settle_hold(engine, uuid.uuid4(), actual_microdollars=0, evidence="none")


def test_the_command_refuses_to_change_anything_without_confirm(capsys):
    assert main(["settle", str(uuid.uuid4()), "--actual-microdollars", "0", "--evidence", "x"]) == 2
    assert "add --confirm" in capsys.readouterr().out
