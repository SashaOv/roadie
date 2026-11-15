# Code quality

Tag: `CQ`.

## Goal and design

Roadie's source gets easier to change and shorter, with the DSL and the
engine/build/system layering kept. The findings are in
[quality-taste-review.md](quality-taste-review.md); this spec cites them by
number (1–10, G1–G6, H1–H7) and does not restate them.

The measure is the line count of `git ls-files 'src/roadie/*.py'`. Each
build's record states the count it leaves.

The work has three kinds, done in this order within the sketch below:

- **Defects.** A failure that reads as success, state that outlives its run,
  and messages that show parser text.
- **Deletions.** Code and prose that say one thing twice.
- **Owners.** Each kind of state gets one owner: a run-owned command facility
  runs commands, a scheduler object holds the scheduling state that `run.py`
  passes between functions, and a step declares its attributes in
  `attributes()`.

### Build sketch

The author allowed build boundaries to be sketched ahead. A sketched build
has no number and no patches until it is planned; the order and the line
estimates are proposals.

| Build | Covers | Lines |
|---|---|---:|
| Failures surface and state ends with its run | 1, G2, G3, G4, G5 | +40 |
| Nothing is written twice | G1 (docstring), G6 (test layout), the duplicates in Facts | −110 |
| One owner runs commands | 2, 6, G1, H1, the author's two finds | −30 |
| The scheduler owns its state | 4, H2, H3, the serving flag, `env_values` | −230 |
| A step declares its attributes | 5, the selected `attributes()` approach | +50 |
| Release plans before it changes anything | 8, H7 | −30 |

That is about 310 lines, 5% of the source. `run.py` gives the largest share,
from 912 lines to about 680.

Order constraints:

- "One owner runs commands" lands before R18, so that tracing wraps one
  command path.
- Windows is suspended (2026-11-15): CI runs Ubuntu and macOS, and acceptance
  names those two. Windows tests resume as their own build, on a Windows
  machine.
- "A step declares its attributes" and the public API part of "One owner runs
  commands" change what Niva imports. Each needs a paired Niva change, after
  Niva's `ws25` branch merges.

## Facts

- 2026-11-14: at `4ee2b6a`, `src/roadie` has 6,277 lines in 43 files, of which
  503 are docstrings. Tests have 9,473 lines. `uv run pytest -q`: 420 passed,
  2 skipped.
- 2026-11-14: read against the code, these review claims hold: `_git_hash`
  returns `None` on failure and `same_as_git_stores` compares the two results
  (1); `_value_adapters` and `_reported_unreadable` are module globals with no
  removal (G2); `load_record_at` prints `str(JSONDecodeError)` (G4);
  `_one_requested` is in `cli.py` and `execute` has no such check (G5);
  `_schedule`, `_execute_step` and `_run_one` pass 11 to 18 state arguments
  each, and `_run_one`'s build branch repeats `_start_one` (4, H3); `run.py`
  tests `type(step).serve is not Step.serve` in five places.
- 2026-11-14: duplicates the review does not list:
  - `wait_for_port` and `wait_for_http_server` (`engine/process.py`) differ in
    the readiness test and one message;
  - `_marked_default` and `_marked_setup` (`engine/graph.py`) differ in two
    messages;
  - `_path_attribute` and `_relative` (`engine/fingerprint.py`) differ in
    `as_posix()` against `str()` on two lines;
  - `tracked` and `tracked_files` (`build/generated.py`) run the same
    `git ls-files`;
  - `PyProject`, `CargoManifest` and `PackageJson` (`build/release.py`) each
    define the same `__init__`, `_file` and `fingerprint`;
  - `Graph.plan_results` has no caller in `src`.
- 2026-11-14: docstrings restate `docs/spec.md`: `Release` has 50 lines,
  `Step._cmd` 27 and `Step._start_cmd` 19. The spec holds the Windows command
  rules at lines 624–626.
