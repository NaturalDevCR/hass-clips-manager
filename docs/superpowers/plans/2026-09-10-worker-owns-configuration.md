# Worker Owns Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Worker the source of truth for collections and processing profiles, edit both in the Library Manager, and reduce the integration to entities, services, and schedule dispatch.

**Architecture:** Five policy fields move from the Home Assistant config entry into the Worker's collection model and API. The Library Manager gains a Collections section with a collection editor and a typed profile editor, absorbing the playback-order editor and retiring the order bridge. The integration deletes its subentry forms and reads policy from the coordinator's Worker snapshot, after a one-way startup migration that pushes, reads back, and only then deletes.

**Tech Stack:** FastAPI + SQLite + Pydantic on the Worker, Vue 3 + Vite + Tailwind for the interface, Home Assistant config entries for the integration.

## Global Constraints

- The five moving fields are `starts_at`, `ends_at`, `schedule`, `playback_mode`, `ordered_clip_ids`.
- `playback_mode` is one of `random`, `sequential`, `custom`; `custom` requires a non-empty `ordered_clip_ids`.
- `/api/v1` keeps its version: the additions are backward compatible for readers.
- Worker domain models are `extra="forbid"`; new fields must be added to `CollectionCreate`, `CollectionPatch`, and `CollectionRecord` together.
- Entity `unique_id`s must not change. They are `entry_id` plus a description key today and stay that way.
- Every Worker URL the interface calls stays relative (Ingress prefix).
- Migration deletes subentries only after reading the records back from the Worker and comparing them.
- Release: Worker minor version, integration major version.

---

### Task 1: Collection policy fields in the Worker

**Files:**
- Modify: `app/src/cinema_collections_worker/database.py`, `app/src/cinema_collections_worker/domain.py`, `app/src/cinema_collections_worker/repositories.py`
- Test: `tests/worker/test_api_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
def test_collection_round_trips_schedule_and_playback_order(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/v1/collections",
        headers=_headers("policy-1"),
        json={
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "compatibility-4k-loudness",
            "starts_at": "2026-01-01T00:00:00+00:00",
            "ends_at": "2026-12-31T00:00:00+00:00",
            "schedule": {"enabled": True, "local_time": "02:30"},
            "playback_mode": "custom",
            "ordered_clip_ids": ["11111111-1111-1111-1111-111111111111"],
        },
    )

    assert created.status_code == 201
    record = client.get("/api/v1/collections", headers=_headers("policy-2")).json()[0]
    assert record["schedule"] == {"enabled": True, "local_time": "02:30"}
    assert record["playback_mode"] == "custom"
    assert record["ordered_clip_ids"] == ["11111111-1111-1111-1111-111111111111"]


def test_custom_playback_requires_an_order(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rejected = client.post(
        "/api/v1/collections",
        headers=_headers("policy-3"),
        json={
            "id": "films",
            "name": "Films",
            "source_directory": "films",
            "processing_profile_id": "compatibility-4k-loudness",
            "playback_mode": "custom",
            "ordered_clip_ids": [],
        },
    )

    assert rejected.status_code == 422
```

Match the existing helpers in that file for the client and headers.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/worker/test_api_endpoints.py -k "round_trips_schedule or custom_playback" -v`
Expected: FAIL — the fields are rejected as extra.

- [ ] **Step 3: Add the database columns**

In `database.py`, add a migration step alongside the existing `ALTER TABLE` block:

```sql
ALTER TABLE collections ADD COLUMN starts_at TEXT;
ALTER TABLE collections ADD COLUMN ends_at TEXT;
ALTER TABLE collections ADD COLUMN schedule TEXT NOT NULL DEFAULT '{}';
ALTER TABLE collections ADD COLUMN playback_mode TEXT NOT NULL DEFAULT 'random';
ALTER TABLE collections ADD COLUMN ordered_clip_ids TEXT NOT NULL DEFAULT '[]';
```

Follow the file's existing migration mechanism exactly; do not invent a second one.

- [ ] **Step 4: Add the model fields**

In `domain.py`, add to `CollectionCreate`, `CollectionPatch` (all optional), and `CollectionRecord`:

```python
    starts_at: str | None = None
    ends_at: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    playback_mode: PlaybackMode = PlaybackMode.RANDOM
    ordered_clip_ids: list[str] = Field(default_factory=list)
