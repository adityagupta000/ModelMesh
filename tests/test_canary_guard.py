import canary_guard

import pytest


@pytest.mark.parametrize(
    ("stable", "canary", "canary_percent", "expected"),
    [
        (canary_guard.Stats(0, 0.0), canary_guard.Stats(0, 0.0), 0, "noop"),
        (canary_guard.Stats(10, 0.0), canary_guard.Stats(10, 0.0), 20, "hold"),
        (canary_guard.Stats(200, 0.01), canary_guard.Stats(50, 0.02), 20, "hold"),
        (canary_guard.Stats(200, 0.01), canary_guard.Stats(50, 0.30), 20, "rollback"),
        (canary_guard.Stats(200, 0.01), canary_guard.Stats(0, 0.0), 20, "rollback"),
        (canary_guard.Stats(200, 0.01), canary_guard.Stats(10, 0.50), 5, "hold"),
        (canary_guard.Stats(200, 0.40), canary_guard.Stats(50, 0.45), 20, "hold"),
        (canary_guard.Stats(200, 0.10), canary_guard.Stats(50, 0.20), 20, "hold"),
    ],
)
def test_decide_cases(stable, canary, canary_percent, expected):
    decision, _ = canary_guard.decide(
        stable, canary, canary_percent, canary_guard.Config()
    )

    assert decision == expected


def test_selects_highest_and_lowest_numeric_versions_and_ignores_middle():
    versions = [
        {"id": "v9", "version": "v9", "status": "active", "canary_percent": 0},
        {"id": "v10", "version": "v10", "status": "active", "canary_percent": 50},
        {"id": "v11", "version": "v11", "status": "active", "canary_percent": 20},
        {"id": "disabled", "version": "v12", "status": "disabled", "canary_percent": 0},
    ]

    stable, canary = canary_guard.select_stable_and_canary(versions)

    assert stable["id"] == "v9"
    assert canary["id"] == "v11"


def test_version_number_selection_orders_v10_after_v9():
    versions = [
        {"id": "v10", "version": "v10", "status": "active", "canary_percent": 20},
        {"id": "v9", "version": "v9", "status": "active", "canary_percent": 0},
    ]

    stable, canary = canary_guard.select_stable_and_canary(versions)

    assert stable["id"] == "v9"
    assert canary["id"] == "v10"


def test_zero_percent_canary_pair_remains_reportable_as_noop():
    versions = [
        {"id": "v1", "version": "v1", "status": "active", "canary_percent": 0},
        {"id": "v2", "version": "v2", "status": "active", "canary_percent": 0},
    ]

    stable, canary = canary_guard.select_stable_and_canary(versions)

    assert canary_guard.decide(
        canary_guard.Stats(0, 0.0),
        canary_guard.Stats(0, 0.0),
        canary["canary_percent"],
        canary_guard.Config(),
    ) == ("noop", "canary already at 0")
    assert stable["id"] == "v1"
    assert canary["id"] == "v2"
