"""Shared runtime activity response contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ACTIVE_ACTIVITY_STATUSES = frozenset({"queued", "running"})
PERSISTED_RUNTIME_ACTIVITY_FIELDS = frozenset({
    "market",
    "lifecycle",
    "stage_key",
    "status",
    "percent",
    "current",
    "total",
    "message",
    "task_name",
    "task_id",
    "updated_at",
})

RUNTIME_STAGE_SEQUENCE = (
    "universe",
    "prices",
    "fundamentals",
    "breadth",
    "groups",
    "scan",
)

STAGE_LABELS = {
    "universe": "Universe Refresh",
    "prices": "Price Refresh",
    "fundamentals": "Fundamentals Refresh",
    "breadth": "Breadth Calculation",
    "groups": "Group Rankings",
    "snapshot": "Feature Snapshot",
    "scan": "Scan",
}

DEFAULT_LIFECYCLE_BY_STAGE = {
    "universe": "weekly_refresh",
    "prices": "daily_refresh",
    "fundamentals": "weekly_refresh",
    "breadth": "daily_refresh",
    "groups": "daily_refresh",
    "snapshot": "daily_refresh",
    "scan": "daily_refresh",
}


def stage_label(stage_key: str | None) -> str | None:
    if stage_key is None:
        return None
    return STAGE_LABELS.get(stage_key, str(stage_key).replace("_", " ").title())


def default_lifecycle(stage_key: str | None) -> str:
    if stage_key is None:
        return "daily_refresh"
    return DEFAULT_LIFECYCLE_BY_STAGE.get(stage_key, "daily_refresh")


def stage_index(stage_key: str | None) -> int:
    if stage_key in RUNTIME_STAGE_SEQUENCE:
        return RUNTIME_STAGE_SEQUENCE.index(stage_key)
    return len(RUNTIME_STAGE_SEQUENCE)


def resolve_progress_percent(
    percent: float | None,
    current: int | None,
    total: int | None,
) -> float | None:
    if percent is not None:
        return float(percent)
    if current is None or total in (None, 0):
        return None
    return round((float(current) / float(total)) * 100.0, 1)


def progress_mode(
    status: str | None,
    percent: float | None,
    current: int | None = None,
    total: int | None = None,
) -> str:
    resolved_percent = resolve_progress_percent(percent, current, total)
    if resolved_percent is not None or status in {"completed", "idle"}:
        return "determinate"
    return "indeterminate"


@dataclass(frozen=True)
class RuntimeActivityRecord:
    market: str
    lifecycle: str
    stage_key: str | None
    stage_label: str | None
    status: str
    progress_mode: str
    percent: float | None
    current: int | None
    total: int | None
    message: str | None
    task_name: str | None
    task_id: str | None
    updated_at: str | None

    @classmethod
    def create(
        cls,
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
        updated_at: str | None = None,
    ) -> "RuntimeActivityRecord":
        resolved_stage_label = stage_label(stage_key)
        resolved_lifecycle = lifecycle or default_lifecycle(stage_key)
        resolved_percent = resolve_progress_percent(percent, current, total)
        resolved_message = message or _default_message(status, resolved_stage_label)
        return cls(
            market=str(market).upper(),
            lifecycle=resolved_lifecycle,
            stage_key=stage_key,
            stage_label=resolved_stage_label,
            status=status,
            progress_mode=progress_mode(status, resolved_percent, current, total),
            percent=resolved_percent,
            current=current,
            total=total,
            message=resolved_message,
            task_name=task_name,
            task_id=task_id,
            updated_at=updated_at,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RuntimeActivityRecord":
        missing_fields = PERSISTED_RUNTIME_ACTIVITY_FIELDS.difference(payload)
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"missing required runtime activity fields: {missing}")

        stage_key = payload.get("stage_key")
        status = str(payload.get("status") or "idle")
        lifecycle = str(payload.get("lifecycle") or "")
        if not lifecycle:
            raise ValueError("missing lifecycle")
        current = payload.get("current")
        total = payload.get("total")

        raw_percent = payload.get("percent")
        percent = (
            float(raw_percent)
            if raw_percent is not None
            else None
        )

        return cls(
            market=str(payload.get("market") or "").upper(),
            lifecycle=lifecycle,
            stage_key=stage_key,
            stage_label=stage_label(stage_key),
            status=status,
            progress_mode=progress_mode(status, percent, current, total),
            percent=resolve_progress_percent(percent, current, total),
            current=current,
            total=total,
            message=(
                str(payload["message"])
                if payload.get("message") is not None
                else None
            ),
            task_name=payload.get("task_name"),
            task_id=payload.get("task_id"),
            updated_at=payload.get("updated_at"),
        )

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_ACTIVITY_STATUSES

    @property
    def active_bootstrap(self) -> bool:
        return self.lifecycle == "bootstrap" and self.active

    def to_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "lifecycle": self.lifecycle,
            "stage_key": self.stage_key,
            "stage_label": self.stage_label,
            "status": self.status,
            "progress_mode": self.progress_mode,
            "percent": self.percent,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "task_name": self.task_name,
            "task_id": self.task_id,
            "updated_at": self.updated_at,
        }

    def to_persisted_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "lifecycle": self.lifecycle,
            "stage_key": self.stage_key,
            "status": self.status,
            "percent": self.percent,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "task_name": self.task_name,
            "task_id": self.task_id,
            "updated_at": self.updated_at,
        }


def _default_message(status: str, resolved_stage_label: str | None) -> str | None:
    if resolved_stage_label is None:
        return None
    action = {
        "queued": "Queued",
        "running": "Running",
        "completed": "Completed",
        "failed": "Failed",
    }.get(status, "Updated")
    return f"{action} {resolved_stage_label.lower()}"