- 2026-11-15: correction to the fact above. A step kind's docstring is its
  spec: `docs/spec.md` "Step kinds" and "Step helpers" say so, and the
  messages in `Release`'s docstring (`nothing to release`, `holds different
  versions`, …) appear nowhere in `docs/spec.md`. Only `_cmd`'s Windows
  paragraph (10 lines) and its `cannot run` sentence restate the spec.
  Docstrings of five lines or more total 374 lines.
- 2026-11-15: Niva imports `wait_for_port` and `signal_server` from
  `roadie.engine.process`, and `_executable_from_cargo_messages` from
  `roadie.build.rust`; it imports none of `plan_results`, `tracked`,
  `tracked_files`, `batch_command_line` or the `graph`/`fingerprint`
  helpers. A repeated five-line window scan of `src/roadie` finds, besides
  the Facts duplicates, `run.py`'s state-argument lists and
  `HttpService._start_cmd`, which restates `Step._start_cmd`'s signature to
  change one default; moving that default onto `Step` would reserve the name
  `health_path` on every step, so it stays.
- 2026-11-14: `tests/` defines 189 step subclasses, about 160 of them with no
  attributes, and `src` defines 15. Niva defines 23 and imports
  `roadie.engine.process`, `roadie.engine.run.execute` and
  `roadie.build.rust._executable_from_cargo_messages`. Niva takes Roadie from
  branch `main`.
- 2026-11-14: `docs/spec.md` states the messages `… can each be requested only
  once per run; request one of them` (line 125) and `ignoring unreadable
  record <path>: <reason>` (line 727).

--- Implemented / Next ---

## Builds

### CQ1: Failures surface and state ends with its run

Status: implemented 2026-11-15 (commits CQ1.*)

**Improvement.** A generated file that git cannot hash fails the step; today
two failed hashes compare equal and the old file is kept as unchanged. Two
once-per-run targets are refused by the engine before any step runs; today a
caller other than the CLI gets `cannot schedule the plan`. A damaged record
is reported in the user's terms, once per run; today the warning shows
`Expecting value: line 1 column 1 (char 0)` once per process. A step class
that is dropped is collected; today the adapter cache keeps it.

**Decisions.** Proposed for the checkpoint.

1. A failed hash raises `cannot compare <path> with git: <git's message>`,
   where `<path>` is the file that was hashed. A successful hash with empty
   output fails the same way, with `git printed no hash`.
<--A: approved -->
2. The once-per-run check moves from `cli.py` into `execute`, with the same
   message, before the run lock is taken.
<--A: approved -->
3. The record warning's reason is `invalid JSON`, `not a Roadie record`, or
   the operating system's message when the file cannot be read. The warning
   prints once per record file per run; the set of reported files belongs to
   `run_scope`. The spec's sentence at line 727 gains the three reasons.
<--A: approved -->
4. A step class holds its own value adapter, so no module-level table names
   the class.
<--A: approved -->
5. `env_values` stays on the step until the scheduler build moves it to the
   run.
<--A: approved -->

**Acceptance check.**

- `uv run roadie` passes on the three CI platforms with the tests the patches
  add.
- `git grep -n '_value_adapters\|_reported_unreadable' src` prints nothing.
- The record states the line count of `src/roadie`.

Author action required at completion (2026-11-15): reproduce and fix the
Windows acceptance failure on a Windows machine, then verify the full gate
there. Discharged 2026-11-15 by suspension: the author suspended Windows
tests, and the Windows fix is a backlog item.

### Patches

- [x] **CQ1.1: Fail when git cannot hash a generated file.** Tests: the
  destination's hash fails; the built file's hash fails; both fail; the hash
  is empty. Each asserts the message and an unchanged destination, for `File`
  and for `Directory`.
- [x] **CQ1.2: Refuse two once-per-run targets in the engine.** Tests call
  `execute` with two such targets and a setup step that writes a file, and
  assert the message and no file. Also: one such target with ordinary
  targets; one target named twice.
- [x] **CQ1.3: Report a damaged record in the user's terms, once per run.**
  Tests: invalid JSON; valid JSON of the wrong shape; an unreadable file; two
  reads in one run print once; a second run in the same process prints again.
  `docs/spec.md` changes with it.
- [x] **CQ1.4: Keep each value adapter with its step class.** Test: a step
  class defined in a function, used once and dropped, is gone after
  `gc.collect()`, observed through a weak reference.
