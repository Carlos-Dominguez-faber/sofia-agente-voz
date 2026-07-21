"""Reading Retell's responses — the shapes that already caused a false zero.

Every test here exists because of a real defect, not a hypothetical one.

`call.list` returns a `CallListResponse` object with `.items`, not a list and
not `.calls`. Reading it wrongly returned an empty list, which the dashboard
rendered as "0 llamadas, 0 citas, 0% de éxito" while the account had three calls
and a booking. Nothing raised. Nothing logged. The panel simply told the clinic
that Sofía had done nothing.

That is the failure mode this project exists to avoid, so the rule is now: an
unrecognised response shape raises. A zero is only ever displayed when Retell
actually said zero.
"""

from __future__ import annotations

import pytest

from app.services.retell_service import RetellServiceError, _filter_criteria, _items_of

AGENT = "agent_test123"


class FakeListResponse:
    """Shaped like the SDK's CallListResponse."""

    def __init__(self, items, has_more=False):
        self.items = items
        self.has_more = has_more


class FakeUnknownResponse:
    """A future SDK version that renames the field."""

    def __init__(self, rows):
        self.rows = rows


# --------------------------------------------------------------------------
# Reading the rows
# --------------------------------------------------------------------------


def test_items_are_read_from_the_items_attribute():
    assert _items_of(FakeListResponse([1, 2, 3])) == [1, 2, 3]


def test_a_genuinely_empty_result_is_still_empty():
    """Retell said zero. That is a fact and it may be displayed."""
    assert _items_of(FakeListResponse([])) == []


def test_a_bare_list_is_still_accepted():
    assert _items_of([1, 2]) == [1, 2]


def test_an_unrecognised_shape_raises_instead_of_reporting_zero():
    """The regression. Returning [] here is how the panel lied about three calls."""
    with pytest.raises(RetellServiceError, match="Unexpected shape"):
        _items_of(FakeUnknownResponse([1, 2, 3]))


def test_the_error_names_what_it_refuses_to_do():
    with pytest.raises(RetellServiceError, match="Refusing to report zero"):
        _items_of(FakeUnknownResponse([]))


# --------------------------------------------------------------------------
# The filter
# --------------------------------------------------------------------------


def test_every_query_is_scoped_to_one_agent():
    """An unscoped query would show one clinic another clinic's patients."""
    criteria = _filter_criteria(agent_id=AGENT)
    assert criteria["agent"] == [{"agent_id": AGENT}]


def test_a_time_range_is_sent_as_a_between_filter():
    criteria = _filter_criteria(agent_id=AGENT, start_ms=1000, end_ms=2000)
    assert criteria["start_timestamp"] == {"type": "range", "op": "bt", "value": [1000, 2000]}


def test_half_a_range_is_ignored_rather_than_guessed():
    assert "start_timestamp" not in _filter_criteria(agent_id=AGENT, start_ms=1000)


def test_the_booking_filter_asks_for_successful_calls_only():
    """This is the appointment count. A failed booking must not be counted."""
    criteria = _filter_criteria(agent_id=AGENT, tool_name="book_appointment", tool_success=True)
    assert criteria["tool_calls"] == [
        {"name": "book_appointment", "success": {"op": "eq", "type": "boolean", "value": True}}
    ]


def test_a_tool_filter_without_a_success_flag_matches_either_outcome():
    criteria = _filter_criteria(agent_id=AGENT, tool_name="check_availability")
    assert criteria["tool_calls"] == [{"name": "check_availability"}]
