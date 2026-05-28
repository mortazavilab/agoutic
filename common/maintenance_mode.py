from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from cortex.models import SystemSetting, User


MAINTENANCE_MODE_KEY = "maintenance_mode"
MAINTENANCE_MESSAGE_KEY = "maintenance_message"
MAINTENANCE_STARTS_AT_KEY = "maintenance_starts_at"

_DEFAULT_ROWS = {
    MAINTENANCE_MODE_KEY: "false",
    MAINTENANCE_MESSAGE_KEY: "",
    MAINTENANCE_STARTS_AT_KEY: "",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_starts_at(value: Any) -> str | None:
    parsed = _normalize_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _setting_value(row: SystemSetting | None, default: str = "") -> str:
    if row is None:
        return default
    return str(row.value or default)


def _serialize_state(rows: dict[str, SystemSetting], *, updated_by_email: str | None = None) -> dict[str, Any]:
    mode_row = rows.get(MAINTENANCE_MODE_KEY)
    return {
        "mode": _as_bool(_setting_value(mode_row, "false")),
        "message": _setting_value(rows.get(MAINTENANCE_MESSAGE_KEY), ""),
        "starts_at": normalize_starts_at(_setting_value(rows.get(MAINTENANCE_STARTS_AT_KEY), "")),
        "updated_by_email": updated_by_email,
        "updated_at": mode_row.updated_at.isoformat() if mode_row and mode_row.updated_at else None,
    }


def _ensure_defaults_sync(session: Session) -> dict[str, SystemSetting]:
    rows = {
        row.key: row
        for row in session.execute(
            select(SystemSetting).where(SystemSetting.key.in_(tuple(_DEFAULT_ROWS)))
        ).scalars()
    }
    missing = [
        SystemSetting(key=key, value=value)
        for key, value in _DEFAULT_ROWS.items()
        if key not in rows
    ]
    if missing:
        session.add_all(missing)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        rows = {
            row.key: row
            for row in session.execute(
                select(SystemSetting).where(SystemSetting.key.in_(tuple(_DEFAULT_ROWS)))
            ).scalars()
        }
    return rows


async def _ensure_defaults_async(session: AsyncSession) -> dict[str, SystemSetting]:
    rows = {
        row.key: row
        for row in (
            await session.execute(
                select(SystemSetting).where(SystemSetting.key.in_(tuple(_DEFAULT_ROWS)))
            )
        ).scalars()
    }
    missing = [
        SystemSetting(key=key, value=value)
        for key, value in _DEFAULT_ROWS.items()
        if key not in rows
    ]
    if missing:
        session.add_all(missing)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        rows = {
            row.key: row
            for row in (
                await session.execute(
                    select(SystemSetting).where(SystemSetting.key.in_(tuple(_DEFAULT_ROWS)))
                )
            ).scalars()
        }
    return rows


def get_maintenance_state(session: Session) -> dict[str, Any]:
    rows = _ensure_defaults_sync(session)
    updated_by = rows.get(MAINTENANCE_MODE_KEY).updated_by if rows.get(MAINTENANCE_MODE_KEY) else None
    updated_by_email = None
    if updated_by:
        updated_by_email = session.execute(
            select(User.email).where(User.id == updated_by)
        ).scalar_one_or_none()
    return _serialize_state(rows, updated_by_email=updated_by_email)


async def get_maintenance_state_async(session: AsyncSession) -> dict[str, Any]:
    rows = await _ensure_defaults_async(session)
    updated_by = rows.get(MAINTENANCE_MODE_KEY).updated_by if rows.get(MAINTENANCE_MODE_KEY) else None
    updated_by_email = None
    if updated_by:
        updated_by_email = (
            await session.execute(select(User.email).where(User.id == updated_by))
        ).scalar_one_or_none()
    return _serialize_state(rows, updated_by_email=updated_by_email)


def set_maintenance_state(
    session: Session,
    *,
    mode: bool,
    message: str = "",
    starts_at: Any = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    rows = _ensure_defaults_sync(session)
    now = utc_now()
    normalized_starts_at = normalize_starts_at(starts_at) or ""
    values = {
        MAINTENANCE_MODE_KEY: "true" if mode else "false",
        MAINTENANCE_MESSAGE_KEY: str(message or ""),
        MAINTENANCE_STARTS_AT_KEY: normalized_starts_at,
    }
    for key, value in values.items():
        row = rows[key]
        row.value = value
        row.updated_at = now
        row.updated_by = updated_by
    session.commit()
    return get_maintenance_state(session)


def clear_maintenance_state(session: Session, *, updated_by: str | None = None) -> dict[str, Any]:
    return set_maintenance_state(
        session,
        mode=False,
        message="",
        starts_at=None,
        updated_by=updated_by,
    )


def maintenance_mode_enabled(session: Session) -> bool:
    return bool(get_maintenance_state(session).get("mode"))


async def maintenance_mode_enabled_async(session: AsyncSession) -> bool:
    return bool((await get_maintenance_state_async(session)).get("mode"))


def maintenance_block_message(state: dict[str, Any], *, noun: str) -> str:
    custom = str(state.get("message") or "").strip()
    if custom:
        return custom
    return (
        f"AGOUTIC is in maintenance mode. New {noun} requests are paused. "
        "Existing work continues."
    )