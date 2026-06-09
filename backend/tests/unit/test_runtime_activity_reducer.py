from __future__ import annotations


def _record(**overrides):
    from app.services.runtime_activity_contract import RuntimeActivityRecord

    values = {
        "market": "US",
        "lifecycle": "bootstrap",
        "stage_key": "prices",
        "status": "running",
        "task_name": "smart_refresh_cache",
        "task_id": "task-us",
        "message": "Refreshing prices",
    }
    values.update(overrides)
    return RuntimeActivityRecord.create(**values)


def test_reduce_market_activity_operates_on_typed_records():
    from app.services.runtime_activity_reducer import reduce_market_activity

    existing = _record(status="completed", percent=100.0)
    incoming = _record(status="running", task_id="task-us", percent=50.0)

    transition = reduce_market_activity(
        existing,
        incoming,
    )

    assert transition.should_persist is False
    assert transition.record == existing


def test_reduce_market_activity_accepts_new_cycle_with_typed_records():
    from app.services.runtime_activity_reducer import reduce_market_activity

    existing = _record(status="failed", stage_key="prices", task_id="task-old")
    incoming = _record(status="queued", stage_key="universe", task_id="task-new")

    transition = reduce_market_activity(
        existing,
        incoming,
    )

    assert transition.should_persist is True
    assert transition.record == incoming


def test_reduce_market_activity_rejects_ownerless_progress_over_completed_record():
    from app.services.runtime_activity_reducer import reduce_market_activity

    existing = _record(status="completed", stage_key="prices", task_id="task-old")
    incoming = _record(status="running", stage_key="prices", task_id=None)

    transition = reduce_market_activity(existing, incoming)

    assert transition.should_persist is False
    assert transition.record == existing
