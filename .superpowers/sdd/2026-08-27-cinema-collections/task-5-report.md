# Task 5 report

Status: complete

Commit: `a572e47 feat(worker): validate processing profiles`

Implemented typed Pydantic processing-profile sections with discriminated
quality, scaling, missing-audio, loudness, and transition modes; deterministic
SHA-256 profile fingerprints; the Compatibility 4K Loudness baseline; decode
error policy documentation; and focused validation tests.

Tests and output:

- `.venv/bin/pytest tests/worker/test_profile_validation.py tests/worker/test_compatibility_profile.py -v` — 9 passed.
- `.venv/bin/pytest -q` — 47 passed.
- `.venv/bin/ruff check ...` — all checks passed.
- `git diff --check` — clean.

## Round 2 regression fix

`profile_fingerprint` now accepts either an `AssetFingerprints` instance or a
mapping and always coerces through `AssetFingerprints.model_validate(...)`.
Added a regression test covering alias-based mapping input.

Round 2 verification:

- Focused profile tests — 15 passed.
- Full suite — 53 passed.
- Ruff — all checks passed.
- Pyright — 0 errors, 0 warnings, 0 informations.
- `git diff --check` — clean.

Self-review: existing model/path/repository APIs remain available; no raw
FFmpeg/filter strings or Home Assistant changes were introduced. References
remain typed strings and are intentionally not filesystem-resolved during
profile validation; queue-time code can apply `SafePathResolver`.

Concerns: the current task only defines profile validation and does not yet
provide the queue implementation that performs intro/outro path resolution or
FFmpeg argument generation.

## Round 1 review fixes

- Intro/outro references now use `validate_relative_path` plus filename
  validation, rejecting absolute paths, traversal, control characters, and
  unsafe final path components while preserving queue-time root resolution.
- Added `minimum_segment_duration_seconds` and a test proving fade-in plus
  fade-out cannot exceed it.
- Corrected the audio-policy test to use the valid discriminated
  `RequiredAudio` policy and directly exercise `AudioSettings.validate_policy`.

Round 1 verification:

- `.venv/bin/pytest -q` — 52 passed.
- `.venv/bin/ruff check ...` — all checks passed.
- `.venv/bin/pyright app/src/cinema_collections_worker/profile_validation.py app/src/cinema_collections_worker/default_profiles.py app/src/cinema_collections_worker/models.py` — 0 errors, 0 warnings, 0 informations.
- `git diff --check` — clean.