- [x] **CQ1.5: Wait on an event before interrupting the service test.**
  `test_ctrl_c_stops_a_requested_service_that_outlives_it` waits, with a
  timeout, for the service to reach `serve`, and its watcher thread ends on
  failure too. The `Sleeper.wait` fake blocks on an event instead of sleeping
  five seconds.

### Execution and acceptance

2026-11-15: committed CQ1.1–CQ1.5 as `356f273`, `288396c`, `59191f2`,
`27b162d`, and `858b302`. Each patch passed `uv run roadie` before commit.
The full-suite results were respectively 436, 440, 444, 445, and 446 passed,
with 2 skipped on this host. The Git-hash, restricted-target, damaged-record,
and dropped-class regression tests failed before their corresponding fixes.
CQ1.5 changes test synchronization; its success and startup-failure cases pass.

Final-head acceptance, after `uv run roadie --clean`:

- Head: `858b30238f32ddf6e5eb81200a987bac32704b3c`.
- Tree: `540e8037c26ffeb8155c2272126b3a6341cefc76`.
- Gate script `build.py` Git blob:
  `51797c00949844d85e3921d4c375e24299cc944c`.
- CI workflow Git blob: `613a812531e064fd71441d0da16f1345a13e4a69`.
- Host: `L25.local`, macOS 26.6.2, arm64; Python 3.13.13; pytest 9.2.0,
  pytest-xdist 3.8.0, Ruff 0.17.3, mypy 2.4.1, Pydantic 2.14.2.
- Command: `uv run roadie --report /private/tmp/cq1-acceptance-858b302.jsonl`.
  PASS: Ruff, mypy, pytest (446 passed, 2 skipped); all three gates executed,
  with no reused gate results.
- `git grep -n '_value_adapters\|_reported_unreadable' src`: PASS, no matches
  (exit 1).
- Source measure: 6,284 lines in 43 tracked Python files, +7 from the baseline.
- Raw local event report: `/private/tmp/cq1-acceptance-858b302.jsonl`;
  provenance, environment lock snapshot, and JUnit report:
  `/private/tmp/cq1-acceptance-858b302/`. The uv lock is untracked; the saved
  provenance records its SHA-256 and installed package versions.