```

with

```python
class PlaybackMode(StrEnum):
    RANDOM = "random"
    SEQUENTIAL = "sequential"
    CUSTOM = "custom"
```

and a model validator on `CollectionCreate` rejecting `custom` without ids:

```python
    @model_validator(mode="after")
    def _custom_order_is_present(self) -> CollectionCreate:
        if self.playback_mode is PlaybackMode.CUSTOM and not self.ordered_clip_ids:
            raise ValueError("custom playback requires ordered clip IDs")
        return self
```

- [ ] **Step 5: Persist and read the fields**

Update the collection INSERT, UPDATE, and row-to-record mapping in
`repositories.py`, JSON-encoding `schedule` and `ordered_clip_ids` the way
`tags` is already handled.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/worker -q`
Expected: PASS.

- [ ] **Step 7: Update the contract and commit**

Add the five fields to the collection schemas in `contract/openapi-v1.yaml`.

Run: `uv run openapi-spec-validator contract/openapi-v1.yaml`

```bash
git add app/src contract tests
git commit -m "feat(worker): own collection schedule and playback order"
```

---

### Task 2: Retire the order bridge

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Test: `tests/worker/test_library_manager_web.py`

- [ ] **Step 1: Write the failing test**

```python
def test_manager_session_no_longer_mints_an_order_capability(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _manager_session(client)

    assert "order_bridge_capability" not in client.get("/manager/session").json()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/worker/test_library_manager_web.py -k order_capability -v`
Expected: FAIL — the key is still present.

- [ ] **Step 3: Delete the capability**

Remove `_order_bridge_capability`, `_ORDER_CAPABILITY_HEADER`,
`_ORDER_CAPABILITY_PATH`, `_ORDER_CAPABILITY_SECONDS`, and the
`order_bridge_capability` key from the session payload. Update the tests that
assert on it.

- [ ] **Step 4: Run the suite and commit**

Run: `uv run pytest -q`

```bash
git add app/src tests
git commit -m "refactor(worker): drop the order bridge capability"
```

---

### Task 3: Collection editor in the Library Manager

**Files:**
- Create: `app/ui/src/composables/useCollections.ts`, `app/ui/src/views/CollectionsView.vue`, `app/ui/src/components/CollectionForm.vue`
- Modify: `app/ui/src/router.ts`, `app/ui/src/components/AppSidebar.vue`, `app/ui/src/types.ts`
- Test: `app/ui/src/composables/useCollections.spec.ts`

**Interfaces:**
- Produces: `useCollections()` → `{ collections, profiles, load(), save(collection), remove(id) }`, and a `Collection` type carrying every field from Task 1.

- [ ] **Step 1: Write the failing test**

```ts
describe("useCollections", () => {
  it("sends a new collection to the Worker and reloads the list", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, options: RequestInit) => {
        calls.push(options);
        return new Response("[]", { status: 200 });
      }),
    );
    const store = useCollections();
    await store.save({ ...blankCollection(), id: "films", name: "Films" });
    expect(JSON.parse(String(calls[0].body)).id).toBe("films");
  });
});
```

- [ ] **Step 2: Run it to verify it fails, then implement**

`useCollections` reads `manager/collections` for the list and writes through the
Worker's authenticated collection routes, surfacing the Worker's refusal message
unchanged.

- [ ] **Step 3: Build the form**

`CollectionForm.vue` covers identity and paths, the profile selector, enabled,
priority, default, manual override, the active window, the schedule, the
playback mode, tags, and notes. Selecting `custom` reveals the order editor from
Task 5; the Save button stays disabled while `custom` has no order.

