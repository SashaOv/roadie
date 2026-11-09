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
  `find_project_root`.
- 2026-11-01: a file `Files` cannot read fails with `cannot read <path>:
  <reason>`.
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
- 2026-10-31: on macOS 25.6 with Python 3.13.13, `fcntl.flock(LOCK_EX)` on
  a lock file blocks while another process holds it and returns once that
  process is killed; the descriptor is not inheritable, so a child process
  started while the lock is held does not keep it after the parent unlocks.
  `os.process_cpu_count()` returns 10 on that host.
- 2026-10-31: `Step._cmd` runs its program in Roadie's process group with
  inherited standard output, so a terminal's Ctrl-C reaches it directly;
  SIGTERM reaches only Roadie. `subprocess.run` kills the child on any
  exception in the calling thread, `KeyboardInterrupt` included (its bare
  `except:` in CPython 3.13.13), and Python never interrupts a worker thread.

- 2026-10-31: cyclopts 4.25.3 matches an option spelled with underscores to
  its hyphenated name: with only `--base-dir` declared, `--base_dir x` sets
  it, and `--help` lists `--base-dir` alone.
- 2026-10-31: in a scratch Cargo workspace (cargo 1.99.0), editing
  `[workspace.package] version` and running `cargo update --workspace
  --offline` rewrites only the members' `version` lines in `Cargo.lock`;
  `cargo metadata --no-deps` leaves the lockfile alone, and `cargo build
  --locked` fails on the stale lockfile. `cargo locate-project --workspace
  --message-format plain` prints the workspace's root `Cargo.toml` from a
  member's directory.
- 2026-10-31: Niva's root `Cargo.toml` holds `[workspace.package] version =
  "0.5.4"`, matching its latest tag `v0.5.4` (a lightweight tag), and its
  build file has no `Release`. Its `package.json` files do not follow that
  version (`desktop/package.json` is `0.5.0`, the root has none). Its
  `pnpm-lock.yaml` importers list dependencies only, not the package's own
  version, and it tracks no `package-lock.json`.
- 2026-10-31: cyclopts 4.25.3 passes `name=value` words through unchanged:
  with `directory`, `result` and `*parameters` positional, `. release
  message=a=b` gives `'.'`, `'release'`, `('message=a=b',)`, and `message=x`
  alone lands in `directory`, so Roadie sorts the words itself.
- 2026-10-31: Niva's workflows run `roadie --locked . <result>` (nine
  lines under `.github/workflows/`), and its docs `roadie --locked jammer
  generated`, naming the build directory as a word. Niva installs Roadie
  from PyPI, so they break when Niva upgrades past R13, not before.
- 2026-10-31: none of Niva's included build files (`forge`, `notation`,
  `desktop`, `jammer`) binds a result named like a public `Step` member
  (`build`, `report`, `serve`, `teardown`, ...); its root file's `build`
  result is never included.

- 2026-11-01: on `windows-latest` (probe runs 41283209577 and 41284571941
  on a deleted branch, `bebee9e` with CI on Windows only; uv chose Python
  3.15.1), `uv sync` and `ruff` pass, and `tests/engine/test_jobs.py` passes
  (18 passed, 2 skipped). Three failures:
  - `test_a_step_that_runs_roadie_in_the_same_project_fails` hangs: the
    nested run prints `waiting for another roadie run: <lock>` with no
    `(pid ...)` and waits, so `ROADIE_RUN_PID` never matches. Likely cause,
    unverified: `msvcrt.locking` locks the bytes holding the PID, so the
    waiter cannot read them.
  - `mypy src build.py` reports six errors in `engine/process.py`:
    `os.killpg`, `os.getpgid` and `signal.SIGKILL` do not exist on Windows;
    the guards test `os.name`, which mypy does not narrow on, unlike
    `sys.platform`.
  - About 25 other tests fail (`F` marks before the hang); their names were
    not captured, because the timeout ended pytest before its summary.
  The author stopped the probes here, to continue on a Windows machine.
- 2026-11-01: on Windows 11 (Python 3.14.3, uv 0.11.26, git 2.53), the
  same mypy errors and the same hang. `ruff` passes; pytest without the
  hanging test gives 21 failed, 277 passed, 7 skipped (`test_jobs.py`
  passes). The hang's cause is confirmed: `msvcrt.locking` locks byte 0,
  which the token also occupies, so `_read` gets `PermissionError` and
  returns `None`; every waiter prints no `(pid ...)`, failing three more
  `test_lock.py` tests. The other failures:
  - Product: a command that cannot start reports `[WinError 2] The system
    cannot find the file specified` without the command's name
    (`test_check.py`).
  - Test assumptions: `pytest.raises(match=f"...{path}")` with an unescaped
    Windows path (3 tests); hand-built cargo JSON with unescaped backslashes
    (`test_rust.py`); `chmod(0)` does not make a directory unreadable (3
    tests); `pty`/`termios` (5 `test_user_task.py` tests); `symlink_to`
    needs a privilege (`test_fingerprint.py`); a copied executable's mode
    differs from its source (2 `test_rust.py` tests); a text file's diff
    shows `\r\n` and lacks the `---`/`+++` pair the test expects
    (2 `test_file.py` tests, unclassified).
- 2026-11-01: on the same machine, parallel steps work: steps run on worker
  threads that start programs with `subprocess`, and `test_jobs.py` passes
  with `--jobs` above 1 except its two SIGTERM tests, skipped. On Python
  3.14.3 for Windows, `Thread.join()` with no timeout cannot be interrupted:
  `_thread.interrupt_main()` one second into a ten-second join raised
  `KeyboardInterrupt` after 10.0 s, while `join(0.1)` in a loop raised it
  after 1.0 s. With `--jobs` above 1 the main thread waits in `join()`
  (`engine/run.py`, `execute`), so Ctrl-C waits for every running step.
- 2026-11-02: the sweep keeps an artifact when its `Path.stem` is a recorded
  variable (`engine/records.py`, `_sweep_directory`). `Path("web_schema.d.ts").stem`
  is `web_schema.d`, so a sweep deletes the `.d.ts` artifact of Niva's
  recorded `web_schema` step (`jammer/ops/jammer_build/steps.py`, `WebSchema`).
- 2026-11-02: Niva (`9cfc3d7`) binds every step it builds to a variable; its
  `cargo_check` helper returns one `Check` over a tuple of packages, so a
  change in one package reruns the check for all of them.
- 2026-11-02: in a scratch pnpm workspace (pnpm 10.33.0, Node 24.12.0),
  `pnpm ls -r --json --depth -1` lists each project's `name` and absolute
  `path` before `pnpm install`, with no dependencies. `--depth 0
  --only-projects` lists workspace dependencies (`"version": "link:../core"`)
  only after install. Run outside any workspace, it prints one JSON array per
  `package.json` found below the working directory.

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

Status: implemented 2026-10-30 (commits R9.*)

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
- [x] **R9.6: Add the `Pytest` step.** `Pytest(*args, inputs=(...))` runs
  `<interpreter running Roadie> -m pytest --junitxml=<artifact> <args>`
  and reads each `testcase` with a `failure` or `error` element into a
  `TestFailure` named `<classname>::<name>`. Exit status 0 and 1 are read
  from the report; any other status (usage error, no tests collected) fails
  the step with the status and the tail of standard error. The pytest half
  of the backlog's test-steps item goes; cargo-nextest stays.
- [x] **R9.7: Add the `Release` step and the `PyProject` version source.**
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
- [x] **R9.8: Check Roadie with Roadie.** The repository root gets a
  `build.py`: ruff and mypy as `Check` steps and the suite as `Pytest`, each
  taking `Files("src", "tests", "pyproject.toml", "build.py")` and
  `PythonEnv()`; `release = Release(PyProject("pyproject.toml"),
  requires=(...))` on the three checks; and `checks`, a `Group` of the
  three, constructed last, so a bare `roadie .` checks and never releases.
  CI runs `uv run roadie . checks` in place of the three commands. Tests
  that run steps without a project root record into a sandbox outside the
  checkout, so the suite can run while `roadie` holds the checkout lock.

**Acceptance run (2026-10-30).** Commit `9104d71dbb823fbdfd12146dda8f1e3d05416592`,
tree `70d8791690afb2206bd869f52d7e34a9628120a9`. Host Darwin 25.6.0 arm64.
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin). Python 3.13.13.
`uv run roadie . --clean`, then `uv run roadie . checks` twice. The raw log
is outside the repo at
`/Users/sasha/.local/share/roadie/r9-acceptance.log`, sha256
`049d3874b6d420f985f05a83553948f10b48e4f44b1589e16f6935f7ba0652cf`.

The first `checks` exited 0 and built ruff, mypy and pytest. The second
exited 0 and printed `reused` for ruff, mypy and pytest.

**CI.** Not observed. The commits are local; `main` has not been pushed, so
the workflow on `ubuntu-latest` and `macos-latest` has not run.

**Review (2026-10-30).** `main` was then pushed. CI run 41021717841 on
`9104d71` ran `uv run roadie . checks` and failed on both platforms:
`test_release_fails_before_changing_anything: DID NOT RAISE StepFailed`. The
test's bare remote took its default branch from git config. macOS's Command
Line Tools set `init.defaultBranch=main` system-wide, and CI runners do not,
so there the clone pushed to `master` and the release branch was never
behind. Reproduced locally with `GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null`. Fixed by creating the bare remote with
`-b main`, in `6df0323 Create release test remotes on main.`, merged to
`main` as `65d3505` (pull request #1). CI run 41090651785 on `65d3505`
passed on `ubuntu-latest` and `macos-latest`, running `uv run roadie .
checks`. That discharges the CI clause.

The review also found `--dry release` marking `release` as `# reuse`, though
Release never reuses: a kind with no record was marked whenever its
dependencies were. A kind that disallows reuse is now always `# run`, with a
test for both cases and the spec's `--dry` rule updated.

