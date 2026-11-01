"""The rigour bar narrows which literature an investigation may use."""

from __future__ import annotations

import pytest
from test_literature import route
from test_run_api import api as api_fixture

from polymath.literature import OpenLiteratureTools
from polymath.rigour import LEVELS, OPENALEX_FILTERS

api = api_fixture


@pytest.mark.parametrize(
    ("level", "openalex_filter", "hosts"),
    [
        ("frontier", None, {"api.openalex.org", "export.arxiv.org"}),
        ("standard", "is_retracted:false", {"api.openalex.org", "export.arxiv.org"}),
        (
            "peer_reviewed",
            "is_retracted:false,primary_location.source.type:journal|conference",
            {"api.openalex.org"},
        ),
        (
            "established",
            "is_retracted:false,primary_location.source.type:journal|conference,cited_by_count:>9",
            {"api.openalex.org"},
        ),
    ],
)
async def test_each_level_sets_a_fixed_filter_and_leaves_out_preprints_above_standard(
    level, openalex_filter, hosts
):
    transport, requests = route()
    await OpenLiteratureTools(transport=transport, rigour=level).search(
        "self-organized criticality", "discovery"
    )
    assert {request.url.host for request in requests} == hosts
    openalex = next(request for request in requests if request.url.host == "api.openalex.org")
    assert openalex.url.params.get("filter") == openalex_filter


async def test_the_default_bar_is_standard():
    transport, requests = route()
    await OpenLiteratureTools(transport=transport).search("criticality", "discovery")
    openalex = next(request for request in requests if request.url.host == "api.openalex.org")
    assert openalex.url.params["filter"] == "is_retracted:false"


def test_unknown_levels_are_refused_and_every_level_has_a_filter():
    with pytest.raises(ValueError):
        OpenLiteratureTools(rigour="lenient")
    assert set(OPENALEX_FILTERS) == set(LEVELS)


@pytest.mark.integration
def test_an_investigation_records_its_bar_and_reports_it(api):
    from test_run_api import bootstrap, submit

    _, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    plain = submit(client, headers)
    assert plain.status_code == 201 and "rigour" not in plain.json()
    client.post(f"/api/v1/runs/{plain.json()['id']}/cancel", headers=headers)
    strict = submit(client, headers, rigour="peer_reviewed")
    assert strict.status_code == 201 and strict.json()["rigour"] == "peer_reviewed"
    assert client.get(f"/api/v1/runs/{strict.json()['id']}").json()["rigour"] == "peer_reviewed"
    client.post(f"/api/v1/runs/{strict.json()['id']}/cancel", headers=headers)
    invalid = submit(client, headers, rigour="lenient")
    assert invalid.status_code == 422