- [ ] **Step 4: Add the route and the sidebar entry**

A fifth section, `Collections`, placed above Library.

- [ ] **Step 5: Verify and commit**

Run: `npm --prefix app/ui run test:unit && npm --prefix app/ui run build`

```bash
git add app/ui
git commit -m "feat(ui): edit collections in the Library Manager"
```

---

### Task 4: Processing profile editor

**Files:**
- Create: `app/ui/src/components/ProfileForm.vue`, `app/ui/src/lib/profile.ts`
- Test: `app/ui/src/lib/profile.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
describe("profile defaults", () => {
  it("switches quality mode without losing the other mode's values", () => {
    const profile = blankProfile();
    const bitrate = withQualityMode(profile, "bitrate");
    expect(bitrate.video.quality.mode).toBe("bitrate");
    expect(withQualityMode(bitrate, "crf").video.quality.crf).toBe(profile.video.quality.crf);
  });
});
```

- [ ] **Step 2: Implement the editor**

`profile.ts` holds the profile's default shape and the mode switches for
quality, scaling, audio, loudness, and transition. `ProfileForm.vue` groups the
fields as the model groups them — video, audio, loudness, scaling, transition —
with each mode switch driving which fields apply, and intro and outro chosen
from `manager/assets`.

The Worker validates on save; its refusal is shown verbatim rather than
duplicated as client-side rules.

- [ ] **Step 3: Verify and commit**

Run: `npm --prefix app/ui run test:unit && npm --prefix app/ui run build`

```bash
git add app/ui
git commit -m "feat(ui): edit processing profiles in the Library Manager"
```

---

### Task 5: Move the order editor and delete the bridge client

**Files:**
- Modify: `app/ui/src/composables/useOrder.ts`, `app/ui/src/views/CollectionsView.vue`, `app/ui/src/router.ts`, `app/ui/src/components/AppSidebar.vue`
- Delete: `app/ui/src/views/OrderView.vue`

- [ ] **Step 1: Rewrite the order composable**

`bridgeGetOrder` and `bridgeSaveOrder` go. The order comes from the collection's
`ordered_clip_ids` and saves with the collection. `deterministicClipCompare` and
`moveId` stay, with their tests, because the deterministic order still matters.

- [ ] **Step 2: Fold the editor into the collection form**

The list, drag-and-drop, and arrow-key reordering move into the collection
form's `custom` playback mode. Copy IDs goes: it existed only as a fallback for
an unreachable bridge.

- [ ] **Step 3: Drop the Playback order section**

Four sections again: Collections, Library, Import, System.

- [ ] **Step 4: Verify and commit**

Run: `npm --prefix app/ui run test:unit && npm --prefix app/ui run build`

```bash
git add -A app/ui
git commit -m "feat(ui): edit playback order with its collection"
```

---

### Task 6: The coordinator carries collection policy

**Files:**
- Modify: `custom_components/cinema_collections/coordinator.py`, `custom_components/cinema_collections/models.py`, `custom_components/cinema_collections/api_client.py`
- Test: `tests/integration/test_entities.py`

- [ ] **Step 1: Write the failing test**

Assert the coordinator's snapshot exposes a collection's `playback_mode`,
`ordered_clip_ids`, `starts_at`, `ends_at`, and `schedule` as fetched from the
Worker.

- [ ] **Step 2: Implement**

`api_client.py` parses the five new fields; the snapshot carries them.

- [ ] **Step 3: Re-point the readers**