With the `--dry` fix, uncommitted, on `65d3505`: the suite passes (193)
with no system or global git config, `uv run roadie . checks` exits 0, and
`--dry release` marks `release` as `# run`. CI is not yet observed on it.

**Live step, approved at the checkpoint.** After R9.8 is pushed and CI is
green, release Roadie 0.1.2 with `uv run roadie . release`. It pushes a
commit and a tag to `main` and publishes to PyPI, which cannot be undone.
Run by the author on 2026-10-30 from `def1a79`, with the committed
`build.py`: it created `7c20ba7 v0.1.2 release` (only `pyproject.toml`
changed) and pushed it with the tag `v0.1.2`. CI and the Release workflow on
`v0.1.2` passed, and PyPI lists 0.1.2. That discharges the live clause.

**Author action required at completion.**

- ~~Push `main` and confirm CI on `ubuntu-latest` and `macos-latest` runs
  `uv run roadie . checks` and passes.~~ Done: run 41090651785 on `65d3505`.
- ~~Then run, or authorize the executor to run, `uv run roadie . release` on
  that clean, pushed `main` (pyproject still says 0.1.1), and confirm PyPI
  lists 0.1.2.~~ Done by the author, 2026-10-30: `v0.1.2` is on PyPI.

## R10: Build files say what they mean, and `--dry` is the run itself

Status: implemented 2026-10-30 (commits R10.*)

**Improvement.** A build file names its default result with
`default_step(...)`, so `release` can be written last and a bare `roadie .`
still only checks. Every parameter that takes several steps, inputs or
files also takes one, so `requires=checks` works and type-checks; Roadie's
own `build.py` is type-checked by its gate. `--dry` walks the plan through
the same code as a run and differs only where a run would build a step, so
what it marks is what a run does. The suite gives the same result on a
developer machine as on CI.

**Acceptance check.**

- `uv run roadie . checks` passes, including mypy on `build.py`, and a second
  run reuses ruff, mypy and pytest. CI passes on `ubuntu-latest` and
  `macos-latest`.
- With Roadie's `build.py` defining `release` last: `uv run roadie . --dry`
  lists the checks and not `release`; `uv run roadie . release --dry` lists
  the three checks, `checks`, and `release  # run`.
- Each patch adds tests for its success and failure paths.

**Decision confirmed at the checkpoint (2026-10-30).** `--dry` marks a step
the way a run reports it: `# reuse` where a run prints `reused <name>` (a reused step,
or a group whose members are all reused), `# not started` where it prints
`not started <name>`, and `# run` otherwise. Today a deferred step that will
not start is marked `# run`.

### Patches

- [x] **R10.1: Mark the default result with `default_step`.**
  `default_step(step)` returns `step` and makes it the result `roadie` runs
  when no result is named; without a mark, the result constructed last runs,
  as today. Only the root build file's mark counts; an included file's mark
  is ignored. Marking twice in one file fails with `<path> marks more than
  one default result: <a>, <b>`; marking a step that no variable of the file
  holds fails with `<path>: the default result is not bound to a variable`;
  calling it outside a loading build file fails with `default_step() works
  only in a build.py that Roadie loads`. `roadie.build` exports it, and the
  spec's Command line section names it.
- [x] **R10.2: Take one value or many wherever a step takes several.**
  `Release(requires=...)`, `Check(inputs=...)`, `Pytest(inputs=...)` and
  `config(env=...)` accept a single step, input or path, or any iterable of
  them, typed with `Step[Any]` so a `tuple[Check, Check, Pytest]` or a `Group`
  type-checks. One shared function stores them as a tuple, because a step's
  attributes must not change after construction: they are fingerprinted. An
  element of the wrong kind fails with `<Class> <parameter> takes <kind>:
  got <type>`.
- [x] **R10.3: Type-check Roadie's `build.py`.** The mypy step runs
  `mypy src build.py`. `build.py` imports `default_step` instead of defining
  a stub, writes `checks = default_step(Group(ruff, mypy, pytest))` and then
  `release = Release(PyProject("pyproject.toml"), requires=checks)`. This
  builds on the author's uncommitted `build.py`.
- [x] **R10.4: Make `--dry` the run itself.** `--dry` goes through the run's
  own loop: loading, environment files, fingerprints, record matching,
  deferred steps. Where a run would build a step or start a deferred one, a
  dry run marks it `# run` and gives it a placeholder output fingerprint
  instead. It takes no lock, writes no records, publishes, serves and sweeps
  nothing, and tears nothing down. It prints the marks as the checkpoint
  decision says. `reuse_markers` goes, and the spec's `--dry` rule is
  restated from the run's.
- [x] **R10.5: Run the suite without the machine's git config.** A session
  fixture sets `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL` to an empty
  file, so a test cannot pass only because of a system default such as
  macOS's `init.defaultBranch=main`, as happened in R9.

**Acceptance run (2026-10-30).** Commit `21f7ac42990f1621570f7831c50ce22df14c6559`,
tree `8ff8296dd83ffa34044e490228cb0e76e6b8cb44`. Host Darwin 25.6.0 arm64.
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin). Python 3.13.13.
`uv run roadie . --clean`, then `uv run roadie . checks` twice, then
`uv run roadie . --dry` and `uv run roadie . release --dry`. The raw log is
outside the repo at `/Users/sasha/.local/share/roadie/r10-acceptance.log`,
sha256 `16d632aed3de8f8a06b2135b1d7dda55990bb29d4d45994a86a9154d8f8f353d`.

The first `checks` exited 0 and built ruff, mypy (`mypy src build.py`), and
pytest. The second exited 0 and printed `reused` for ruff, mypy, and pytest.
`roadie . --dry` listed ruff, mypy, pytest, and checks, and not release.
`roadie . release --dry` listed those four and `release  # run`.

**CI (2026-10-30).** Run 41120109921 on `21f7ac4` passed on `ubuntu-latest`
and `macos-latest`. That discharges the CI clause.

**Author action required at completion.** Push `main` and confirm CI passes
on both platforms, or authorize the executor to push. Authorized with this
build: `main` was pushed at `21f7ac4`, and run 41120109921 passed.

