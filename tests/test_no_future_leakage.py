import pytest

from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot


@pytest.mark.parametrize("field", ["t1_return", "future_5d_return", "max_adverse_excursion"])
def test_future_outcomes_are_rejected(field):
    with pytest.raises(ValueError):
        validate_and_build_canonical_snapshot({"symbol": "600001", "price": 10, field: 0.1})
