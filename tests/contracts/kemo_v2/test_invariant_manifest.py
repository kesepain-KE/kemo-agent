from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3] / "provider" / "protocol" / "spec"


def test_p4_manifest_has_one_explicit_row_for_every_acceptance_id() -> None:
    rows = json.loads((ROOT / "invariants.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in rows] == [f"K2-R{i:02d}" for i in range(1, 30)]
    for row in rows:
        assert row["owner_symbol"]
        assert row["kind"] in {"L1", "L2", "L3", "stream", "runtime"}
        assert row["description"]
        assert (ROOT / row["positive_fixture"]).is_file()
        assert (ROOT / row["negative_fixture"]).is_file()
        # The evidence string is deliberately a concrete test path/function,
        # not a prose "covered" checkbox.
        assert "::" in row["evidence"]