**Review (2026-10-31).** Commit subjects match the patch names. The R10
acceptance passes again on the reviewed tree: `roadie . checks` passes and a
second run reuses ruff, mypy and pytest; `roadie . --dry` omits `release` and
`roadie . release --dry` marks it `# run`. Two changes the author asked for
after review: `--dry` takes the run lock like a run, so `execute` no longer
branches on it (this reverses R10.4's "takes no lock"); and `build.py` starts
with `from roadie.build import *`, as the spec says a build file usually does.

## R11: Independent steps run at once, and a second run waits its turn

Status: implemented 2026-10-31 (commits R11.*)

**Improvement.** `roadie` runs the steps of a plan that do not depend on each
other at the same time, up to `--jobs` of them, so `roadie . checks` runs
ruff, mypy and pytest together. While several steps run, each step's output is
printed as one block, so it stays readable. A run that finds another run
holding the lock waits for it, naming the holder once, instead of failing,
and a run that a step of the holding run started fails at once instead of
waiting for itself.

**Decisions at the checkpoint (2026-10-31).** Keep `--jobs`, which no
backlog item asked for, because `--jobs 1` helps troubleshooting.

1. Confirmed: `--jobs` defaults to the CPUs Roadie may use
   (`os.process_cpu_count()`), like ninja and cargo.
2. Confirmed: with `--jobs` above 1, output stays live, and each line a step
   prints, its programs' output included, starts with `[<name>] `, so it is
   easy to filter out; `<name>` is the step's name, or its class name when it
   has none. With `--jobs 1`, output is unprefixed, as today.
3. Confirmed: when a step fails, no new step starts; Roadie prints
   `waiting for <names>` once and lets the running steps finish, then tears
   down and fails with the first failure, printing later failures to standard error. Cargo does
   the same (`build failed, waiting for other jobs to finish...`). Steps that
   finish are recorded, so the next run reuses them.

**Acceptance check.**

- `uv run roadie . checks` passes and CI passes on `ubuntu-latest` and
  `macos-latest`.
- After `uv run roadie . --clean`, `uv run roadie . checks` prints the
  `build` lines of ruff, mypy and pytest before pytest's summary line, every
  line of pytest's output starts with `[pytest] `, and a second run reuses
  all three.
- With `uv run roadie . checks` running after a source change, a second
  `uv run roadie . --dry` in another terminal prints the waiting line once,
  then its marks after the first run ends.
- Each patch adds tests for its success and failure paths. Concurrency is
  shown by steps that each wait, with a timeout, for the other to start;
  nothing in a test sleeps.

### Patches

- [x] **R11.1: Wait for the run lock instead of failing.** The run lock is an
  OS lock on `run.lock` (`fcntl.flock` on POSIX, `msvcrt.locking` on
  Windows, unverified until the Windows CI job), and the holder still writes
  its PID and start time into the file. A run, a dry run or `--clean` that
  finds it held prints `waiting for another roadie run: <lock> (pid <n>,
  started <time>)` once, without the parenthesis when the file cannot be
  read yet, and blocks until it is released. The OS releases a dead run's
  lock, so the stale-lock rules (process gone, 24 hours, unreadable) go.
  Releasing unlocks without deleting the file, and `--clean` deletes
  everything under `target/roadie/` except `run.lock`, so two runs can never
  hold locks on different files. Tests start a holder process and wait for
  the waiting line, never for time: the waiter proceeds after the holder
  releases, and after the holder is killed.
- [x] **R11.2: Fail at once when the holder started this run.** A run puts
  its PID in the environment of the programs its steps run. A run that finds
  the lock held by that PID fails at once with `<lock> is held by the roadie
  run that started this one (pid <n>)` instead of waiting for itself. Tests:
  a step that runs `roadie` in the same project root fails with that message;
  a run started by an unrelated process holding the variable still waits.
- [x] **R11.3: Run up to `--jobs` steps at once.** `--jobs N` (`-j N`) runs a
  step as soon as every step it takes is done, up to N at a time; a value
  below 1 fails with `--jobs must be at least 1`. The default stays 1 in this
  patch. Fingerprints, records, `run[...]` and teardown stay correct when
  steps finish in any order: records are written by one thread, `run_state`
  makes each state once, and worker threads see the run's project root. A
  deferred step starts once, before the first consumer that must build; a
  consumer that needs it while it starts waits for it. A step is torn down
  after every step that takes it has finished. Failure follows decision 3.
  On Ctrl-C or SIGTERM, Roadie stops the programs its steps are running,
  waits for those steps, and tears down. `--dry` uses the same scheduler and
  prints its marks in plan order. The spec's Plans and runs concept and
  Teardown section say "after every step that takes it" instead of "after the
  last".
- [x] **R11.4: Prefix each line of a step's output with its name, and run on
  all CPUs by default.** With `--jobs` above 1, a step's output follows
  decision 2: what the step prints and the output of the programs `_cmd` and
  `_start_cmd` run are read line by line and printed live with the prefix, a
  line at a time, so lines of different steps never mix. The `--jobs`
  default follows decision 1.

**Acceptance run (2026-10-31).** Commit `6b01434d07f30d284bfa327279c4cdad1595c934`,
tree `a5e14253189775580dbbfb308dce09c50d19ac9d`. Host Darwin 25.6.0 arm64.
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin). Python 3.13.13.
`process_cpu_count` 10, so the `--jobs` default is 10. On that commit,
`uv run roadie . --clean` then `uv run roadie . checks` exited 0 and printed
the prefixed `build` lines, but pytest's own output was missing: `Pytest`
captures standard output, and a parallel run did not print captured output.
The second `checks` reused ruff, mypy, and pytest. A second terminal's
`--dry` did not wait, because a timestamp touch does not change a
fingerprint. That log is outside the repo at
`/Users/sasha/.local/share/roadie/r11-acceptance-commit.log`, sha256
`4cdb7fcfad355efb28fb143643cad75d19a72384f8e571574eadcf42d1f59a8c`.

The fix is uncommitted, in `src/roadie/engine/step.py` and
`tests/engine/test_jobs.py`: with `--jobs` above 1, captured standard output
is printed live with the prefix and still returned unprefixed; with `--jobs`
1 it stays hidden. `uv run pytest -q --tb=line` is 232 passed on that tree.
Re-ran on the dirty tree: `uv run roadie . --clean`, `uv run roadie . checks`
twice, then a one-line addition to `prefix.py` (restored afterward) so a
third `checks` rebuilt while `uv run roadie . --dry` waited. Script
`/Users/sasha/.local/share/roadie/r11-acceptance.py`, sha256
`fe87a07bab7ae8ba9305e55a80c15ca80fa2d427a0214b71717e2892802b9c3b`. Log
`/Users/sasha/.local/share/roadie/r11-acceptance.log`, sha256
`6d8fef7968a80d7c5a4b001fff4eb7b695016d8cec91298f6eff7040d7ec2913`.

- Clean exited 0.
- First `checks` exited 0. The `build` lines of ruff, mypy, and pytest
  preceded `[pytest] ============================= 232 passed in 15.80s =============================`.
  Pytest's lines, including that summary, start with `[pytest] `.
- Second `checks` exited 0 and printed `reused` for ruff, mypy, and pytest.
- The dry run printed `waiting for another roadie run: .../run.lock (pid 61192, started 2026-10-31T03:05:00Z)` once, then the four `# reuse` marks, and exited 0.

**Author action required at completion.** The capture-output fix above was
committed on the author's approval (2026-10-31); CI runs on it when `main`
is pushed. ~~Push `main` and confirm CI passes on both platforms, or authorize
the executor to push. Discharges the CI clause.~~ The author pushed `main`;
CI run 41163106520 on `6b01434` passed on `ubuntu-latest` and
`macos-latest`.

**Review (2026-10-31).** R11.3's commit subject drops the backticks of its
patch name (`--jobs` for `` `--jobs` ``); the other three match. The
missing pytest output is a planning error, not an R11 defect: `Pytest` has
captured standard output since R9, so before R11 pytest printed nothing but
its `+` line either, and the acceptance clause quoting pytest's summary was
never checked against a run. The executor's fix prints captured output only
with `--jobs` above 1, so `--jobs 1`, kept for troubleshooting, shows less
than a parallel run; the same already holds for standard error since R11.4.
The author chose to show programs' output the same way in both modes, the
modes differing only in the prefix: `_cmd` now shows standard error, and
captured standard output, live with `--jobs 1` too, and still returns them.
The gate passes (232 tests), and `roadie . checks -j 1` after `--clean` shows
pytest's output unprefixed. The spec's Plans and runs concept carried the
`--jobs` default and output format; the review moves them to a new Parallel
steps section under Running a plan and retargets ten test markers.

**Reflection.** Acceptance clauses that quote output are checked against a
current run of the command while planning.

## R12: Each step says how long it took, and reuse is never stale

Status: implemented 2026-10-31 (commits R12.*)

**Improvement.** Each step that builds reports how long it took, and the
run its total, so a slow step stands out. Editing a step class defined in a build file reruns its
steps; today they are reused with stale results. A new `Env(*names)` input
reruns a step when a named environment variable changes, so a build file
declares that dependency instead of reading `os.environ` into an attribute. A
step attribute that would collide with Roadie's own (`name`, `base_dir`,
`report`, ...) fails with a message instead of being silently overwritten,
or hiding a hook, and going unfingerprinted. The rules the spec states but no
test covers get tests.

**Decisions confirmed at the checkpoint (2026-10-31).**

1. A step whose class hierarchy includes a class defined in a build file has
   that build file's bytes in its code hash, so any edit to that build file
   reruns those steps (not steps of `roadie.build` classes). Hashing only
   each class's source would miss module-level helpers the class calls, and a
   stale reuse is worse than a rerun.
2. Roadie keeps its bookkeeping names (`name`, `base_dir`, ...) public, since
   step writers use `self.base_dir` and `self.name`, and reserves them instead
   of moving them under a private attribute.

**Acceptance check.**

- `uv run roadie . checks` passes, and CI passes on `ubuntu-latest` and
  `macos-latest`.
- After `uv run roadie . --clean`, `uv run roadie . checks` prints `built ruff
  in <m:ss>`, `built mypy in <m:ss>` and `built pytest in <m:ss>`, each
  prefixed with its step's name, and ends with `finished in <m:ss>`; a second
  run prints `reused` for each and no `built` line.
- Each behaviour below is covered by a test that fails before its patch.
  Nothing outside the repo is involved, so the suite is the whole check.

### Patches

- [x] **R12.1: Print how long each step and the run took.** After a step
  that printed a `build` line finishes building, Roadie prints `built <name> in <m:ss>`, or
  `built <Class> in <m:ss>` when the step has no name: whole minutes, then
  seconds as two digits, rounded down (`0:03`, `12:45`, `75:00`). It is
  prefixed like any other line of the step with `--jobs` above 1. A
  deferred step's duration is its start. A reused step, a step that keeps no
  record and a failed step print no `built` line; the failure message is
  unchanged. The spec's Reusing a step section states it next to the
  `build` line. A run that succeeds prints `finished in <m:ss>`, unprefixed,
  as its last line, or before it serves the requested result; a failed run
  and a dry run print none. The backlog's Step durations item goes. Tests
  set the clock Roadie reads; nothing sleeps.
- [x] **R12.2: Rerun a step when its build file's class changes.** The code
  hash adds, for each class of the step's hierarchy defined in a build file,
  that build file's content keyed by its `roadie:<relative path>` module name.
  Editing the file reruns those steps; steps of imported classes are not
  affected. The spec's Step fingerprints rule loses "A class defined in a
  build file itself is not hashed", and the backlog item goes.
- [x] **R12.3: Add the `Env(*names)` input.** `Env("MODE", "TARGET")`
  fingerprints each named process environment variable's value, an unset
  variable differing from an empty one. No names fails with `Env takes at
  least one variable name`; a name that is not a string fails with `Env
  takes variable names: got <type>`. `roadie.build` exports it, the spec's
  Inputs section describes it, and the user-manual note about reading
  `os.environ` into a plain attribute points to it instead.
