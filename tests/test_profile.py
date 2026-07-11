import httpx

from tests.conftest import DATASET, DOMAIN

CHUNK_AGGREGATES = [
    {
        "nn_0": "90",
        "d_0": "90",
        "nn_1": "100",
        "d_1": "50",
        "mn_1": "2018-01-01T00:00:00.000",
        "mx_1": "2026-06-30T00:00:00.000",
        "nn_2": "80",
        "d_2": "70",
        "mn_2": "-122.4",
        "mx_2": "-122.2",
        "av_2": "-122.3",
    }
]

TOP_VALUES = [
    {"offense_id": "THEFT", "count": "60"},
    {"offense_id": "ASSAULT", "count": "30"},
]


def is_chunk_query(params):
    return "count(distinct" in params.get("$select", "") and "$group" not in params


def is_top_values_for(field):
    return lambda params: params.get("$group") == field


def setup_happy_portal(fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]
    fake_portal.stub(is_chunk_query, CHUNK_AGGREGATES)
    fake_portal.stub(is_top_values_for("offense_id"), TOP_VALUES)


def by_field(profile):
    return {c["field_name"]: c for c in profile["columns"]}


def test_profile_shape(provider, fake_portal):
    setup_happy_portal(fake_portal)
    profile = provider.profile_dataset(DOMAIN, DATASET)
    assert profile["row_count"] == 100
    cols = by_field(profile)

    offense = cols["offense_id"]
    assert offense["type"] == "text"
    assert offense["null_rate"] == 0.1
    assert offense["distinct_count"] == 90
    assert offense["top_values"] == [
        {"value": "THEFT", "count": 60},
        {"value": "ASSAULT", "count": 30},
    ]

    date_col = cols["offense_date"]
    assert date_col["null_rate"] == 0.0
    assert date_col["min"] == "2018-01-01T00:00:00.000"
    assert date_col["max"] == "2026-06-30T00:00:00.000"
    assert "top_values" not in date_col

    num = cols["longitude"]
    assert num["null_rate"] == 0.2
    assert num["min"] == -122.4
    assert num["max"] == -122.2
    assert num["avg"] == -122.3


def test_profile_skips_system_columns(provider, fake_portal):
    fake_portal.views[DATASET] = dict(
        fake_portal.views[DATASET],
        columns=fake_portal.views[DATASET]["columns"]
        + [{"fieldName": ":@computed_region_x", "name": "Region", "dataTypeName": "number"}],
    )
    setup_happy_portal(fake_portal)
    profile = provider.profile_dataset(DOMAIN, DATASET)
    assert ":@computed_region_x" not in by_field(profile)
    for path, params in fake_portal.requests:
        assert ":@computed_region_x" not in params.get("$select", "")


def test_top_values_skipped_for_high_cardinality(provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]
    aggregates = [dict(CHUNK_AGGREGATES[0], d_0="5000")]
    fake_portal.stub(is_chunk_query, aggregates)
    profile = provider.profile_dataset(DOMAIN, DATASET)
    assert "top_values" not in by_field(profile)["offense_id"]
    assert not any(
        params.get("$group") == "offense_id" for _, params in fake_portal.requests
    )


def test_chunk_failure_falls_back_per_column(provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]

    fake_portal.stub(
        lambda p: is_chunk_query(p) and p.get("$select", "").count("count(") > 3,
        lambda p: httpx.Response(400, json={"error": True, "message": "query too complex"}),
    )
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "offense_id" in p.get("$select", ""),
        [{"nn_0": "90", "d_0": "600"}],
    )
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "offense_date" in p.get("$select", ""),
        [{"nn_1": "100", "d_1": "50", "mn_1": "2018-01-01T00:00:00.000",
          "mx_1": "2026-06-30T00:00:00.000"}],
    )
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "longitude" in p.get("$select", ""),
        lambda p: httpx.Response(400, json={"error": True, "message": "no aggregates on this"}),
    )

    profile = provider.profile_dataset(DOMAIN, DATASET)
    cols = by_field(profile)
    assert cols["offense_id"]["distinct_count"] == 600
    assert cols["offense_date"]["min"] == "2018-01-01T00:00:00.000"
    assert "no aggregates on this" in cols["longitude"]["error"]


def test_profile_is_cached(provider, fake_portal):
    setup_happy_portal(fake_portal)
    provider.profile_dataset(DOMAIN, DATASET)
    before = len(fake_portal.requests)
    provider.profile_dataset(DOMAIN, DATASET)
    assert len(fake_portal.requests) == before


def test_column_failure_degrades_to_no_distinct(provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]
    fake_portal.stub(
        lambda p: is_chunk_query(p) and p.get("$select", "").count("count(") > 3,
        lambda p: httpx.Response(400, json={"error": True, "message": "query too complex"}),
    )
    # offense_date keeps failing while count(distinct) is requested...
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "offense_date" in p.get("$select", ""),
        lambda p: httpx.Response(400, json={"error": True, "message": "aggregate timed out"}),
    )
    # ...but succeeds without it
    fake_portal.stub(
        lambda p: "count(offense_date)" in p.get("$select", "")
        and "count(distinct" not in p.get("$select", ""),
        [{"nn_1": "100", "mn_1": "2018-01-01T00:00:00.000",
          "mx_1": "2026-06-30T00:00:00.000"}],
    )
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "offense_id" in p.get("$select", ""),
        [{"nn_0": "90", "d_0": "90"}],
    )
    fake_portal.stub(
        lambda p: is_chunk_query(p) and "longitude" in p.get("$select", ""),
        [{"nn_2": "80", "d_2": "70", "mn_2": "-122.4", "mx_2": "-122.2",
          "av_2": "-122.3"}],
    )
    fake_portal.stub(is_top_values_for("offense_id"), TOP_VALUES)

    profile = provider.profile_dataset(DOMAIN, DATASET)
    cols = by_field(profile)
    date = cols["offense_date"]
    assert date["null_rate"] == 0.0
    assert date["min"] == "2018-01-01T00:00:00.000"
    assert "distinct_count" not in date
    assert "distinct count skipped" in date["error"]
    assert "aggregate timed out" in date["error"]
    assert cols["offense_id"]["distinct_count"] == 90
    assert cols["offense_id"]["top_values"] == [
        {"value": "THEFT", "count": 60},
        {"value": "ASSAULT", "count": 30},
    ]


def test_profile_aggregates_fail_fast(fake_portal, config):
    from socrata_mcp.cache import DiskCache
    from socrata_mcp.http_client import HttpClient
    from socrata_mcp.providers.socrata import SocrataProvider

    http = HttpClient(
        config, transport=httpx.MockTransport(fake_portal.handler),
        sleep=lambda s: None,
    )
    provider = SocrataProvider(config=config, http=http, cache=DiskCache(config.cache_dir))
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]
    attempts = []

    def unreachable(params):
        attempts.append(1)
        raise httpx.ConnectError("simulated timeout")

    fake_portal.stub(is_chunk_query, unreachable)
    provider.profile_dataset(DOMAIN, DATASET)
    # 1 chunk probe + 3 per-column probes, two transport attempts each —
    # the fallback ladder is the retry strategy, not the transport loop
    assert len(attempts) == 8
