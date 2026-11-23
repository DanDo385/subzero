from subzero.mcp.compact import dump


def test_dump_strips_nested_nulls() -> None:
    assert dump({"a": 1, "b": None, "c": [], "d": {"e": None, "f": "x"}}) == {
        "a": 1,
        "d": {"f": "x"},
    }
