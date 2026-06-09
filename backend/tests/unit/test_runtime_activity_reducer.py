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


def test_reduce_market_activity_inherits_running_context_from_existing_record():
    from app.services.runtime_activity_contract import RuntimeActivityUpdate
    from app.services.runtime_activity_reducer import reduce_market_activity

    existing = _record(
        status="running",
        stage_key="fundamentals",
        lifecycle="bootstrap",
        task_name="refresh_all_fundamentals",
        task_id="task-us",
        message="Refreshing fundamentals",
    )
    incoming = RuntimeActivityUpdate(
        market="US",
        stage_key="fundamentals",
        lifecycle=None,
        status="running",
        current=25,
        total=100,
        message=None,
    )

    transition = reduce_market_activity(existing, incoming)

    assert transition.should_persist is True
    assert transition.record.lifecycle == "bootstrap"
    assert transition.record.task_name == "refresh_all_fundamentals"
    assert transition.record.task_id == "task-us"
    assert transition.record.message == "Refreshing fundamentals"
    assert transition.record.percent == 25.0


def test_runtime_activity_update_is_a_passive_value_object():
    from app.services.runtime_activity_contract import RuntimeActivityUpdate

    update = RuntimeActivityUpdate(
        market="US",
        stage_key="prices",
        lifecycle=None,
        status="running",
        current=3,
        total=10,
    )

    assert not hasattr(RuntimeActivityUpdate, "inherit_context")
    assert update.to_record().task_id is None
    assert update.to_record().message == "Running price refresh"


def test_ownerless_failure_without_active_context_defaults_to_scan_stage():
    from app.services.runtime_activity_contract import RuntimeActivityUpdate
    from app.services.runtime_activity_reducer import reduce_market_activity

    transition = reduce_market_activity(
        None,
        RuntimeActivityUpdate(
            market="US",
            stage_key=None,
            lifecycle=None,
            status="failed",
            message="Task failed",
        ),
    )

    assert transition.should_persist is True
    assert transition.record.stage_key == "scan"
    assert transition.record.stage_label == "Scan"
