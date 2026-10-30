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
- 2026-10-29: Ctrl-C or SIGTERM while a step builds exits with status 130
  and prints nothing more: cyclopts 4.25.3 catches `KeyboardInterrupt` and
  exits 130, and the run releases its lock (observed by signalling a real
  `roadie` run).
- 2026-10-29: `roadie <directory>` on a missing directory, and any run without
  git on `PATH`, print a `FileNotFoundError` traceback from
  `find_project_root`. A `Files` directory with its permissions removed
  builds without error.
- 2026-10-29: `uv version <v>` (uv 0.12.23) writes `[project] version` and
  creates or rewrites `uv.lock`; `uv version --frozen <v>` leaves the
  lockfile alone. `uv version --short` prints the version.
- 2026-10-29: `git ls-files --cached --others --exclude-standard -- <dir>`
  skips a directory it cannot read, prints `warning: could not open
  directory '<dir>/': Permission denied` and exits 0. It never lists an
  ignored path, even one named explicitly.
- 2026-10-29: `git tag --sort=-v:refname` orders `v0.2.0-rc1` above
  `v0.2.0`.
- 2026-10-29: pytest's `--junitxml` report has one `testcase` element per
  test, with `classname` and `name`, and a `failure` or `error` child whose
  `message` holds the reason.

--- Implemented / Next ---

## R8: Roadie's state survives upgrades, concurrent runs and moves

Status: implemented 2026-10-29 (commits R8.*)

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
- [x] **R8.3: Allow one run per project root at a time.** A run takes
  `<root>/target/roadie/run.lock`, created exclusively and holding the PID
  and start time. Another run fails with `another roadie run holds <lock>
  (pid <n>, started <time>)`. A lock whose process is gone, or which is
  older than 24 hours, is stale and taken over. The lock is released before
  a requested result is served, so a running dev server does not block
  builds. `--dry` takes no lock.
- [x] **R8.4: Add `--clean`.** Takes the lock, deletes `target/roadie/`
  (records, artifacts, lock) and exits.
- [x] **R8.5: Remove artifacts no record keeps, once a day.** After a
  successful run, while it still holds the lock, Roadie checks a timestamp
  in `target/roadie/`. If 24 hours have passed, it deletes each artifact
  whose stem is not a variable in its build directory's record, which
  includes all artifacts of unnamed steps. It also deletes each record
  directory whose build file no longer exists.
- [x] **R8.6: Make fingerprints independent of the checkout's location.** The
  code hash keys each source file by its module name instead of its
  absolute path. A `Path` attribute is fingerprinted relative to the project
  root in POSIX form when inside it.
- [x] **R8.7: Show `--dry` arguments without dataclass metadata.** Default
  `call_arguments` shows every attribute that is not `None` as `name=value`;
  `Group` keeps its members positional.
- [x] **R8.8: Separate platform-specific process code behind one API.**
  `engine/process.py` keeps `RunningProcess.start` and `stop`. The
  per-platform parts move into clearly named functions: process groups and
  descendant discovery on POSIX, signalling on Windows. macOS finds
  descendants with `ps -A -o pid=,ppid=` where `/proc` is absent. Windows
  keeps its current behavior, marked as unverified until a Windows CI job
  exists (backlog).
- [x] **R8.9: Show each step as code when it builds.** Before a step builds,
  Roadie prints `build <name> = <Class>(<arguments>)`, or
  `build <Class>(<arguments>)` for a step with no name, with the arguments
  `--dry` shows. A deferred step prints its line when it starts; a reused
  step still prints only `reused <name>`; a step without a record, such as a
  group, prints no line. The printer moves from `roadie.cli` into
  `roadie.engine`, which may not import the CLI, and `--dry` uses it from
  there.

**Acceptance run (2026-10-29).** Tree `79ce0bec0275b7aa494461a1cb7c52284e6277b7`.
Host Darwin 25.6.0 arm64. uv 0.12.23. Python 3.13.13. Commands, in order:
`uv run ruff check`, then `uv run ruff check` on `git ls-files '*.py'`,
`uv run mypy src`, `uv run pytest -q --tb=line`. The raw log is outside the
repo, sha256
`3422a3c7f18f6d1bdecc50003d4d2f39deb33d1cef46b89da7b394486ee59546`.

`mypy src` passed (24 files). `pytest` passed (167). `ruff check` on tracked
Python passed. The literal `uv run ruff check` failed with three import-order
errors, all in the untracked nested checkout `roadie/tests/`, which is not in
the commit.

**CI (2026-10-29).** Run 40917836814 on `79ce0be` passed on `ubuntu-latest`
and `macos-latest`. Windows process stopping is not covered by this build;
the backlog item for a Windows CI job and `taskkill /T` covers it.

## R9: Roadie checks and releases itself with Roadie

Status: planned, approved 2026-10-29

**Improvement.** Roadie's own repository has a `build.py`. `roadie . checks`
runs ruff, mypy and pytest as reusable steps, so an unchanged tree reuses
all three, and CI runs that command instead of the three tools. `roadie .
release` releases Roadie: it sets the version, commits, tags and pushes, and
the existing GitHub Release workflow publishes to PyPI. Along the way:
`Files` follows git, so `__pycache__` and other ignored files no longer
change fingerprints; a dependency upgrade reruns the checks; and a missing
directory, a missing git and an unreadable directory are reported as
messages instead of a traceback or a silent skip.

**Acceptance check.**

- `uv run roadie . checks` passes, and a second run with no change prints
  `reused` for ruff, mypy and pytest. CI on `ubuntu-latest` and
  `macos-latest` runs `uv run roadie . checks` and passes.
