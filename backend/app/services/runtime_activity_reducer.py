"""Pure transition rules for persisted runtime market activity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .runtime_activity_contract import (
    ACTIVE_ACTIVITY_STATUSES,
    RuntimeActivityRecord,
    RuntimeActivityUpdate,
    stage_index,
)

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
    payload: RuntimeActivityRecord | RuntimeActivityUpdate | Mapping[str, Any] | None,
    *,
    existing: RuntimeActivityRecord | None = None,
) -> RuntimeActivityRecord | None:
    if payload is None:
        return None
    if isinstance(payload, RuntimeActivityRecord):
        return payload
    if isinstance(payload, RuntimeActivityUpdate):
        return _inherit_existing_context(existing, payload).to_record()
    try:
        return RuntimeActivityRecord.from_payload(payload)
    except ValueError:
        return None


def reduce_market_activity(
    existing_payload: RuntimeActivityRecord | Mapping[str, Any] | None,
    incoming_payload: RuntimeActivityRecord | RuntimeActivityUpdate | Mapping[str, Any],
) -> RuntimeActivityTransition:
    """Return the activity payload that should win this state transition."""
    existing = _coerce_activity_record(existing_payload)
    incoming = _coerce_activity_record(incoming_payload, existing=existing)
    if incoming is None:
        raise ValueError("incoming runtime activity payload is invalid")

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
        if payload_status == "queued" or not same_owner:
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
        if _should_supersede_failed_activity(existing, incoming):
            return RuntimeActivityTransition(should_persist=True, record=incoming)
        if payload_status == "failed" and same_owner:
            if _should_preserve_existing_failed_message(existing, incoming):
                return RuntimeActivityTransition(should_persist=False, record=existing)
        elif not _is_new_cycle_after_failed(
            existing,
            incoming,
            same_owner=same_owner,
            incoming_has_owner=incoming_has_owner,
        ):
            return RuntimeActivityTransition(should_persist=False, record=existing)

    return RuntimeActivityTransition(should_persist=True, record=incoming)


def _inherit_existing_context(
    existing: RuntimeActivityRecord | None,
    update: RuntimeActivityUpdate,
) -> RuntimeActivityUpdate:
    if existing is None or existing.status not in ACTIVE_ACTIVITY_STATUSES:
        if update.status == "failed" and update.stage_key is None:
            return replace(update, stage_key="scan")
        return update
    if update.status not in {"running", "failed"}:
        return update
    if existing.task_id and update.task_id and existing.task_id != update.task_id:
        return update
    if update.stage_key is not None and existing.stage_key != update.stage_key:
        return update
    if update.lifecycle is not None and existing.lifecycle != update.lifecycle:
        return update
    return replace(
        update,
        stage_key=update.stage_key or existing.stage_key,
        lifecycle=update.lifecycle or existing.lifecycle,
        task_name=update.task_name or existing.task_name,
        task_id=update.task_id or existing.task_id,
        message=update.message or existing.message,
    )


def _is_new_cycle_after_failed(
    existing: RuntimeActivityRecord,
    incoming: RuntimeActivityRecord,
    *,
    same_owner: bool,
    incoming_has_owner: bool,
) -> bool:
    if incoming.status not in {"queued", "running"} or not incoming_has_owner or same_owner:
        return False
    lifecycle_changed = existing.lifecycle != incoming.lifecycle
    payload_restarts_at_or_before_failed_stage = (
        stage_index(incoming.stage_key) <= stage_index(existing.stage_key)
    )
    return lifecycle_changed or payload_restarts_at_or_before_failed_stage


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
