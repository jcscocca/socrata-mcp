import pytest

from socrata_mcp.errors import ValidationError
from socrata_mcp.providers.base import QuerySpec, WithinBox, WithinCircle
from socrata_mcp.soql import build_query, soql_quote


def build(**kwargs):
    defaults = dict(default_limit=100, max_rows=5000)
    default_limit = kwargs.pop("default_limit", defaults["default_limit"])
    max_rows = kwargs.pop("max_rows", defaults["max_rows"])
    return build_query(
        QuerySpec(**kwargs), default_limit=default_limit, max_rows=max_rows
    )


def test_structured_params():
    built = build(
        select=["offense", "count(*) as n"],
        where="beat = 'B1'",
        group=["offense"],
        order="n DESC",
        limit=50,
    )
    assert built.raw is False
    assert built.params["$select"] == "offense, count(*) as n"
    assert built.params["$where"] == "beat = 'B1'"
    assert built.params["$group"] == "offense"
    assert built.params["$order"] == "n DESC"
    assert built.effective_limit == 50
    assert "$limit" not in built.params


def test_default_order_is_row_id():
    built = build(select=["a"])
    assert built.params["$order"] == ":id"


def test_default_order_with_group_uses_group_columns():
    built = build(select=["a", "count(*)"], group=["a"])
    assert built.params["$order"] == "a"


def test_default_limit_applied():
    built = build()
    assert built.effective_limit == 100


def test_structured_limit_clamped_to_max_rows():
    built = build(limit=999_999, max_rows=5000)
    assert built.effective_limit == 5000
    assert built.clamped is True


def test_within_circle_merged_into_where():
    built = build(
        where="offense = 'THEFT'",
        within_circle=WithinCircle(field="location", lat=47.6, lon=-122.3, radius_m=500),
    )
    assert built.params["$where"] == (
        "(offense = 'THEFT') AND (within_circle(location, 47.6, -122.3, 500))"
    )


def test_within_box_alone():
    built = build(
        within_box=WithinBox(
            field="location", nw_lat=47.7, nw_lon=-122.4, se_lat=47.5, se_lon=-122.2
        )
    )
    assert built.params["$where"] == "within_box(location, 47.7, -122.4, 47.5, -122.2)"


def test_raw_soql_conflicts_with_structured():
    with pytest.raises(ValidationError):
        build(soql="SELECT * LIMIT 5", where="a = 1")


def test_raw_soql_rejects_semicolons():
    with pytest.raises(ValidationError):
        build(soql="SELECT * ; DROP TABLE x")


def test_raw_soql_limit_over_cap_errors():
    with pytest.raises(ValidationError) as exc_info:
        build(soql="SELECT * LIMIT 10000", max_rows=5000)
    assert "5000" in str(exc_info.value)


def test_raw_soql_limit_rewritten_for_truncation_detection():
    built = build(soql="SELECT offense LIMIT 200")
    assert built.raw is True
    assert built.params["$query"] == "SELECT offense LIMIT 201"
    assert built.effective_limit == 200


def test_raw_soql_without_limit_gets_default():
    built = build(soql="SELECT offense WHERE beat = 'B1'", default_limit=100)
    assert built.params["$query"] == "SELECT offense WHERE beat = 'B1' LIMIT 101"
    assert built.effective_limit == 100


def test_structured_where_rejects_semicolons():
    with pytest.raises(ValidationError):
        build(where="a = 1; delete")


def test_negative_limit_rejected():
    with pytest.raises(ValidationError):
        build(limit=-5)


def test_soql_quote_escapes_single_quotes():
    assert soql_quote("O'Brien") == "'O''Brien'"
