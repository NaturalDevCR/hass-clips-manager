"""Transactional repositories for Worker configuration and its audit trail."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .database import Database
from .domain import (
    CollectionCreate,
    CollectionPatch,
    CollectionRecord,
    ProfileCreate,
    ProfilePatch,
    ProfileRecord,
)


class OptimisticConflict(Exception):
    """The caller supplied a stale revision; HTTP adapters map this to 409."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _summary(value: Any) -> str:
    if isinstance(value, dict):
        return _json(
            {
                k: "[REDACTED]" if "secret" in k.lower() or "token" in k.lower() else _summary(v)
                for k, v in value.items()
            }
        )
    return str(value)


class _Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _audit(
        self, action: str, target: str, actor: str | None, request_id: str | None, payload: Any
    ) -> None:
        if not actor or not request_id:
            raise ValueError("actor and request_id are required for mutations")
        self.db.connection.execute(
            "INSERT INTO audit_events(action_type,target_id,actor,request_id,summary,occurred_at) VALUES(?,?,?,?,?,?)",
            (action, target, actor, request_id, _summary(payload), _now()),
        )


class CollectionRepository(_Repository):
    def create(
        self, payload: CollectionCreate, *, actor: str | None = None, request_id: str | None = None
    ) -> CollectionRecord:
        now = _now()
        try:
            with self.db.connection:
                if payload.is_default:
                    self.db.connection.execute(
                        "UPDATE collections SET is_default=0, updated_at=? WHERE is_default=1",
                        (now,),
                    )
                self.db.connection.execute(
                    "INSERT INTO collections(id,name,enabled,source_directory,compiled_output_prefix,processing_profile_id,is_default,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        payload.id,
                        payload.name,
                        int(payload.enabled),
                        payload.source_directory,
                        payload.id,
                        payload.processing_profile_id,
                        int(payload.is_default),
                        now,
                        now,
                    ),
                )
                self._audit(
                    "collection.create",
                    payload.id,
                    actor,
                    request_id,
                    payload.model_dump(exclude={"worker_secret"}),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"collection {payload.id!r} already exists or violates an invariant"
            ) from exc
        return self.get(payload.id)

    def get(self, collection_id: str) -> CollectionRecord:
        row = self.db.connection.execute(
            "SELECT * FROM collections WHERE id=?", (collection_id,)
        ).fetchone()
        if row is None:
            raise KeyError(collection_id)
        return CollectionRecord(
            id=row["id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            source_directory=row["source_directory"],
            compiled_output_prefix=row["compiled_output_prefix"],
            processing_profile_id=row["processing_profile_id"],
            is_default=bool(row["is_default"]),
            allow_manual_override=bool(row["allow_manual_override"]),
            tags=json.loads(row["tags"]),
            notes=row["notes"],
            revision=row["revision"],
        )

    def patch(
        self,
        id: str,
        revision: int,
        patch: CollectionPatch | dict[str, Any],
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> CollectionRecord:
        patch = (
            patch if isinstance(patch, CollectionPatch) else CollectionPatch.model_validate(patch)
        )
        values = patch.model_dump(exclude_none=True)
        if not values:
            raise ValueError("patch must not be empty")
        columns = {
            "name": "name",
            "enabled": "enabled",
            "priority": "priority",
            "source_directory": "source_directory",
            "processing_profile_id": "processing_profile_id",
            "is_default": "is_default",
            "allow_manual_override": "allow_manual_override",
            "tags": "tags",
            "notes": "notes",
        }
        assignments, args = [], []
        for key, value in values.items():
            assignments.append(f"{columns[key]}=?")
            args.append(
                _json(value) if key == "tags" else int(value) if isinstance(value, bool) else value
            )
        args.extend([_now(), id, revision])
        with self.db.connection:
            if values.get("is_default") is True:
                self.db.connection.execute(
                    "UPDATE collections SET is_default=0, updated_at=? WHERE is_default=1 AND id<>?",
                    (_now(), id),
                )
            cur = self.db.connection.execute(
                f"UPDATE collections SET {', '.join(assignments)}, revision=revision+1, updated_at=? WHERE id=? AND revision=?",
                args,
            )
            if cur.rowcount != 1:
                raise OptimisticConflict(f"collection {id!r} revision {revision} is stale")
            self._audit("collection.patch", id, actor, request_id, values)
        return self.get(id)


class ProfileRepository(_Repository):
    def create(
        self, payload: ProfileCreate, *, actor: str | None = None, request_id: str | None = None
    ) -> ProfileRecord:
        now = _now()
        try:
            with self.db.connection:
                self.db.connection.execute(
                    "INSERT INTO profiles(id,name,settings,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (payload.id, payload.name, _json(payload.settings), now, now),
                )
                self._audit(
                    "profile.create",
                    payload.id,
                    actor,
                    request_id,
                    payload.model_dump(exclude={"asset_secret"}),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"profile {payload.id!r} already exists") from exc
        return self.get(payload.id)

    def get(self, profile_id: str) -> ProfileRecord:
        row = self.db.connection.execute(
            "SELECT * FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return ProfileRecord(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            settings=json.loads(row["settings"]),
            revision=row["revision"],
        )

    def patch(
        self,
        id: str,
        revision: int,
        patch: ProfilePatch | dict[str, Any],
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> ProfileRecord:
        patch = patch if isinstance(patch, ProfilePatch) else ProfilePatch.model_validate(patch)
        values = patch.model_dump(exclude_none=True)
        if not values:
            raise ValueError("patch must not be empty")
        args = [
            _json(values["settings"]) if "settings" in values else values.get("name"),
            _now(),
            id,
            revision,
        ]
        if "settings" in values and "name" in values:
            sql = "UPDATE profiles SET name=?, settings=?, version=version+1, revision=revision+1, updated_at=? WHERE id=? AND revision=?"
            args = [values["name"], _json(values["settings"]), _now(), id, revision]
        elif "settings" in values:
            sql = "UPDATE profiles SET settings=?, version=version+1, revision=revision+1, updated_at=? WHERE id=? AND revision=?"
            args = [_json(values["settings"]), _now(), id, revision]
        else:
            sql = "UPDATE profiles SET name=?, version=version+1, revision=revision+1, updated_at=? WHERE id=? AND revision=?"
        with self.db.connection:
            if self.db.connection.execute(sql, args).rowcount != 1:
                raise OptimisticConflict(f"profile {id!r} revision {revision} is stale")
            self._audit("profile.patch", id, actor, request_id, values)
        return self.get(id)
