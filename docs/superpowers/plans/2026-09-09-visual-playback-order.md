# Visual Playback Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible/copyable clip IDs and a drag-and-drop playback-order editor that persists custom order through an authenticated Home Assistant bridge.

**Architecture:** The Worker Library Manager owns the visual catalog editor and keeps Worker routes relative for Ingress. A new authenticated Home Assistant HTTP view owns persistence of `playback_mode=custom` and `ordered_clip_ids` in the existing collection subentry; the browser falls back to copying IDs when the bridge is unavailable.

**Tech Stack:** Python 3.13, Home Assistant custom integration, FastAPI server-rendered HTML, vanilla JavaScript/CSS, pytest, pytest-homeassistant-custom-component.

**Spec:** `docs/superpowers/specs/2026-09-09-visual-playback-order-design.md`

## Global Constraints

- Do not modify existing Home Assistant installations or physical playback automations.
- Keep Worker filesystem operations and API routes unchanged; no database migration.
- Keep all user-facing strings in English in the Worker UI; Home Assistant translations remain English/Spanish.
- Preserve deterministic sequential ordering and durable history behavior.
- Keep all Worker action URLs relative for App Ingress; only the HA bridge uses a same-origin absolute URL.
- Use TDD: every production behavior starts with a failing test.

---

### Task 1: Authenticated Home Assistant order bridge

**Files:**
- Create: `custom_components/cinema_collections/order_view.py`
- Modify: `custom_components/cinema_collections/__init__.py`
- Test: `tests/integration/test_order_view.py`

**Interfaces:**
- `POST /api/cinema_collections/order` accepts `{collection_id: str, ordered_clip_ids: list[str], entry_id?: str}` and returns the saved mode/order.
- `GET /api/cinema_collections/order?collection_id=<id>&entry_id=<id?>` returns the current mode/order.

- [ ] Write failing tests for single-entry GET, authenticated POST persistence, invalid duplicate IDs, and ambiguous multi-entry selection.
- [ ] Run `uv run pytest tests/integration/test_order_view.py -q` and verify the new tests fail because the view is absent.
- [ ] Implement a `HomeAssistantView` with authenticated request handling, entry resolution, collection lookup, `normalize_clip_order`, and `async_update_collection_subentry`; refresh the coordinator after a successful update.
- [ ] Register the view once from `async_setup` without changing setup-entry lifecycle.
- [ ] Run the focused tests and verify they pass.

### Task 2: Visible IDs and visual order editor in Worker Manager

**Files:**
- Modify: `app/src/cinema_collections_worker/manager_web.py`
- Modify: `app/src/cinema_collections_worker/templates/manager.html`
- Modify: `app/src/cinema_collections_worker/static/manager.css`
- Test: `tests/worker/test_library_manager_web.py`

**Interfaces:**
- Existing Worker routes remain relative.
- Browser bridge request uses `window.location.origin + '/api/cinema_collections/order'` and degrades to copy-only behavior when unavailable.

- [x] Add failing HTML assertions for the visible Clip ID column, Copy ID controls, order tab, collection selector, drag list, and save/copy/reset controls.
- [x] Run the focused Worker web tests and verify failure.
- [x] Render full IDs safely, add clipboard fallback, build the order list from catalog rows, implement keyboard-accessible drag/drop plus deterministic reset, and add bridge GET/POST handling with explicit failure feedback.
- [x] Preserve all existing relative action URLs and existing trash/delete behavior.
- [x] Run `uv run pytest tests/worker/test_library_manager_web.py -q` and verify all focused tests pass.

### Task 3: Documentation and translation-facing guidance

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/security.md`

- [x] Document where IDs appear, the visual editor flow, bridge availability, and copy fallback.
- [x] Document that the saved order is integration-owned and unavailable IDs are skipped safely.
- [x] Run repository documentation/metadata checks.

### Task 4: Full verification and release preparation

**Files:**
- Modify: `custom_components/cinema_collections/manifest.json` only if a version bump is required by the release policy.

- [x] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright`.
- [x] Run the repository verification script; record Docker limitations accurately. Local Docker daemon was unavailable; application, contract, metadata, lint, type, and test checks passed.
- [x] Review the diff for secrets, absolute paths, unsafe HTML, and accidental changes outside scope.