`scheduler.py`, `selection.py`, and `resolver.py` take policy from the snapshot
instead of subentries. Their signatures keep taking the same policy shape, so
the change is where the data comes from, not what it looks like.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/integration -q`

```bash
git add custom_components tests
git commit -m "feat(integration): read collection policy from the Worker"
```

---

### Task 7: One-way startup migration

**Files:**
- Create: `custom_components/cinema_collections/migration.py`
- Modify: `custom_components/cinema_collections/__init__.py`, `custom_components/cinema_collections/const.py`
- Test: `tests/integration/test_migration.py`

**Interfaces:**
- Produces: `async_migrate_subentries(hass, entry, client) -> bool` — True when the entry is migrated and its subentries removed.

- [ ] **Step 1: Write the failing tests**

```python
async def test_migration_deletes_subentries_only_after_reading_them_back(...):
    # Worker accepts the pushes and returns matching records.
    assert await async_migrate_subentries(hass, entry, client) is True
    assert entry.subentries == {}
    assert entry.data[CONF_MIGRATED_TO_WORKER] is True


async def test_migration_keeps_subentries_when_the_read_back_disagrees(...):
    # Worker accepts every push but returns a collection with a different name.
    assert await async_migrate_subentries(hass, entry, client) is False
    assert entry.subentries != {}


async def test_migration_keeps_subentries_when_the_worker_is_unreachable(...):
    assert await async_migrate_subentries(hass, entry, client) is False
    assert entry.subentries != {}


async def test_migration_is_a_no_op_once_marked(...):
    assert await async_migrate_subentries(hass, entry, client) is True
    assert client.calls == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/integration/test_migration.py -v`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement the migration**

Push every collection and profile subentry, read both lists back, compare each
pushed record field by field against what came back, and only on a full match
mark `CONF_MIGRATED_TO_WORKER` and remove the subentries. Log the full pushed
payload before deleting anything, so a user can recover from the log if the
Worker later loses it.

- [ ] **Step 4: Run it before platform setup**

In `__init__.py`, call it where `_async_sync_subentries_on_startup` is called
today, and set up platforms only after it returns.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/integration -q`

```bash
git add custom_components tests
git commit -m "feat(integration): migrate configuration into the Worker"
```

---

### Task 8: Delete the subentry forms and the bridge

**Files:**
- Delete: `custom_components/cinema_collections/subentries.py`, `custom_components/cinema_collections/order_view.py`
- Modify: `custom_components/cinema_collections/__init__.py`, `custom_components/cinema_collections/const.py`, `custom_components/cinema_collections/translations/en.json`, `custom_components/cinema_collections/translations/es.json`, `custom_components/cinema_collections/manifest.json`
- Test: the tests covering those modules

- [ ] **Step 1: Remove the modules and their registrations**

Drop the subentry types, `_async_sync_subentries_on_startup`, the order view's
HTTP registration, and every translation key for the two forms. The migration
from Task 7 keeps its own copy of the subentry shape, so deleting the flows does
not break reading what is already stored.

- [ ] **Step 2: Require a Worker that owns configuration**

At setup, compare the Worker's reported version against the minimum this
integration needs, and raise `ConfigEntryNotReady` with a message naming the
required version when it is older.

- [ ] **Step 3: Bump the integration to its major version**

`manifest.json` goes to `2.0.0`.

- [ ] **Step 4: Verify and commit**

Run: `scripts/verify.sh`

```bash
git add -A custom_components tests
git commit -m "refactor(integration): drop the configuration forms and order bridge"
```

---

### Task 9: Documentation and release

**Files:**
- Modify: `README.md`, `docs/getting-started.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/migration.md`, `app/DOCS.md`, `app/config.yaml`, `app/src/cinema_collections_worker/api.py`

- [ ] **Step 1: Rewrite the configuration walkthrough**

Every instruction that creates a collection or a profile through Home Assistant
moves to the Library Manager's Collections section. `docs/migration.md` gains a
section on what moves, what the startup migration does, and the advice to take a
backup first.

- [ ] **Step 2: Bump the Worker**

`app/config.yaml` and `WORKER_VERSION` together — the version-sync test holds
them.

- [ ] **Step 3: Run the release gate**

Run: `scripts/verify.sh`
Expected: every check passes.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: describe configuration living in the Worker"
```