- [x] **R12.4: Reserve Roadie's names on a step.** An attribute a step sets
  whose name is a member of `Step` (its bookkeeping, such as `name`,
  `base_dir` and `env_values`, its hooks, such as `report`, and its class
  attributes) fails with `<step> attribute <name> is reserved by Roadie`,
  when the step is fingerprinted or shown by `--dry`. That covers an
  attribute set before Roadie names the step, which today is overwritten.
  The spec's Dependencies and attributes section states the rule, and the
  Gaps item goes.
- [x] **R12.5: Test the rules the backlog lists as untested.** One test for
  each item of the backlog's "Tests to add": an `Input` whose digest changes
  changes the fingerprint; an unknown result names the file and its results;
  `include` outside a loading build file fails; cargo reporting several
  executables fails the step; a package the workspace lacks fails; a changed
  root `Cargo.toml` or `Cargo.lock` selects every package; `HttpService`
  waits on `/` unless it sets `health_path`. The section goes. A test that
  fails reveals a defect: stop and report it rather than change the rule.

**Acceptance run (2026-10-31).** Commit `9b7c410b5411dbb64e8ec94f3e7b0c6dc73839e3`,
tree `27ba30dd8d5e542b0254345086f00db059765359`. Host Darwin 25.6.0 arm64.
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin). Python 3.13.13.
`process_cpu_count` 10, so the `--jobs` default is 10. Worktree clean.
Script `/Users/sasha/.local/share/roadie/r12-acceptance.py`, sha256
`cd8ef22e85fe871adc0753f1ce94185b33978aa9911f5bf8cd356592a26c5aa8`. Log
`/Users/sasha/.local/share/roadie/r12-acceptance.log`, sha256
`10cae23aa3e1661631671d26046ee6d63542da2e029d9951d33eea9d341bdd00`.

- `uv run ruff check`, `uv run mypy src`, and `uv run pytest -q --tb=line` on
  that commit: 259 passed.
- `uv run roadie . --clean` exited 0.
- The first `uv run roadie . checks` exited 0 and printed `[ruff] built ruff
  in 0:00`, `[mypy] built mypy in 0:00`, and `[pytest] built pytest in 0:18`,
  then an unprefixed `finished in 0:18` as the last line.
- The second `checks` exited 0 and printed `reused` for ruff, mypy, and
  pytest, and no `built` line. It ended with `finished in 0:00`.
