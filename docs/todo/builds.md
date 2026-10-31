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

Status: planned, approved 2026-10-31

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
- [ ] **R13.10: Ask the user to run checks.** `UserTask(instructions,
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
- [ ] **R13.11: Ask the user to approve a run.** `UserApproval(question)`
  is a user step whose question is asked once,
  `<question> [y/n] `, under R13.10's rules for answers and the terminal.
  `y` continues the run; `n` fails the step with `cancelled by the user`,
  so the run stops as on any failure. It is never reused and keeps no
  record, so it asks on every run, and its output fingerprint is its own
  fingerprint. Roadie's `build.py` makes `release` require
  `UserApproval("Release main?")`. `roadie.build` exports it, and the
  backlog item goes.

**Author action required at completion.** Push `main` and confirm CI passes
on both platforms, or authorize the executor to push. Discharges the CI
clause.

## Backlog

See [backlog.md](backlog.md).
