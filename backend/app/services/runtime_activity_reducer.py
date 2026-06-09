"""Pure transition rules for persisted runtime market activity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .runtime_activity_contract import RuntimeActivityRecord, stage_index

_OPTIONAL_FAILURE_SCAN_SUPERSEDE_STAGES = frozenset({"groups"})
_PRESERVED_EXISTING_STATUSES = frozenset({"running", "completed", "failed"})


@dataclass(frozen=True)
class RuntimeActivityTransition:
    should_persist: bool
    record: RuntimeActivityRecord

    @property
    def payload(self) -> dict[str, Any]:
        return self.record.to_payload()


def _coerce_activity_record(
    payload: RuntimeActivityRecord | Mapping[str, Any] | None,
) -> RuntimeActivityRecord | None:
    if payload is None:
        return None
    if isinstance(payload, RuntimeActivityRecord):
        return payload
    try:
        return RuntimeActivityRecord.from_payload(payload)
    except ValueError:
        return None


def reduce_market_activity(
    existing_payload: RuntimeActivityRecord | Mapping[str, Any] | None,
    incoming_payload: RuntimeActivityRecord | Mapping[str, Any],
) -> RuntimeActivityTransition:
    """Return the activity payload that should win this state transition."""
    incoming = _coerce_activity_record(incoming_payload)
    if incoming is None:
        raise ValueError("incoming runtime activity payload is invalid")

    existing = _coerce_activity_record(existing_payload)
    if existing is None:
        return RuntimeActivityTransition(should_persist=True, record=incoming)

    existing_status = existing.status
    if existing_status not in _PRESERVED_EXISTING_STATUSES:
        return RuntimeActivityTransition(should_persist=True, record=incoming)

    payload_status = incoming.status
    same_task = existing.task_id == incoming.task_id
    same_stage = existing.stage_key == incoming.stage_key
    same_owner = same_task and same_stage
    incoming_has_owner = incoming.task_id is not None

    if existing_status == "running":
        if payload_status == "queued" or (
            payload_status != "failed" and not same_owner
        ):
            return RuntimeActivityTransition(should_persist=False, record=existing)
        if payload_status == "failed" and not same_owner:
            return RuntimeActivityTransition(should_persist=False, record=existing)
    elif existing_status == "completed":
        if payload_status != "failed":
            incoming_new_cycle = (
                payload_status in {"queued", "running"}
                and incoming_has_owner
                and not same_owner
            )
            if not incoming_new_cycle:
                return RuntimeActivityTransition(should_persist=False, record=existing)
    elif existing_status == "failed":
        supersedes_failed_activity = _should_supersede_failed_activity(existing, incoming)
        incoming_new_cycle = (
            payload_status in {"queued", "running"}
            and incoming_has_owner
            and not same_owner
        )
        if incoming_new_cycle:
            existing_stage_index = stage_index(existing.stage_key)
            payload_stage_index = stage_index(incoming.stage_key)
            lifecycle_changed = existing.lifecycle != incoming.lifecycle
            incoming_new_cycle = (
                lifecycle_changed or payload_stage_index <= existing_stage_index
            )
        if supersedes_failed_activity:
            pass
        elif payload_status == "failed" and same_owner:
            if _should_preserve_existing_failed_message(existing, incoming):
                return RuntimeActivityTransition(should_persist=False, record=existing)
        elif not incoming_new_cycle:
            return RuntimeActivityTransition(should_persist=False, record=existing)

    return RuntimeActivityTransition(should_persist=True, record=incoming)


def _should_preserve_existing_failed_message(
    existing_payload: RuntimeActivityRecord,
    payload: RuntimeActivityRecord,
) -> bool:
    existing_message = str(existing_payload.message or "").strip()
    incoming_message = str(payload.message or "").strip()
    return bool(
        existing_message
        and incoming_message
        and existing_message != incoming_message
        and len(existing_message) >= len(incoming_message)
    )


def _should_supersede_failed_activity(
    existing_payload: RuntimeActivityRecord,
    payload: RuntimeActivityRecord,
) -> bool:
    """Allow a real scan stage to replace stale optional-stage failures."""
    if existing_payload.stage_key not in _OPTIONAL_FAILURE_SCAN_SUPERSEDE_STAGES:
        return False
    if payload.status not in {"running", "completed"}:
        return False
    if payload.stage_key != "scan":
        return False
    if existing_payload.lifecycle != payload.lifecycle:
        return False
    if payload.lifecycle != "bootstrap":
        return False
    return stage_index(payload.stage_key) > stage_index(existing_payload.stage_key)
