# Builds

Builds that change Roadie's [spec](../spec.md), one record per build: executed
builds, then the one planned build. Unplanned work is in
[backlog.md](backlog.md).

## Facts

- 2026-10-29: CI runs on `ubuntu-latest` and `macos-latest` only
  (`.github/workflows/ci.yml`); no Windows job exists.
- 2026-10-29: the code hash covers each source file's absolute path
  (`engine/fingerprint.py`, `_code_hash`), so moving a checkout or its virtual
  environment changes every fingerprint. Records and artifacts live in each
  checkout's `target/roadie/`; nothing is shared between machines.
- 2026-10-29: macOS has no `/proc`, so `RunningProcess.stop` finds no
  descendants there and stops only the process group
  (`engine/process.py`, `_posix_descendant_pids`).

--- Implemented / Next ---

## R8: Roadie's state survives upgrades, concurrent runs and moves

Status: planned, approved 2026-10-29

**Improvement.** Resolves the spec's open questions about state. A reused
value comes back with its built type. A record from another Roadie version,
or a damaged one, is rebuilt instead of failing the run. Two runs on one
project cannot corrupt each other's records. `target/roadie/` no longer
grows without bound. Moving a checkout keeps its records valid. Stopping a
server stops its whole process tree on macOS, as it does on Linux. The log
shows each step, as code, when it builds.

**Acceptance check.** `uv run ruff check`, `uv run mypy src` and
`uv run pytest` pass on both CI platforms. Each patch adds tests for its own
success and failure paths, and removes its item from the spec's Open
questions in favor of a rule. No item is left in Open questions.

**Decisions at the checkpoint (2026-10-29).** `--clean` deletes
`target/roadie/`. `--dry` shows every attribute as `name=value`. R8.9 added
at the author's request on 2026-10-29.

### Patches

- [x] **R8.1: Restore recorded values with their declared type.** The default
  `encode` and `decode` use a Pydantic `TypeAdapter` for the step's value
  type `T` from `Step[T]`, so a tuple comes back as a tuple. Adds the
  `pydantic` dependency. A step class with no concrete `T` keeps JSON as
  is.
- [x] **R8.2: Version the record file.** The record holds a format version and
  its entries, both read through a Pydantic model. A record of another
  version counts as empty, so its steps rebuild. A record that cannot be
  read is also treated as empty, and Roadie prints `ignoring unreadable
  record <path>: <reason>`, so a damaged file never fails the run.
- **R8.3: Allow one run per project root at a time.** A run takes
  `<root>/target/roadie/run.lock`, created exclusively and holding the PID
  and start time. Another run fails with `another roadie run holds <lock>
  (pid <n>, started <time>)`. A lock whose process is gone, or which is
  older than 24 hours, is stale and taken over. The lock is released before
  a requested result is served, so a running dev server does not block
  builds. `--dry` takes no lock.
- **R8.4: Add `--clean`.** Takes the lock, deletes `target/roadie/`
  (records, artifacts, lock) and exits.
- **R8.5: Remove artifacts no record keeps, once a day.** After a
  successful run, while it still holds the lock, Roadie checks a timestamp
  in `target/roadie/`. If 24 hours have passed, it deletes each artifact
  whose stem is not a variable in its build directory's record, which
  includes all artifacts of unnamed steps. It also deletes each record
  directory whose build file no longer exists.
- **R8.6: Make fingerprints independent of the checkout's location.** The
  code hash keys each source file by its module name instead of its
  absolute path. A `Path` attribute is fingerprinted relative to the project
  root in POSIX form when inside it.
- **R8.7: Show `--dry` arguments without dataclass metadata.** Default
  `call_arguments` shows every attribute that is not `None` as `name=value`;
  `Group` keeps its members positional.
- **R8.8: Separate platform-specific process code behind one API.**
  `engine/process.py` keeps `RunningProcess.start` and `stop`. The
  per-platform parts move into clearly named functions: process groups and
  descendant discovery on POSIX, signalling on Windows. macOS finds
  descendants with `ps -A -o pid=,ppid=` where `/proc` is absent. Windows
  keeps its current behavior, marked as unverified until a Windows CI job
  exists (backlog).
- **R8.9: Show each step as code when it builds.** Before a step builds,
  Roadie prints `build <name> = <Class>(<arguments>)`, or
  `build <Class>(<arguments>)` for a step with no name, with the arguments
  `--dry` shows. A deferred step prints its line when it starts; a reused
  step still prints only `reused <name>`; a step without a record, such as a
  group, prints no line. The printer moves from `roadie.cli` into
  `roadie.engine`, which may not import the CLI, and `--dry` uses it from
  there.

## Backlog

See [backlog.md](backlog.md).
