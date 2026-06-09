"""Runtime market activity persistence and aggregation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.app_settings import AppSetting
from ..services.bootstrap_run_manifest import (
    BOOTSTRAP_RUN_KEY,
    BootstrapRunManifest,
    BootstrapRunManifestRepository,
)
from ..services.runtime_activity_contract import (
    PersistedRuntimeActivity,
    RuntimeActivityRecord,
    RuntimeActivityUpdate,
    progress_mode,
    resolve_progress_percent,
)
from ..services.runtime_activity_presenter import build_runtime_activity_status
from ..services.runtime_activity_reducer import reduce_market_activity
from ..services.runtime_preferences_service import get_runtime_bootstrap_status
from ..wiring.bootstrap import get_data_fetch_lock

RUNTIME_ACTIVITY_CATEGORY = "runtime_activity"
MARKET_ACTIVITY_KEY_PREFIX = "runtime.activity.market."


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _activity_key(market: str) -> str:
    return f"{MARKET_ACTIVITY_KEY_PREFIX}{str(market).upper()}"


def _get_setting(db: Session, key: str) -> AppSetting | None:
    return db.query(AppSetting).filter(AppSetting.key == key).first()


def _load_market_activity(db: Session, market: str) -> dict[str, Any] | None:
    setting = _get_setting(db, _activity_key(market))
    if setting is None:
        return None
    try:
        payload = json.loads(setting.value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return PersistedRuntimeActivity.from_payload(payload).to_record().to_payload()
    except ValueError:
        return None


def _load_runtime_bootstrap_run(db: Session) -> dict[str, Any] | None:
    manifest = BootstrapRunManifestRepository().load(db)
    if manifest is None:
        return None
    return manifest.to_payload()


def save_runtime_bootstrap_run(
    db: Session,
    *,
    primary_market: str,
    enabled_markets: list[str],
    primary_task_id: str | None = None,
    market_task_ids: dict[str, str | None] | None = None,
    queue_state: str = "queued",
) -> dict[str, Any]:
    return BootstrapRunManifestRepository().save(
        db,
        BootstrapRunManifest.create(
            primary_market=primary_market,
            enabled_markets=enabled_markets,
            primary_task_id=primary_task_id,
            market_task_ids=market_task_ids or {},
            queue_state=queue_state,
            queued_at=_utcnow_iso(),
        ),
    )


def _save_market_activity(
    db: Session,
    market: str,
    payload: RuntimeActivityUpdate | RuntimeActivityRecord | dict[str, Any],
) -> dict[str, Any]:
    key = _activity_key(market)
    setting = _get_setting(db, key)
    existing_payload = None
    if setting is not None:
        try:
            existing_payload = json.loads(setting.value)
        except (json.JSONDecodeError, TypeError):
            existing_payload = None

    transition = reduce_market_activity(
        existing_payload if isinstance(existing_payload, dict) else None,
        payload,
    )
    if not transition.should_persist:
        return transition.payload
    record = transition.record

    encoded = json.dumps(PersistedRuntimeActivity.from_record(record).to_payload())
    if setting is None:
        setting = AppSetting(
            key=key,
            value=encoded,
            category=RUNTIME_ACTIVITY_CATEGORY,
            description=f"Latest runtime activity state for {market.upper()}",
        )
        db.add(setting)
    else:
        setting.value = encoded
        setting.category = RUNTIME_ACTIVITY_CATEGORY
        setting.description = f"Latest runtime activity state for {market.upper()}"
    db.commit()
    return record.to_payload()


def _activity_payload(
    *,
    market: str,
    stage_key: str | None,
    lifecycle: str | None,
    status: str,
    task_name: str | None = None,
    task_id: str | None = None,
    percent: float | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> RuntimeActivityUpdate:
    return RuntimeActivityUpdate(
        market=market,
        stage_key=stage_key,
        lifecycle=lifecycle,
        status=status,
        task_name=task_name,
        task_id=task_id,
        percent=percent,
        current=current,
        total=total,
        message=message,
        updated_at=_utcnow_iso(),
    )


def mark_market_activity_queued(
    db: Session,
    *,
    market: str,
    stage_key: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=stage_key,
            lifecycle=lifecycle,
            status="queued",
            task_name=task_name,
            task_id=task_id,
            message=message,
        ),
    )


def mark_market_activity_started(
    db: Session,
    *,
    market: str,
    stage_key: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    percent: float | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=stage_key,
            lifecycle=lifecycle,
            status="running",
            task_name=task_name,
            task_id=task_id,
            percent=percent,
            current=current,
            total=total,
            message=message,
        ),
    )


def mark_market_activity_progress(
    db: Session,
    *,
    market: str,
    stage_key: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    percent: float | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=stage_key,
            lifecycle=lifecycle,
            status="running",
            task_name=task_name,
            task_id=task_id,
            percent=percent,
            current=current,
            total=total,
            message=message,
        ),
    )


def mark_market_activity_completed(
    db: Session,
    *,
    market: str,
    stage_key: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    percent: float | None = 100.0,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=stage_key,
            lifecycle=lifecycle,
            status="completed",
            task_name=task_name,
            task_id=task_id,
            percent=percent,
            current=current,
            total=total,
            message=message,
        ),
    )


def mark_market_activity_failed(
    db: Session,
    *,
    market: str,
    stage_key: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    percent: float | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=stage_key,
            lifecycle=lifecycle,
            status="failed",
            task_name=task_name,
            task_id=task_id,
            percent=percent,
            current=current,
            total=total,
            message=message,
        ),
    )


def mark_current_market_activity_failed(
    db: Session,
    *,
    market: str,
    lifecycle: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return _save_market_activity(
        db,
        market,
        _activity_payload(
            market=market,
            stage_key=None,
            lifecycle=lifecycle,
            status="failed",
            task_name=task_name,
            task_id=task_id,
            message=message,
        ),
    )


def _overlay_live_progress(record: dict[str, Any], market: str) -> dict[str, Any]:
    if record.get("status") != "running":
        return record
    if record.get("stage_key") != "prices":
        return record
    try:
        current_task = get_data_fetch_lock().get_current_task(market=market)
    except Exception:
        current_task = None
    if not current_task or current_task.get("task_id") != record.get("task_id"):
        return record
    merged = dict(record)
    if merged.get("percent") is None and current_task.get("progress") is not None:
        merged["percent"] = float(current_task["progress"])
    if merged.get("current") is None and current_task.get("current") is not None:
        merged["current"] = current_task["current"]
    if merged.get("total") is None and current_task.get("total") is not None:
        merged["total"] = current_task["total"]
    if current_task.get("last_heartbeat") is not None:
        merged["updated_at"] = current_task["last_heartbeat"]
    merged["percent"] = resolve_progress_percent(
        merged.get("percent"),
        merged.get("current"),
        merged.get("total"),
    )
    merged["progress_mode"] = progress_mode(
        merged.get("status"),
        merged.get("percent"),
        merged.get("current"),
        merged.get("total"),
    )
    return merged


def _queued_bootstrap_market_payload(
    market: str,
    _primary_market: str,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    return RuntimeActivityRecord.create(
        market=market,
        lifecycle="bootstrap",
        stage_key="universe",
        status="queued",
        message="Bootstrap queued.",
        task_id=task_id,
    ).to_payload()


def _idle_market_payload(market: str, record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return RuntimeActivityRecord.create(
            market=market,
            lifecycle="idle",
            stage_key=None,
            status="idle",
            message="Idle",
        ).to_payload()
    payload = dict(record)
    if payload.get("status") == "completed":
        payload["lifecycle"] = "idle"
    payload["percent"] = resolve_progress_percent(
        payload.get("percent"),
        payload.get("current"),
        payload.get("total"),
    )
    payload["progress_mode"] = progress_mode(
        payload.get("status"),
        payload.get("percent"),
        payload.get("current"),
        payload.get("total"),
    )
    return payload


def _market_payload(
    *,
    market: str,
    record: dict[str, Any] | None,
    bootstrap_state: str,
    bootstrap_required: bool,
    primary_market: str,
    bootstrap_run: dict[str, Any],
) -> dict[str, Any]:
    if record is None and bootstrap_state == "running" and bootstrap_required:
        market_task_ids = bootstrap_run.get("market_task_ids") or {}
        return _queued_bootstrap_market_payload(
            market,
            primary_market,
            task_id=market_task_ids.get(str(market).upper()),
        )
    if record is None:
        return _idle_market_payload(market, None)
    return _idle_market_payload(market, _overlay_live_progress(record, market))


def get_runtime_activity_status(db: Session) -> dict[str, Any]:
    bootstrap_status = get_runtime_bootstrap_status(db)
    enabled_markets = list(bootstrap_status.enabled_markets)
    primary_market = bootstrap_status.primary_market
    bootstrap_run = _load_runtime_bootstrap_run(db) or {}

    market_payloads = []
    for market in enabled_markets:
        record = _load_market_activity(db, market)
        market_payloads.append(
            _market_payload(
                market=market,
                record=record,
                bootstrap_state=bootstrap_status.bootstrap_state,
                bootstrap_required=bootstrap_status.bootstrap_required,
                primary_market=primary_market,
                bootstrap_run=bootstrap_run,
            )
        )

    return build_runtime_activity_status(
        bootstrap_status=bootstrap_status,
        bootstrap_run=bootstrap_run,
        market_payloads=market_payloads,
    )