- Each patch adds tests for its success and failure paths. Release is tested
  against real git repositories with a local bare remote, and real `uv`.
- Live: `uv run roadie . release` on a clean, pushed `main` whose
  `pyproject.toml` says 0.1.1 creates the commit `v0.1.2 release`, pushes it
  with the tag `v0.1.2`, the Release workflow on that tag passes, and PyPI
  lists 0.1.2.

**Decisions confirmed at the checkpoint (2026-10-29).**

- The "build part" of `X.Y.Z` is `Z`.
- The latest tag is the first tag of the form `v<X>.<Y>.<Z>` (integers) in
  the order of `git tag --list 'v*' --sort=-v:refname`; a tag with any other
  form, such as `v0.2.0-rc1`, is skipped. Git's version sort is numeric for
  that form, but puts a suffixed tag above its release.
- The commit message is `v<X.Y.Z> release`.
- After committing, Release tags `v<X.Y.Z>` and pushes the branch and the
  tag atomically to the branch's upstream.
- With no version tag yet, the primary version is released as it is.
- A primary version lower than the latest tag fails, and so does a release
  when `HEAD` is the latest tag's commit (nothing to release).

### Patches

- [x] **R9.1: Fail with a message when the project root cannot be found.**
  `find_project_root` fails with `directory does not exist: <path>` when the
  root build file's directory does not exist, and with `cannot run git to
  find the project root: <reason>` when git cannot be started. Both reach
  the command as a `RoadieError`, with and without `--clean`. The spec's
  Project root section gains both messages; the backlog gap goes.
- [x] **R9.2: Start `cannot check whether` in lower case.** The message, its
  test and the spec's Generated files section change together.
- [x] **R9.3: Make `Files` follow git.** In a git work tree, a directory
  stands for the files `git ls-files --cached --others --exclude-standard`
  lists under it; a file named explicitly counts even when ignored. Outside
  a work tree, a directory stands for every file under it, as today. A
  directory that cannot be read fails the step with `cannot read <path>:
  <reason>`; under git, any warning `git ls-files` prints is that reason,
  because git skips an unreadable directory and exits 0. `CargoPackage`
  shares the listing. Both backlog items (the feature and the gap) go.
- [x] **R9.4: Add the `PythonEnv` input.** `PythonEnv()` fingerprints the
  version of the interpreter running Roadie and the name and version of
  every distribution installed in its environment, so upgrading mypy reruns
  the checks that take it. It replaces the backlog's `PythonEnv` item, which
  assumed a tracked lockfile.
- [x] **R9.5: Add the `Check` step.** `Check(*command, inputs=(...))` is a
  `Tests` step that runs `command` in its base directory. Exit status 0 is a
  pass; any other status is one failure named after the command, with the
  tail of its output. `inputs` are fingerprinted.
- [ ] **R9.6: Add the `Pytest` step.** `Pytest(*args, inputs=(...))` runs
  `<interpreter running Roadie> -m pytest --junitxml=<artifact> <args>`
  and reads each `testcase` with a `failure` or `error` element into a
  `TestFailure` named `<classname>::<name>`. Exit status 0 and 1 are read
  from the report; any other status (usage error, no tests collected) fails
  the step with the status and the tail of standard error. The pytest half
  of the backlog's test-steps item goes; cargo-nextest stays.
- [ ] **R9.7: Add the `Release` step and the `PyProject` version source.**
  `Release(primary, *secondary, requires=(...))` never reuses and keeps no
  record. It fails, before changing anything, with `cannot release: <reason>`
  when the work tree has uncommitted changes, the branch has no upstream, or
  the branch is behind its upstream after `git fetch`. It then reads the
  primary source's version, compares it with the latest tag as the
  checkpoint decisions say, writes the version to every source that differs,
  and commits those files as `v<X.Y.Z> release` when any changed. Finally it
  tags, pushes, and prints `released v<X.Y.Z>`; its value is the version.
  A version source is an `Input`, fingerprinted by its file's content.
  `PyProject(path)` reads `[project] version` and fails with `<path> has no
  static [project] version` when it is absent or dynamic. It writes with
  `uv version --frozen <version>`, or without `--frozen` when git tracks a
  `uv.lock` beside it, which then joins the commit, because `uv version`
  otherwise creates or rewrites that lockfile. The docstrings specify the
  kind, as for every step kind.
- [ ] **R9.8: Check Roadie with Roadie.** The repository root gets a
  `build.py`: ruff and mypy as `Check` steps and the suite as `Pytest`, each
  taking `Files("src", "tests", "pyproject.toml", "build.py")` and
  `PythonEnv()`; `release = Release(PyProject("pyproject.toml"),
  requires=(...))` on the three checks; and `checks`, a `Group` of the
  three, constructed last, so a bare `roadie .` checks and never releases.
  CI runs `uv run roadie . checks` in place of the three commands.

**Live step, approved at the checkpoint.** After R9.8 is pushed and CI is
green, release Roadie 0.1.2 with `uv run roadie . release`. It pushes a
commit and a tag to `main` and publishes to PyPI, which cannot be undone.

**Author action required.**

- Before execution: delete the untracked nested checkout `roadie/` at the
  repository root. `ruff check`, which R9.8 runs as a step, lints it and
  fails on its import order.
- At completion: run, or authorize the executor to run, the live release
  above, and confirm PyPI lists 0.1.2. This discharges the live clause of the
  acceptance check.

## Backlog

See [backlog.md](backlog.md).