- Three-platform CI, [run 43746398695](https://github.com/SashaOv/roadie/actions/runs/43746398695)
  on the same head: Ubuntu PASS; macOS PASS; Windows FAIL. Windows ran
  Python 3.15.2 and its pytest worker crashed with `keyboard-interrupt` while
  running `test_ctrl_c_stops_a_requested_service_that_outlives_it[False]`.
  Ruff and mypy passed there; pytest stopped at 205 passed, 9 skipped,
  1 failed. Hosted setup, environment, and per-check logs remain with the run.

**Verdict: acceptance remains open.** On 2026-11-15 the author directed that
Windows failures be fixed on a Windows machine. No further Windows fixes or
CI retries were attempted after that instruction.

A tentative test-only change had already been made before the handoff: it
signals readiness from the process's `wait()` rather than at entry to
`Service.serve`. The working hypothesis is that SIGINT can arrive before the
service enters its protected wait. This is not yet verified on Windows.
The diff remains uncommitted in `tests/build/test_service.py`, as required
for acceptance fixes by `onebuild`. Local verification of that diff passed
Ruff, mypy, and 446 tests with 2 skipped; its event report is
`/private/tmp/cq1-acceptance-fix-858b302.jsonl`. This local pass does not close
the Windows clause. A copy of the candidate diff and its JUnit report is
saved beside the committed-head provenance. The acceptance record also
remains uncommitted; no commits beyond the five planned patches were made.

Review, 2026-11-15, on `main` at `87311d1` (`cq1` fast-forwarded into
`main`):

- The author committed the tentative test change as `87311d1` "Beginning of
  fix on Windows, will continue in a separate build", outside the patch list.
- Subject drift: CQ1.3 landed as "Report damaged records once per run in user
  terms" and CQ1.5 as "Coordinate service interruption tests with events";
  the other three match their patch names.
- `uv run roadie` after `--clean` on macOS: 446 passed, 2 skipped. The grep
  prints nothing. `src/roadie` is 6,284 lines.
- The author suspended Windows tests; CI's matrix drops `windows-latest`.
  Ubuntu and macOS passed in run 43746398695, so acceptance passes on the
  platforms CI runs.

### CQ2: Nothing is written twice

Status: planned, approved 2026-11-15

**Improvement.** Each duplicate in Facts becomes one definition, and code
nothing calls goes. `src/roadie` shrinks by about 110 lines, from 6,284, with
no change in behavior, messages or public names.

**Decisions.** Proposed for the checkpoint.

1. Step-kind docstrings stay: they are the spec. Only `_cmd`'s Windows
   paragraph and `cannot run` sentence go, replaced by a pointer to "Step
   helpers".
2. Names Niva imports keep their signatures: `wait_for_port`,
   `signal_server`, `_executable_from_cargo_messages`.
3. `_relative` and `_path_attribute` become one function that returns POSIX
   form outside the root too. This changes the code-hash key of a build file
   outside the project root on Windows only; any Roadie change already
   changes every code hash.
4. `batch_command_line` becomes `_batch_command_line` and leaves the
   `roadie.system` exports (G1); its tests call it from its module.

**Acceptance check.**

- `uv run roadie` passes on Ubuntu and macOS CI with no test changed except
  by CQ2.5's move and by imports of the renamed function.
- `git grep -n 'plan_results\|_marked_setup\|wait_for_http_server' src`
  prints nothing. `tracked` and `tracked_files` keep their names (CQ2.4).
- The record states the line count of `src/roadie`; it is at most 6,200.

No clause needs the author.

### Patches

- [x] **CQ2.1: Wait for readiness in one loop.** `wait_for_port` and
  `wait_for_http_server` call one loop that takes the readiness test and the
  timeout message.
- [x] **CQ2.2: Read a marked step in one function.** `_marked_default` and
  `_marked_setup` become one function that takes the mark's name for its
  messages; `Graph.plan_results` goes.
- [x] **CQ2.3: Make a path relative in one function.** `_relative` and
  `_path_attribute` in `engine/fingerprint.py` become one, as decision 3
  says.
- [x] **CQ2.4: List tracked files in one git call.** `tracked` and
  `tracked_files` share one `git ls-files` helper; their messages stay.
- [x] **CQ2.5: Keep Windows command rules in the system layer and the spec.**
  `_cmd`'s docstring loses the Windows paragraph, decision 4 renames the
  helper, and `tests/engine/test_batch.py`'s quoting tests move to
  `tests/system/test_command.py` (G6).
- [ ] **CQ2.6: Share the manifest sources' path and fingerprint.**
  `PyProject`, `CargoManifest` and `PackageJson` take `__init__`, `_file`,
  `__repr__` and `fingerprint` from one base class.

## Backlog

- Resume Windows tests: fix `test_ctrl_c_stops_a_requested_service_that_outlives_it`
  on a Windows machine (CI's Python 3.15.2 worker crashed with
  `keyboard-interrupt`; `87311d1` holds the unverified start), then restore
  `windows-latest` to CI's matrix.

- Decide before the attributes build: explicit `attributes()` on every step
  adds about 320 lines to `tests/`, two per attribute-less subclass. The
  alternatives are a base `attributes()` that returns `{}`, or one that reads
  dataclass fields; the review rejected both as fallbacks.
<--A: `attributes()` that returns `{}` is OK -->
<--A: if the docstring is implementation-related and doesn't belong in spec, remove it. Otherwise, replace copies with links to the spec -->
- Decide whether `__all__` in `build/__init__.py` and `system/__init__.py` is
  an approved exception to the initializer rule (G6).
<--A: approved both exceptions -->
- Share one JUnit reader between `Pytest` and Niva's `ContractFuzz`, after
  the naming and aggregation differences are settled (7).
- Share the input normalization of `Check` and `Pytest` (H4).
- Reorder modules to imports, classes, functions when a build already edits
  them (G6).
- Look for line savings in `tests/`, which is 1.5 times the source and was
  not examined.
- Niva, in its own change spec: declare helper-code and Node inputs (3);
  compose Jammer's process and storage owners (9); one workspace-version
  reader (10); distinct mypy cache directories from one helper (H5).