- CI run [41179096531](https://github.com/SashaOv/roadie/actions/runs/41179096531)
  on `9b7c410` passed on `ubuntu-latest` and `macos-latest`.

**Author action required at completion.** ~~Push `main` and confirm CI passes
on both platforms, or authorize the executor to push. Discharges the CI
clause.~~ The author authorized the push. CI run 41179096531 on `9b7c410`
passed on `ubuntu-latest` and `macos-latest`.

**Review (2026-10-31).** R12.3's commit subject drops the backticks of its
patch name (`Env(*names)`), as R11.3 did; the other four match. CI run
41179096531 on `9b7c410` passed on both platforms (checked with `gh run
view`). Rerun on `9b7c410`: after `--clean`, `checks` printed `[ruff] built
ruff in 0:00`, `[mypy] built mypy in 0:00`, `[pytest] built pytest in 0:16`
and last `finished in 0:16`; the second run printed only `reused` lines and
`finished in 0:00`. No drift.

## R13: Runs take several results and parameters, build files compose, the user checks and approves, and Rust and JS projects release

Status: implemented 2026-10-31 (commits R13.*)

**Improvement.** The command line names results and their parameters, as in
`roadie version=1.2.0 package release message="Fix the lock"`; the build
file moves to `-f`. A build file declares a parameter, a mistyped or missing
one fails before any step builds, and a step reruns when its value changes.
One run builds several results. An included file stands for its default
result, so `Group(lib, app)` runs both; a setup step builds first in every
run; and a check can take another step's output. `Release` takes a message, so a release says
what it ships, and Roadie's own `release` takes it from the command line.
`Release` also writes the version of a Cargo workspace and its lockfile, and
of a `package.json`, so a project such as Niva releases its Rust and JS
packages with Roadie. A build file asks the user to check what no script
can, such as how an app looks, and records the answer, so the user is asked
again only when the step's inputs change; and it can ask the user to
approve a run, such as a release, before it goes on. `--base_dir` becomes
`--base-dir`, like every other option.

**Decisions confirmed at the checkpoint (2026-10-31).**

1. The command line is `roadie [options] [name=value]... [result
   [name=value]...]...`, as make's `make -f FILE target VAR=value`. `-f
   PATH`, `--file PATH` names the root build file, or a directory holding
   `build.py`, and defaults to `.`; `--input` would clash with Roadie's
   inputs. A word with `=` is a parameter, any other word a result.
2. A parameter before every result is run-wide: each step in the plan that
   takes a `Param` of that name gets the value. A parameter after a result
   is that result step's own and overrides a run-wide value for it; it does
   not reach the result's dependencies (passing it down may come later, at
   a cost in clarity). A value belongs to the step, not to the `Param`
   object, and each `Param` keeps its own default.
3. A run builds several results, the union of their plans, each shared step
   once. A kind that can be requested only once per run, today `Service`
   because a served result keeps the terminal, says so on its class, as
   `reusable` and `deferred` do.
4. The message goes into the release commit's body, under the unchanged
   subject `v<X.Y.Z> release`, and into an annotated tag `v<X.Y.Z>`, so a
   release that changes no file still carries it. Without a message the tag
   stays lightweight, as today.
5. `--base_dir` stays accepted for good, not for one release: cyclopts
   matches it to `--base-dir` by itself (Facts), and `--help` shows only
   `--base-dir`.
6. Both Cargo and `package.json` release now; Niva's diverging
   `package.json` versions are an oversight to fix in Niva.
7. A release names its version sources by path, and the file name says the
   kind (`pyproject.toml`, `Cargo.toml`, `package.json`); `PyProject` goes.
8. A user step asks alone: it waits until no other step is running, and
   no step starts while it asks, so prompts never interleave with prefixed
   output, at the cost of a serial stretch in the run. A service that is not
   a requested result starts with standard input from the null device, so
   it does not compete with the user's answers. The author accepts this
   for now; its other implications are hard to predict.
9. A step `include` returns has the file's results as attributes, so a
   result named like a `Step` member (`build`, `report`, ...) would hide it;
   including such a file fails rather than renaming anything (R13.3).
10. `UserTask` takes steps through `inputs`, as `Check` does after R13.5,
    rather than a separate `requires`; `UserApproval` takes only its
    question, and a `Release` requires it.
11. The interactive kinds are `UserTask` and `UserApproval`, the backlog's
    `AuthorTask` and `AuthorApproval`, after common practice.

**Acceptance check.**

- `uv run roadie checks` passes, and CI, which runs it, passes on
  `ubuntu-latest` and `macos-latest`.
- `uv run roadie --help` lists `--file`, `-f` and `--base-dir`, and not
  `--base_dir`; `uv run roadie --dry --base_dir .` still exits 0.
- `uv run roadie . checks --dry` fails with the hint to pass `-f .`;
  `uv run roadie -f build.py ruff mypy --dry` exits 0 and marks both.
- `uv run roadie release message=x --dry` exits 0 and shows
  `message=Param('message', default=None)` on `release`'s line;
  `uv run roadie release mesage=x --dry` fails with `release takes no
  parameter mesage; it takes: message`; `uv run roadie message=x --dry`
  fails with `unknown parameter message; the plan takes no parameters`.
- Each behaviour below is covered by a test that fails before its patch.
  The `Release` tests release to a local bare upstream with real git and,
  for `Cargo.toml`, real cargo, as the existing `Release` and Cargo tests do.
  No real release is made: a pushed tag publishes to PyPI. The user-step
  tests answer through a pseudo-terminal (`pty`), as a user at a
  terminal would.

### Patches

- [x] **R13.1: Name the build file with `-f` and run several results.**
  `-f PATH`, `--file PATH` replaces the `directory` word: a file is the
  root build file, a directory means its `build.py`, and the default is
  `.`; a path that does not exist fails with `build file does not exist:
  <path>`. Every word is a result; the run builds each, in the order given,
  over the union of their plans, a step shared by two plans once, and with
  no word it runs the default result as today. A word naming no result that
  is an existing directory fails with `<path> has no result named '<word>';
  to use the build file in <word>, pass -f <word>`. A step class sets
  `once_per_run = True` when it may be requested once per run; `Service`
  sets it, and requesting two such results fails with `<a> and <b> can each
  be requested only once per run; request one of them`. `--base_dir`
  becomes `--base-dir`; a test shows `--base_dir` still sets it. The spec's
  Command line, Settings and Requested result sections, the README, CI's
  `roadie . checks`, and the tests change with it. The backlog's rename item
  goes.
- [x] **R13.2: Pass parameters to results.** `Param(name, default=...)` is
  an input: `name` must be an identifier (`Param takes an identifier: got
  <repr>`); `default`, when given, is a string or `None` (`Param default
  takes a string or None: got <type>`), and without it the parameter is
  required. A word containing `=` is a parameter: its name is the text
  before the first `=`, its value the rest. A name that is not an
  identifier fails with `parameter name must be an identifier: <word>`.
  Before every result, a parameter is run-wide; after a result, it is that
  result step's own and overrides a run-wide value for that step only. A
  step's value of a `Param` is its own parameter, else the run-wide one,
  else that `Param`'s default. Before any step builds, also under `--dry`:
  a run-wide name no step in the plan takes fails with `unknown parameter
  <name>; the plan takes: <names>`, or `the plan takes no parameters`; a
  result's own name it does not take fails with `<result> takes no
  parameter <name>; it takes: <names>`, or `<result> takes no parameters`;
  one name given twice in the same place fails with `parameter <name> is
  given twice`; a required parameter a step gets no value for fails with
  `<step> needs parameter <name>`. Inside `build`, `self.<attr>.value` is
  that step's value; it lasts for the run, and outside a run fails with
  `parameter <name> has no value outside a run`. A step's fingerprint covers
  its own values, an unset optional parameter differing from an empty one.
  `roadie.build` exports `Param`; the spec's Command line shows the
  parameters, and a Parameters section under Inputs describes `Param`. The
  backlog item goes.
- [x] **R13.3: Return a step from `include`.** `include(directory)` returns
  a step that stands for the included file's default result, as a `Group`
  of that one step, and has the file's results as attributes (`lib.test`),
  which are not its dependencies; so `Group(lib, app)` runs each file's
  default, and `roadie lib` runs `lib`'s. A plan holding it fails, before
  any step builds, with `<path> marks no default result` when the file has
  no `default_step` mark. Including a file that binds a result named like a
  `Step` member (R12.4) fails with `<path>: result <name> is reserved by
  Roadie in an included file`. The spec's Including build files section
  states it, and the backlog item goes.
- [x] **R13.4: Build a setup step first.** `setup(step)` returns `step` and
  marks it, like `default_step`: every run builds it, after its own plan
  and before any other step, whichever results it runs, also under `--dry`.
  A build file marks at most one, bound to a variable (`<path>: the setup
  step is not bound to a variable`); a second fails with `<path> marks more
  than one setup step: <names>`; `setup` outside a loading build file fails
  with `setup() works only in a build.py that Roadie loads`. A mark in an
  included file counts, and a file's setup step builds after those of the
  files it includes. A failed setup step stops the run before any other
  step builds. Reuse is unchanged. `roadie.build` exports it; the spec
  states it beside `default_step`, and the backlog item goes.
- [x] **R13.5: Let a check take a step in `inputs`.** `Check` and `Pytest`
  take inputs and steps in `inputs`, in any mix: a step there is a
  dependency, and its output fingerprint is part of the check's
  fingerprint. Another kind fails with `<Kind> inputs takes inputs or
  steps: got <type>`. The docstrings state it, and the backlog item goes.
- [x] **R13.6: Release with a message.** `Release(..., message=...)` takes a
  string, a `Param` or `None` (the default); another kind fails with
  `Release message takes a string or a Param: got <type>`. A message that is
  not empty becomes the body of the release commit, under `v<X.Y.Z>
  release`, and the message of an annotated tag `v<X.Y.Z>`; otherwise the
  release is as today. Roadie's `build.py` passes `message=Param("message",
  default=None)`. The `Release` docstring states it, and the backlog item
  goes.
- [x] **R13.7: Name a release's version sources by their files.**
  `Release(primary, *secondary, ...)` takes paths, relative to the step's
  base directory, and the file name says the kind: `pyproject.toml` is a
  Python project, read and written as `PyProject` is today. Any other name
  fails with `Release takes pyproject.toml files: got <path>`, a list that
  R13.8 and R13.9 extend; a value that is not a path fails with `Release
  takes paths: got <type>`. `PyProject` leaves `roadie.build`; Roadie's
  `build.py` becomes `Release("pyproject.toml", ...)`, and `--dry` shows
  the paths.
- [x] **R13.8: Release a Cargo workspace's version.** A source named
  `Cargo.toml` is a Cargo manifest. Its version is
  `[workspace.package] version`, or, without that table, `[package]
  version`; a manifest with neither as a string fails with `<path> has no
  static Cargo version`. Writing replaces the version string on that table's
  `version` line and leaves the rest of the file as it is. When git tracks
  the workspace's `Cargo.lock` (from `cargo locate-project --workspace`),
  writing then runs `cargo update --workspace --offline`, and the lockfile
  joins the release commit; a failing cargo fails with `cannot release:
  <cargo's message>`.
- [x] **R13.9: Release a `package.json` version.** A source named
  `package.json` is a JS package. Its version is the top-level `version`
  string; a file without one fails with `<path> has no version`. Writing
  replaces that string and leaves the rest of the file as it is. pnpm's
  lockfile does not record a package's own version (Facts), so no lockfile
  is touched; when git tracks a `package-lock.json` beside the file, the
  release fails before changing anything with `cannot release: <path> has a
  package-lock.json, which Roadie does not update`. The backlog item goes.
- [x] **R13.10: Ask the user to run checks.** `UserTask(instructions,
  *checks, inputs=())` is a `Tests` step: `instructions` is a
  string, each check a string (`UserTask takes checks as strings: got
  <type>`), at least one (`UserTask takes at least one check`); `inputs`
  takes inputs and steps, as `Check`'s does after R13.5. Building
  prints the instructions, then asks each check in turn, `<check>? [y/n] `;
  `y`/`yes` passes it, and `n`/`no` asks `What failed? ` for a reason that
  is not empty, which becomes the check's failure; any other answer asks
  again. A failed check fails the step as any `Tests` failure does, so it
  records nothing; when every check passes, the record keeps the step, and
  it is reused, without asking, until its fingerprint changes. Standard
  input that is not a terminal fails the step with `<name> needs the user
  at a terminal`, and an end of input with `<name>: no answer from the
  user`. A user step asks only while no other step is running, and no
  step starts while it asks, so its questions are the terminal's last lines
  and its answers reach it alone; for the same reason a service that is not
  a requested result starts with standard input from the null device.
  `--dry` asks nothing. `roadie.build` exports it, and the backlog item
  goes.
- [x] **R13.11: Ask the user to approve a run.** `UserApproval(question)`
  is a user step whose question is asked once,
  `<question> [y/n] `, under R13.10's rules for answers and the terminal.
  `y` continues the run; `n` fails the step with `cancelled by the user`,
  so the run stops as on any failure. It is never reused and keeps no
  record, so it asks on every run, and its output fingerprint is its own
  fingerprint. Roadie's `build.py` makes `release` require
  `UserApproval("Release main?")`. `roadie.build` exports it, and the
  backlog item goes.

**Acceptance run (2026-10-31).** Commit `bebee9ee4f243857d809c71fc4a516ae8f370081`,
tree `f93771ef82e0f836effa89972a8feed8ae3952fa`. Host Darwin 25.6.0 arm64,
uv 0.12.23, Python 3.13.13. Script
`/Users/sasha/.local/share/roadie/r13-acceptance.py`, sha256
`ce15aadf9ea4176ee7272a4982b97200dade6b2f63353bb15e7ca4ae69d5c669`. Log
`/Users/sasha/.local/share/roadie/r13-acceptance.log`, sha256
`7b435f4851db708a9126d6658598d89692de35e34f0fdb1c20ec056cb11e8de1`.

- `uv run ruff check`, `uv run mypy src`, and `uv run pytest -q --tb=line` on
  that commit: 306 passed.
- `uv run roadie checks` exited 0.
- `uv run roadie --help` lists `--file`, `-f`, and `--base-dir`, and not
  `--base_dir`. `uv run roadie --dry --base_dir .` exited 0.
- `uv run roadie . checks --dry` failed with `pass -f .`.
  `uv run roadie -f build.py ruff mypy --dry` exited 0 and marked both.
- `uv run roadie release message=x --dry` exited 0 and showed
  `message=Param('message', default=None)` on `release`'s line.
  `uv run roadie release mesage=x --dry` failed with `release takes no
  parameter mesage; it takes: message`. `uv run roadie message=x --dry`
  failed with `unknown parameter message; the plan takes no parameters`.
- CI run [41282856686](https://github.com/SashaOv/roadie/actions/runs/41282856686)
  on `bebee9e` passed on `ubuntu-latest` and `macos-latest`. The earlier run
  on `f791383` failed on Ubuntu: closing the pty master before the child
  read made standard input not a terminal. `bebee9e` sends end of input
  after the prompt, while the terminal is still open.

**Author action required at completion.** ~~Push `main` and confirm CI passes
on both platforms, or authorize the executor to push. Discharges the CI
clause.~~ The author authorized the push. CI run 41282856686 on `bebee9e`
passed on `ubuntu-latest` and `macos-latest`.

**Review (2026-11-01).** CI run 41282856686 on `bebee9e` passed on both
platforms (checked with `gh run view`), and every acceptance command above,
rerun on `bebee9e`, gave the recorded result. Four subjects drop the
backticks of their patch names (R13.1, R13.3, R13.5, R13.9), as R11.3 and
R12.3 did; the rest match. Two commits are not patches: `2895004` (the
author's, between R13.3 and R13.4) lets `include` take a build file as well
as a directory, and the spec states it; `bebee9e` fixes the user-task test
that failed on Ubuntu CI by sending end of input while the pseudo-terminal
is still open, and is not tagged `R13:`.

**Reflection.** Patch names are written without code formatting, so a
commit subject can equal its patch name literally; three builds in a row
dropped the backticks.

## R14: Roadie runs on Windows

Status: implemented 2026-11-01 (commits R14.*)

**Improvement.** Roadie's checks pass on Windows, and CI proves it on every
push. A second run on Windows names the run it waits for, and a step that runs
Roadie in its own project fails instead of waiting forever. Stopping a server
stops its whole process tree, as on Linux and macOS. Ctrl-C ends a run with
several jobs at once, instead of waiting for its running steps. A command that cannot
start names itself on every platform. A release keeps a manifest's line
endings. Platform differences live in one package, `roadie.system`, and ruff
fails any platform check outside it, so they stay there. The tests no longer
assume POSIX paths, permissions and terminals. Facts, 2026-11-01, list each
failure this build removes.

**Decisions confirmed at the checkpoint (2026-11-01).**

1. `roadie.system` holds every platform difference and imports nothing from
   Roadie. It offers operations (lock a file, start a process in its own
   group, stop a process tree, `executable_suffix()`) and, when a caller
   needs one, capabilities, never the platform's name. Ruff's `banned-api` rule
   (TID251) bans `sys.platform`, `os.name`, `platform.system`, `msvcrt`,
   `fcntl`, `termios`, `pty`, `winreg`, `os.killpg`, `os.getpgid`,
   `os.setsid` and `signal.SIGKILL` everywhere except `src/roadie/system/`
   and `tests/support.py`. Ruff cannot ban an argument such as
   `start_new_session=`; it is used in `roadie.system` alone.
2. The run lock locks one byte at offset 2³⁰, past any token, on every
   platform, so the holder's PID stays readable while it is locked. One
   offset keeps one code path; `flock` ignores it.
3. A command that cannot start fails with `<step> failed: cannot run
   <program>: <reason>`, where `<reason>` is the error's text without its
   filename. Today POSIX says `[Errno 2] No such file or directory:
   '<program>'` and Windows names nothing.
4. `--jobs` above 1 runs steps at once on Windows too. The author's
   2026-11-01 decision to run one step at a time there assumed parallel steps
   did not work; they do (Facts, 2026-11-01), except that Ctrl-C waits for
   every running step to finish. The main thread waits with a timed join on
   every platform, which Ctrl-C interrupts.
5. Stopping a server on Windows runs `taskkill /T /PID <pid>`, then
   `taskkill /T /F /PID <pid>` after 10 seconds, as POSIX sends SIGTERM and
   then SIGKILL. If `taskkill` cannot run, Roadie kills the process alone.
6. A test that needs a pseudo-terminal, `chmod(0)` or an unprivileged
   symlink is skipped on Windows, with the reason, when no Windows equivalent
   exists. The rules those tests cover still need a test that runs on Windows.
7. A generated file counts as unchanged when git would store the same
   content for it: Roadie compares `git hash-object --path=<destination>`
   of the destination and of the built file, which applies the
   destination's line-ending rules. With Git for Windows' default
   `core.autocrlf=true`, a tracked LF file checks out as CRLF, so today's
   byte comparison calls it stale under `--locked` and rewrites it on every
   other run. An untracked destination keeps the byte comparison.

**Acceptance check.**

- On a Windows machine, `uv run ruff check`, `uv run mypy src build.py` and
  `uv run pytest` pass, the last within the time it takes on macOS and with
  no hang; `uv run roadie checks` passes.
- CI passes on `ubuntu-latest`, `macos-latest` and `windows-latest`.
- Adding `if sys.platform == "win32": pass` to `src/roadie/engine/run.py`
  makes `uv run ruff check` fail with TID251; the same line in
  `src/roadie/system/` passes.
- On Windows, with `--jobs 1` and with `--jobs 2`, a dev server built from a
  `cmd /c` wrapper that starts a child leaves neither process running after
  Ctrl-C (checked with `tasklist`), and Ctrl-C during `uv run roadie checks`
  ends the run within a second.
- Each behaviour change below is covered by a test that fails before its
  patch on the platform it concerns.

### Patches

- [x] **R14.1: Move platform code into roadie.system and ban it elsewhere.**
  As decision 1, with no change in behaviour. The OS calls of
  `engine/lock.py` and `engine/process.py` move to `roadie.system`, whose
  branches test `sys.platform`, so mypy checks each platform's branch on that
  platform and `mypy src build.py` passes on all three; the engine keeps its
  rules (the lock token, the waiting message, when to stop a server).
  `build/rust.py` takes `executable_suffix()`. In the tests, the `os.name`
  skips become markers in `tests/support.py` (`posix_only`, `needs_pty`,
  `needs_symlinks`), and the lock-holder script in `test_lock.py` locks
  through `roadie.system`. The ruff rule turns on in `pyproject.toml`. The
  spec's Packages section already states the rule.
- [x] **R14.2: Keep the lock holder's PID readable on Windows.** As decision
  2, in `roadie.system`. The waiter prints `(pid <pid>, started <time>)`,
  and a step that runs Roadie in its own project fails as the spec says. The
  four `test_lock.py` failures pass.
- [x] **R14.3: Name the command that cannot start.** As decision 3. The spec's
  Step helpers section states the message; `test_check.py` asserts it
  without the POSIX-only text.
- [x] **R14.4: Keep a manifest's line endings when releasing.** `Release`
  rewrites `Cargo.toml` and `package.json` with the file's own line endings
  (`newline=""`), so a release on Windows does not turn an LF file into
  CRLF. A test releases a CRLF and an LF manifest and checks each keeps its
  endings. Not platform code: the same call is right everywhere.
- [x] **R14.5: Compare a tracked generated file as git stores it.** As
  decision 7. A test sets `core.autocrlf=true` in the test repository,
  checks out an LF generated file as CRLF, and expects `--locked` to pass
  and no `updated` line. The spec's Generated files section states it. Not
  platform code: `core.autocrlf` can be set anywhere.
- [x] **R14.6: Let Ctrl-C interrupt a run with several jobs.** As decision
  4. The main thread waits for its workers with `join(0.1)` in a loop. The
  interrupt test raises `KeyboardInterrupt` in the main thread with
  `_thread.interrupt_main()` instead of sending SIGTERM, so it runs on every
  platform, with `--jobs` 1 and 2; it also checks that the step's program
  is stopped. Not platform code: the loop is right everywhere.
- [x] **R14.7: Stop a server's process tree on Windows.** As decision 5, in
  `roadie.system`, replacing the Windows branch that signals the process
  alone and the `SIGKILL` Windows lacks. The spec's Step helpers section
  drops "unverified" and states the rule. A test starts a server whose
  wrapper starts a child and checks both are gone.
- [x] **R14.8: Make the tests platform-neutral.** Escape paths in
  `pytest.raises(match=...)` (`re.escape`); build cargo JSON with
  `json.dumps`; write test files with explicit `newline="\n"` (the
  `TextFile` helper); compare a copied executable's mode only on POSIX; mark
  the cases of decision 6 with the markers of R14.1. The 15 test-assumption
  failures in Facts pass or skip, and each skipped rule keeps a test that
  runs.
- [x] **R14.9: Add a Windows CI job.** `windows-latest` joins the matrix. The
  backlog's Windows item and the spec's "unverified until the Windows CI
  job" go.
- [x] **R14.10: Force a console program's stop at once on Windows.** Added
  in review (author decision, 2026-11-01), amending decision 5: `taskkill`
  without `/F` cannot close a console program ("can only be terminated
  forcefully"), so every stop of a running server waited the full 10
  seconds. When the first `taskkill` fails, `/F` follows at once. The
  tree-stop test bounds the stop at 5 seconds.

**Acceptance run (2026-11-01).** Commit `82269080ffcbf0e4aff5cc9c0b2154bd8a25d873`,
tree `bbf3bac2fed8f4dda7dfb8804dc14d077d5126a2`. Host Windows-11-10.0.26200-SP0
AMD64, uv 0.11.26, Python 3.14.3. Script
`C:\Users\sasha\.local\share\roadie\r14-acceptance.py`, sha256
`54da32d37a70e8812f0273a962cbda9ee0a82ec49a71722d4c9e098392f84e3b`. Log
`C:\Users\sasha\.local\share\roadie\r14-acceptance.log`, sha256
`c2df7ff400fb7273a279fc56fc67f8ea2030850ffae7dcdc2398ccd166f95659`.

- `uv run ruff check`, `uv run mypy src build.py`, and
  `uv run pytest -q --tb=line` on that commit: 294 passed, 16 skipped.
- `uv run roadie checks` exited 0 (pytest reused in 4:31).
- Adding `if sys.platform == "win32": pass` to `src/roadie/engine/run.py`
  made `uv run ruff check` fail with TID251; the same line in
  `src/roadie/system/` passed.
- The interrupt tests under R14.6 cover Ctrl-C ending a run with `--jobs` 1
  and 2 and stopping the step's program; the live Ctrl-C / `tasklist` clause
  for a `cmd /c` wrapper was not observed in this run.

**CI (2026-11-01).** `main` at R14.9 is pushed; CI run 41349688275 passed on
`ubuntu-latest`, `macos-latest` and `windows-latest`. Discharges the CI
clause.

**Author action required at completion.** The author runs the Ctrl-C
procedure below in a real Windows console (Windows Terminal or PowerShell,
not through an agent, which has no console to send Ctrl-C to) and reports
each result. Discharges the live Ctrl-C clause.

The probe project is `~/.local/share/roadie/r14-ctrlc` (a git repository so
it has a root): `build.py` defines `dev`, a `Service` that starts
`cmd /c <venv python> server.py 8765 pids.txt`. That is three processes:
`cmd.exe`, the venv's `python.exe` launcher and the real interpreter running
the server. `server.py` records the last two in `pids.txt`, and `build.py`
records `cmd.exe`. `check.py` asks `tasklist` about each PID and exits 1 if
any is still running. A dry start, stopped with `taskkill`, showed all three
running while up and gone afterwards.

From the Roadie checkout, with `$probe = "$HOME\.local\share\roadie\r14-ctrlc"`:

1. `uv run roadie -f $probe --jobs 1 dev`. Wait for `dev is up:
   http://127.0.0.1:8765 (Ctrl-C to stop)`, then press Ctrl-C. Pass: the
   prompt returns, and `uv run python $probe\check.py` prints `gone` for
   `cmd.exe`, `parent` and `server` (exit 0).
2. The same with `--jobs 2` (lines come prefixed with `[dev] `).
3. `uv run roadie --clean`, then `uv run roadie checks`. When
   `build pytest` appears, press Ctrl-C. Pass: the prompt returns within
   about a second, and
   `Get-CimInstance Win32_Process | ? CommandLine -match 'pytest|ruff check|mypy src' | select ProcessId, CommandLine`
   prints nothing. Afterwards rerun `uv run roadie checks` to rebuild the
   cleared state.

Any failure: paste the console output and the `check.py` lines; it reopens
R14 with a fix patch.

**Ctrl-C result (2026-11-01).** The author ran steps 1–3; all passed, and
step 3's prompt returned at once. Discharges the live Ctrl-C clause. The
probe's server exits on Ctrl-C by itself, which hid a gap: on Windows a
requested service's untimed `wait()` sees Ctrl-C only when the process exits,
so a server that outlives Ctrl-C hung the run. Fixed in review by waiting in
slices, as `_cmd` already did.

**Final CI (2026-11-02).** CI run 41391184321 on `68ec836`, which includes
the review fix and R14.10, passed on `ubuntu-latest`, `macos-latest` and
`windows-latest`.

## R15: A step builds a directory, and a changed directory is never replayed

Status: implemented 2026-11-02 (commits R15.*)

**Improvement.** A step can build a directory, such as a bundler's output or a
generated site, and Roadie treats that directory as one value. Roadie records
every file under it and reuses the step only while the directory still holds
that content. A deleted, added or edited file builds the step again, and a
consumer reruns only when the content it reads changes; a rebuild with identical
files leaves its consumers reusable. A directory step can publish into the
source tree as a file step does with `output`, and under `--locked` a committed
directory that would change fails with the paths that differ. A `Files`
directory that git ignores fails with its path instead of fingerprinting
nothing, so a stale build output is never reused.

Today a bundler's output can only be a `Check`, whose record survives the
output's deletion, or a `Files` directory, which is reused stale when git
ignores it.

Current state (2026-11-01, `bdbf30d`):

- `PathStep` (`build/path.py`) records one file's path and SHA-256. No step kind
  has a directory value. `File` publishes with `output` and compares a tracked
  destination with `git hash-object` (`build/file.py`).
- `Files` (`build/files.py`) lists a directory with `git ls-files --cached
  --others --exclude-standard` in a work tree and walks it otherwise. Verified
  2026-11-01 in a temporary repository: `git ls-files` lists nothing under an
  ignored directory, so that fingerprint is the same whatever the directory
  holds. A named file inside it is hashed directly.
- Facts (2026-10-29) record a `Files` directory with its permissions removed
  building without error. The spec's Files section says an unreadable directory
  fails with `cannot read <path>: <reason>`.

Decided at the checkpoint (2026-11-01):

1. The kind is `Directory`, after `File` and `Binary`. Its value is the path of
   the directory it built. Its record lists every regular file under the
   directory, ignored or not, by path relative to the directory and SHA-256.
   Empty directories do not count, as in `Files`. Symbolic links are not
   followed; see R15.2.
2. Its output fingerprint is a SHA-256 of that record, so a rebuild with
   identical content leaves its consumers reusable.
3. `Directory(output=...)` publishes the built directory to `output`, resolved
   from the base directory, under the rules `File` follows. Each tracked file is
   compared as `File` compares it, so `core.autocrlf` does not make it stale.
   Untracked files are written. Under `--locked`, a tracked file that would
   change fails the step with `generated directory is stale: <destination>`
   and one line per changed path.
4. Publishing removes a tracked file in the destination that the build no
   longer produces; under `--locked` it fails the step as stale instead. An
   untracked file in the destination is left alone, because it is not Roadie's
   to delete.
5. A `Files` directory that git ignores fails when fingerprinted, with
   `Files(<path>): <path> is ignored by git; take the step that builds it`. A
   named file is still hashed whether or not git ignores it, and ignored files
   under a listed directory git does not ignore stay excluded, as now.

Not in this build, and why:

- `Dir(path)` input (git tree id): it needs a decision on untracked files, which
  git's tree id leaves out and `Files` includes. It stays in the backlog.
- `Image(tag)` output: its acceptance needs a container runtime on every
  platform, which this build does not provide.
- `Log(path)` output: `File` already records a written file by content, so the
  kind would duplicate it. Removed from the backlog on 2026-11-01.
- `Toolchain`, `NodeEnv`, `Host(label)`, `GitCommit`: separate kinds, each a
  different input, not part of this change.
- Enforcing declared inputs: a design of its own, with a file-access tracer.

Verified before the checkpoint:

- 2026-11-01, `git ls-files` under an ignored directory: lists nothing (above).
- 2026-11-01, symbolic-link creation on the `windows-latest` runner: unconfirmed.
  A web search found no GitHub-specific evidence. Windows allows `os.symlink`
  only with the `SeCreateSymbolicLinkPrivilege` privilege or Developer Mode, and
  the runner's grant is unknown. The first CI run on Windows decides; the rule
  in the acceptance check says what follows.

**Acceptance check.**

- `uv run roadie checks` passes, and CI passes on `ubuntu-latest`,
  `macos-latest` and `windows-latest`.
- The scenario in R15.5 runs the real `roadie` command as a subprocess in a
  temporary git project, outside the repo, and asserts on its output. It shows:
  a first run builds the directory step and a second run builds nothing; deleting
  or editing one output file builds it again; a rebuild with identical files
  leaves a consumer reused; `Files('dist')` with `dist` ignored fails and names
  `dist`; a stale tracked file under `--locked` fails and names the path, and an
  untracked file is written.
- Each behavior is covered by a test that fails before its patch.
- Link tests (R15.2) run on all three platforms. If the first Windows CI run
  shows the runner cannot create symbolic links, the executor skips those tests
  on Windows only. Each skip carries a reason naming the missing privilege, and
  the executor reports the skip and its CI run to the author before the build is
  marked implemented. Linux and macOS never skip them. This skip is the one
  accepted exception to the CI clause above.
- The scenario uses no live or external service, so the suite and the CLI run
  are the whole check.

**Author action required at completion.** Push `main` and confirm CI passes on
all three platforms, or authorize the executor to push. Discharges the CI
clause.

### Patches

- [x] **R15.1: Add the Directory step kind.** A `Directory` step's value is the
  path of the directory it built, and its record is the path and the relative
  path and SHA-256 of every file under it. It is reused only while the directory
  holds exactly that content. A build that returns a path that is not a
  directory fails with `<step> built <path>, which is not a directory`. Move the
  recursive walk out of `files.py` into `path.py` so `Files` and `Directory`
  share it. `roadie.build` exports `Directory`, its docstring specifies the kind,
  the spec's Kinds of step names it, and the backlog's `Dir(path)` output item
  goes. Tests cover reuse, a deleted, added or edited file rebuilding, a rebuild
  with identical content keeping the output fingerprint, and the not-a-directory
  failure.
- [x] **R15.2: Record links by target, and fail on an unreadable entry.** A
  symbolic link is recorded by its target text and not followed, so a link to a
  directory is not descended and a retargeted link changes the record. A file
  the walk cannot read fails with `cannot read <path>: <reason>`, the same
  wording as `Files`, and the `Files` case in Facts gets the same rule. The
  spec's Files and Directory sections state both. Tests use a monkeypatched read
  error, not file permissions, so they hold on Windows. Link tests follow the
  Windows rule in the acceptance check.
- [x] **R15.3: Fail a Files directory that git ignores.** A `Files` directory
  inside a git work tree that git ignores fails when fingerprinted, with
  `Files(<path>): <path> is ignored by git; take the step that builds it`. The
  spec's Files input section states it. Tests cover an ignored directory (fails),
  an ignored named file (hashed, as now), and an ignored file under a listed
  directory (excluded, as now).
- [x] **R15.4: Publish a Directory output into the source tree.**
  `Directory(output=...)` follows the rules in item 3 above. Factor the tracked
  comparison and `git hash-object` helpers out of `file.py` so `File` and
  `Directory` share one implementation (DRY). Tests cover: a new destination is
  written; an identical destination is not rewritten; under `--locked`, a
  tracked changed file fails and names its path, and no destination file is
  written; an untracked file is written under `--locked`; a file outside git is
  written; a reused step does not publish; the failed step releases its built
  directory, as `File` does. A tracked file that the build no longer produces is
  removed, and an untracked one is kept, as item 4 states.
- [x] **R15.5: Run the directory scenario through the CLI.** Add the scenario from
  the acceptance check as a test that runs `roadie` as a subprocess in a
  temporary git project. It covers each clause the acceptance check lists and
  fails if a clause does not hold.

**Acceptance run (2026-11-02).** Commit `facf035bc1519d82de5229df2a675d18bc4264aa`,
tree `98925b2fc93d59dfb78653c83152a26c3250a049`. Host Darwin 25.6.0 arm64,
uv 0.12.23, Python 3.13.13. Script
`/Users/sasha/.local/share/roadie/r15-acceptance.py`, sha256
`b70469d7226d36255eb59d232cb62698ef83542e2938f01666b85e10a7e89dbc`. Log
`/Users/sasha/.local/share/roadie/r15-acceptance.log`, sha256
`6a52c9cfbb24d6ff0d1f3e9fc3e5b05dbf595625847c0933f89925a7b62cec61`.
Command `uv run roadie checks`. Verdict: pass, with the CI clause open.

- `uv run ruff check` and `uv run mypy src build.py` passed. `uv run pytest
  -q --tb=line`: 337 passed in 24.35s, none skipped.
- `uv run roadie checks` exited 0. It reused ruff, mypy, pytest, and checks,
  finishing in 0:00; those records match this tree. The direct pytest run
  above is the observation of the suite.
- The scenario test passed in that pytest run. It shows a first run building
  the directory step and a second run building nothing; deleting or editing
  one output file building it again; a rebuild with identical files leaving
  the consumer reused; `Files('dist')` with `dist` ignored failing and naming
  `dist`; a stale tracked file under `--locked` failing and naming the path;
  and an untracked file being written.
- Each of those behaviors has a test in the suite that passed. The run that
  showed each test failing before its patch was that patch's verification,
  not this run.
- Link tests ran on this Darwin host, and nothing was skipped. They were not
  run on Ubuntu or Windows. No Windows skip was added. Windows symbolic-link
  verification waits for the author to push.
- The scenario uses no live or external service. That clause has nothing
  further to observe.

CI on `ubuntu-latest`, `macos-latest`, and `windows-latest` is open. The
author action above discharges it, and that push is also the Windows and
Ubuntu link-test run.

**Final CI (2026-11-02).** CI run 41556262112 on `29b7114` passed on
`ubuntu-latest`, `macos-latest` and `windows-latest`. The link tests ran on all
three; no Windows skip was needed.

## R16: Every step has a name, and Roadie's output says what matters

Status: planned, approved 2026-11-08

**Improvement.** A step that no variable holds, such as `Copy(Emitter())`'s
emitter or each member of `Group(*(Check(...) for package in PACKAGES))`, takes
a name derived from the variable that reaches it: `copy.source`, `checks[2]`.
Roadie records and reuses it under that name, so a change in one package reruns
that package's step alone, and output, `--dry` and failures name it instead of
its class. The daily sweep deletes exactly the artifacts no record lists, so it
no longer deletes a recorded step's `.d.ts` file (Facts, 2026-11-02). Ctrl-C and
SIGTERM print `interrupted: stopping <names>`, naming what they stop. A
program whose output a step captures as data, such as `cargo
--message-format=json` under `RustBinary` or a `gh ... --json` query, no longer
floods the console. `File` and `Directory` take `copy_to` instead of `output`,
which says what the parameter does: copy the built value into the source tree.

Today an unnamed step is never reused (`engine/run.py`, `_reusable` requires a
variable), prints its class name, and keeps its artifacts under
`<Class>-<serial>`, which the next sweep deletes. `_cmd(capture_output=True)`
prints standard output as it arrives and also returns it, so every `RustBinary`
build prints each cargo JSON message, and Niva's `OpenApi` prints its whole
OpenAPI document.

**Decisions for the checkpoint.**

1. A derived name is the variable followed by the path to the step, read from
   the arguments `--dry` shows: positional argument `i` adds `[i]`, keyword
   argument `k` adds `.k`, and element `i` of a tuple keyword argument adds
   `.k[i]`. Nested steps add a segment per level (`copy.source.source`). A
   dependency that the arguments do not show is reached by attribute name the
   same way. The derived name of a step in an included file takes that file's
   `<directory>:` prefix, as its variables do. A variable is an identifier, so
   a derived name never equals one.
2. A step reachable from several variables takes its name from the first one
   the build file binds, walking depth-first in argument order. Loading
   finishes an included file first, so its steps keep its names.
3. A derived step's record and artifacts live with the build file whose
   variable names it. Entries for derived names count as the file's own when
   the record drops entries the file no longer defines.
4. Every step in a plan is reachable from a variable, so it has a name. The
   spec's "or its class name when it has none" clauses, and the
   `<ClassName>-<serial>` artifact stem, go. One `Step` helper gives a
   step's label for the code paths that still meet a step outside a loaded
   build file (unit tests).
5. `run.artifact` notes each file name it returns, and the record entry keeps
   those names. The sweep deletes a file in a record directory unless an entry
   lists it. Record format version 2 replaces version 1, so every step builds
   once after the upgrade, as the Records section states for a record of
   another version.
6. On Ctrl-C or SIGTERM during a run or while a result is served, Roadie prints
   `interrupted: stopping <names>` once, unprefixed, before stopping anything.
   `<names>` are the running steps in plan order, then the live services not
   among them. With none, it prints `interrupted`. The exit status stays 130.
7. `_cmd(capture_output=True)` returns standard output and does not print it.
   `show_output=True` also prints it as it arrives, prefixed as now; `Check`
   and `Pytest` pass both, since their program's output is what the user
   reads. Standard error is shown as it arrives either way, and a failure
   still quotes its last 2000 characters.
8. `File(output=...)` and `Directory(output=...)` become `copy_to=...`, with
   no alias: `output=` fails as an unknown argument. The attribute's name is
   part of the fingerprint, so those steps would rebuild once; the record
   format change of decision 5 rebuilds every step once anyway. Niva passes
   `output=` five times (`jammer/build.py`, lines 47–52) and changes them when
   it upgrades.

Not in this build: the project-root override, the nextest step, the input kinds
enforcement of declared inputs, and the `Release` check for unlisted version
files, which needs its own decisions (backlog). Niva's `cargo_check` stays as
it is; splitting it per package is Niva's change once it upgrades.

**Acceptance check.**

- `uv run roadie checks` passes, and CI passes on `ubuntu-latest`,
  `macos-latest` and `windows-latest`.
- The scenario in R16.7 runs the real `roadie` command as a subprocess in a
  temporary git project and asserts on its output. The build file binds
  `copy = Copy(Emit(...), copy_to=...)`, a `File` subclass, and `checks =
  Group(*(Check(...) for each of three files))`; `Emit` captures a program's
  output. It shows: a first run prints `build copy.source = Emit(...)` and
  `build checks[0] = ...`; a second run reuses every step and builds nothing;
  editing the file `checks[1]` reads builds `checks[1]` alone; `--dry` shows
  `checks[1] = Check(...)  # run` after that edit; the program `Emit` captures
  does not appear in the output; the copied file is written. On POSIX, SIGTERM during a
  slow step prints `interrupted: stopping <name>` and exits 130.
- Each behaviour change is covered by a test that fails before its patch.
- Nothing here is a live or external effect: the suite and the CLI scenario are
  the whole check.

**Author action required at completion.** Push `main` and confirm CI passes on
all three platforms, or authorize the executor to push. Discharges the CI
clause.

### Patches

- [x] **R16.1: Label a step in one place.** Replace the 27 copies of
  `step.name or type(step).__name__` with one `Step` helper. No behaviour
  change; the suite passes unchanged.
- [x] **R16.2: Sweep exactly the artifacts no record lists.** As decision 5.
  Tests: a recorded step's `.d.ts` artifact and an artifact with no suffix
  survive a due sweep; an unlisted file and a version-1 record's artifacts are
  deleted; a reused step keeps its entry's list. The spec's Records and
  Artifacts sections state the list and the rule.
- [x] **R16.3: Name inline and loop-built steps from the variable that reaches
  them.** Decisions 1–4, in `engine/graph.py`, with reuse, records, artifacts,
  `--dry` and every message following the name. Tests cover a keyword path, a
  positional path, a tuple keyword path, a nested path, a step shared by two
  variables, a step in an included file, a hidden dependency named by
  attribute, reuse of each, and a rebuild of one loop member leaving the others
  reused. The spec's Results section defines derived names, the class-name
  fallbacks go, and the backlog's "Reuse unnamed steps" item and the user
  manual's "never reused" line go.
- [x] **R16.4: Capture a command's output without printing it.** Decision 7.
  Tests: a captured command's standard output is returned and not printed,
  with `--jobs` 1 and 2; with `show_output=True` it is both; standard error
  is shown in both modes; a failing captured command still quotes standard
  error; a `RustBinary` build prints no cargo JSON. The `_cmd` docstring
  states both flags, and the backlog item goes.
- [ ] **R16.5: Rename output to copy_to.** Decision 8, in `File`,
  `Directory`, their docstrings, the spec's Generated files and Directory step
  sections, and the tests. A test shows `output=` fails as an unknown
  argument. The backlog item goes.
- [ ] **R16.6: Name what Ctrl-C stops.** Decision 6. Tests raise the interrupt
  with `_thread.interrupt_main()` with `--jobs` 1 and 2 and check the line for
  a running step, a live service, and neither. The spec's Teardown section
  states the line, and the backlog item goes.
- [ ] **R16.7: Run the naming scenario through the CLI.** Add the acceptance
  scenario as a test that runs `roadie` as a subprocess in a temporary git
  project. Its SIGTERM clause is marked POSIX-only with the R14 marker.

## Backlog

See [backlog.md](backlog.md).
