# Agent Instructions

Read `CLAUDE.md` before making code changes.

## Change Rules

- Update `DEVLOG.md` after every code or documentation change.
- Create database migration files only when a change modifies database schema. This project currently has no database layer.
- Do not use destructive git commands unless the user explicitly asks for them.
- Keep changes scoped to playback, routing, device handling, UI, tests, or documentation requested by the task.

## Verification

- Run `python3 -m compileall .` after Python changes.
- Run `python3 -m pytest` when tests are available.
- For GStreamer behavior, also perform the manual checks in `docs/TESTING.md` on a machine with PulseAudio or PipeWire-Pulse.
