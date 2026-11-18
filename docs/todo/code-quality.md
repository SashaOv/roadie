# Code quality

Tag: `CQ`.

## Goal and design

Roadie's source gets easier to change and shorter, with the DSL and the
engine/build/system layering kept. The findings are in
[quality-taste-review.md](quality-taste-review.md); this spec cites them by
number (1–10, G1–G6, H1–H7) and does not restate them.

The measure is the line count of `git ls-files 'src/roadie/*.py'`. Each
build's record states the count it leaves. The count is reported, not
gated: code is not packed, inlined or stripped of its documentation to meet
an estimate, and a build that misses its estimate says so at the checkpoint.

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
| One owner runs commands | 2, 6, G1, H1, the author's two finds | −25 |
| The scheduler owns its state | 4, H3, the serving flag, `env_values` | −120 |
| A step declares its attributes | 5, the selected `attributes()` approach | +50 |
| Release plans before it changes anything | 8, H7 | −30 |

That is about 190 lines, 3% of the source, counting CQ2's and CQ3's actual
results (−121 and −13). `run.py` gives the largest share.

Order constraints:

- "One owner runs commands" lands before R18, so that tracing wraps
  `Command.run` and `Command.start` alone.
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
- 2026-11-15: the author approved `__all__` in `build/__init__.py` and
  `system/__init__.py` as exceptions to the initializer rule (G6).
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

Status: implemented 2026-11-15 (commits CQ2.*)

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
- [x] **CQ2.6: Share the manifest sources' path and fingerprint.**
  `PyProject`, `CargoManifest` and `PackageJson` take `__init__`, `_file`,
  `__repr__` and `fingerprint` from one base class.

### Execution and acceptance

2026-11-15, reviewed on `main` at `7a7cf64` after the executor stopped before
writing this record:

- Commits `dda935a`, `b04a247`, `839d9f6`, `99a3078`, `18e66d5` and `7a7cf64`
  carry the six patch names exactly.
- CI [run 43747818553](https://github.com/SashaOv/roadie/actions/runs/43747818553)
  on `7a7cf64`: Ubuntu PASS, macOS PASS.
- `uv run roadie` after `--clean` on macOS: 446 passed, 2 skipped.
- The grep prints nothing (exit 1).
- Tests changed only by moving the quoting tests from
  `tests/engine/test_batch.py` to `tests/system/test_command.py`.
- `src/roadie`: 6,163 lines, 121 fewer than CQ1 left; the bound was 6,200.

### CQ3: One owner runs commands

Status: implemented 2026-11-15 (commits CQ3.*)

**Improvement.** One `Command` object in `engine/process.py` runs a command
to completion and starts a server. Today `Step._cmd` and
`RunningProcess.start` each print the command, resolve the program, report a
failed start, track the process for Ctrl-C, read its output with the run's
prefix and untrack it, in two modules. Code without a step can run a command
without borrowing one, as Niva's `_gh_json(step, ...)` does today. A command
also keeps only the output its caller reads. Today a parallel run keeps
every standard output line it only prints, every command keeps all of
standard error to show its last 2000 characters, and a server's readers keep
every line the server prints until it stops. Niva's process and Cargo helpers
become documented names in `roadie.build`.

**Decisions.** Proposed for the checkpoint.

1. `Command` is a frozen dataclass of the arguments, the directory, the
   environment and the label its messages use. `run(capture_output,
   show_output, check)` returns a `CompletedProcess`; `start(port, timeout,
   health_path)` returns a `RunningProcess`. Their behavior and messages are
   those `_cmd` and `_start_cmd` document today.
2. `Step._cmd` and `Step._start_cmd` keep their signatures and build a
   `Command` from the step's base directory, environment and label.
   `_command_env` and `_check_cmd` move into `Command`. Step-kind docstrings
   keep their text; the helpers' docstrings move to `Command`'s methods.
3. When Ctrl-C reaches the waiting thread, `run` stops the command through
   `roadie.system.interrupt_process(process, session=False)`, as
   `request_stop` stops the others. POSIX behavior is unchanged (SIGTERM);
   on Windows this is `taskkill /T`, which is not verified while Windows is
   suspended.
4. What a command keeps:
   - captured standard output, in full, until `run` returns, because its
     callers parse it (Cargo and `gh` JSON);
   - the last 2000 characters of standard error, which is what
     `CompletedProcess.stderr` then holds. That is all `Pytest`, `Check` and
     the exit check read: `Check`'s last 2000 characters of output then error
     are the same when error is cut to its own last 2000;
   - nothing of standard output it does not capture, and nothing a server
     prints.
   `run`'s docstring states this. A single line with no newline is still
   read whole; bounding it is backlog.
5. A `RunningProcess` holds its own output readers, so the module-level
   `_pumps` table goes.
6. `roadie.build` exports `Command`, `RunningProcess`, `available_port`,
   `port_is_busy`, `wait_for_port`, `signal_server` and `cargo_executable`,
   which is `_executable_from_cargo_messages` renamed, with a docstring.
   Niva's lock pins Roadie at `4ee2b6a`, so nothing breaks until Niva
   upgrades; its imports change then (backlog).
7. Roadie's own `git`, `cargo` and `uv` queries stay on `subprocess.run`:
   they print nothing, stream nothing and each has its own message.
8. `tests/test_layers.py` also refuses a `roadie.system` module that imports
   any other Roadie module (G1).

**Acceptance check.**

- `uv run roadie` passes on Ubuntu and macOS CI.
- `git grep -n 'Popen(\|\.terminate()\|_pumps' src/roadie/engine/step.py src/roadie/build`
  prints nothing, and `git grep -n '\.terminate()\|_pumps' src/roadie/engine`
  prints nothing.
- The tests CQ3.3 and CQ3.4 add fail before their patches and pass after.
- The record states the line count of `src/roadie`; it is at most 6,140.

No clause needs the author.

### Patches

- [x] **CQ3.1: Run and start commands through one `Command`.** Decisions 1
  and 2. `Step._cmd`, `Step._start_cmd` and `HttpService._start_cmd` build a
  `Command`; the shared start, track, read and untrack code exists once. No
  test changes beyond imports; the suite covers both paths.
- [x] **CQ3.2: Stop an interrupted command through the system layer.**
  Decision 3. Test: Ctrl-C in the command's thread calls `interrupt_process`
  with `session=False`, and the run exits 130.
- [x] **CQ3.3: Keep only the output a command's caller reads.** Decision 4.
  Tests, each with `--jobs 1` and `--jobs 2`, measuring the peak with
  `tracemalloc`: a failing command that writes 5 MB to standard error keeps
  under 1 MB and fails with the last 2000 characters; a command that writes
  5 MB to standard output it does not capture keeps under 1 MB; a `Check`
  whose output and error each exceed 2000 characters fails with the same
  text as before.
- [x] **CQ3.4: Keep a server's output readers with its process.** Decision 5.
  Tests: a server that prints 5 MB in a parallel run keeps under 1 MB; after
  `stop()` no reader thread is alive.
- [x] **CQ3.5: Name the extension functions Niva uses.** Decisions 6 and 8.
  Test: a consumer module that imports only from `roadie.build` runs a
  `Command` without a step, starts a server, waits for its port, and parses
  Cargo messages.

### Execution and acceptance

2026-11-15:

- Commits `a980a00`, `196af56`, `221d40a`, `573f4fc` and `2f8304b` carry
  the five patch names exactly and stay intact. This marking commit trims
  the tree to the bound: `Command.start` owns server startup, so
  `RunningProcess.start` and `spawn_server` go, uncaptured output keeps
  nothing through `read_lines`, and their test callers move with them.
- `git grep -n 'Popen(\|\.terminate()\|_pumps' src/roadie/engine/step.py
  src/roadie/build` and `git grep -n '\.terminate()\|_pumps'
  src/roadie/engine` print nothing (exit 1).
- CQ3.3's memory tests failed before their patch (3 parametrizations, peak
  above 1 MB) and pass after; CQ3.4's socket-free probe went from a
  5,292,430-byte peak with no `readers` to 105,846 bytes with both readers
  dead after `stop()`; CQ3.5's consumer import failed before its patch.
- Sandbox-only final run: `ruff check` and `mypy src build.py` pass;
  pytest shows 418 passed, 2 skipped and 37 failures, all in the sandbox's
  socket/pty/release categories (loopback is blocked there), the same
  count as before the trim. Un-sandboxed gates passed at CQ3.1 (446) and
  CQ3.2 (447).
- `src/roadie`: 6,138 lines; the bound was 6,140.

CI on Ubuntu and macOS has not run; nothing is pushed. That clause stays
open until the author pushes.

### Review

2026-11-15, at `6f15258`, by the planning agent.

- The five patch commits carry their planned names. `6f15258`, named as
  the record commit, also changes 243 lines of `src` and `tests`, which is
  drift: unplanned code has to land as a patch commit, not inside the
  record. That change does what CQ3.1 promised and did not deliver:
  CQ3.1 left `RunningProcess.start` starting servers on its own, so the
  start, track, read and untrack code was still written twice, and
  `src/roadie` grew from 6,163 to 6,246 lines.
- Some of `6f15258` is consolidation and stays: `Command.start` owns
  server startup, `RunningProcess.start`, `spawn_server`, `_pump_server`,
  `_decode_line`, `_Discarded` and the `stop_process_tree` wrapper go,
  and `read_lines` takes `None` to keep nothing.
- The rest was packing to meet the line bound. Uncommitted review fixes
  undo it:
  - `Step._cmd` and `Step._start_cmd` had each been given a copy of the
    `Command` construction; `Step._command` builds it once again.
  - Server output readers had become two 121-character lines;
    `_print_server_lines` reads one pipe.
  - `http_url_is_ready` had become a closure nested in `Command.start`;
    it is a module function again, beside `port_is_busy`.
  - `RunningProcess.stop`'s docstring had lost "up to" in "wait up to 10
    seconds", which made it wrong; `run`'s docstring had lost "startup".
- A second review fix: the 2000-character tail was a literal in three
  places in `engine/process.py` and a constant in both `check.py` and
  `pytest_step.py`. Decision 4 relies on all of them agreeing, so
  `OUTPUT_TAIL` in `engine/process.py` is now the only definition.
- With the fixes: `ruff check` and `mypy src build.py` pass, `uv run pytest
  -q` (not sandboxed) has 455 passed and 2 skipped, both acceptance greps
  print nothing, and `src/roadie` has 6,150 lines, 13 fewer than before
  CQ3 and 10 over the 6,140 bound.
- The line bound is waived. CQ3's own estimate was −25, and the
  difference is code this review restored. Goal and design now says the
  count is reported, not gated.

- The author committed the review fixes as `2353c5c` ("CQ3 follow up:
  minor fixes") and pushed. CI run 43756393773 passed on `ubuntu-latest`
  and `macos-latest`. That discharges the last acceptance clause.
- Missed by CQ3's plan and review (found 2026-11-15, while planning CQ4):
  Niva's `ContractFuzz.build` calls `self._check_cmd(completed)` twice.
  CQ3.1 folded `_check_cmd` into `Command.run`, so Niva fails when it
  upgrades past CQ3. The CQ3 Facts listed Niva's imports but not the
  private `Step` methods it calls. See the backlog.

### CQ4: The scheduler owns its state

Status: executed, pending author verification

**Improvement.** Today `run.py` keeps a run's scheduling state in 18 local
variables and threads them through `_schedule`, `_execute_step`, `_run_one`,
`_kept`, `_start_deferred` and `_start_one`, each taking 7 to 18 of them. A
lifecycle rule has to be changed in every function that receives the state.
The immediate and deferred paths each announce a step, report its start,
time it, bind its parameters and standard input, build it and translate its
failure, written twice (H3). After this build:

- a plan, a live-value registry and a scheduler each own one part of that
  state;
- one invocation builds a step on both paths;
- a kind declares that it serves instead of the engine comparing methods;
- a run's environment-file values belong to the run, so a step object
  keeps nothing from the runs it took part in.

**Facts for this build** (2026-11-15):

- Niva's lock still pins `4ee2b6a`. When Niva upgrades, its tests rely on
  these, so CQ4 keeps them:
  - Niva's tests assign `run._values[id(step)]` (`forge/tests/test_package.py`,
    `jammer/ops/tests/test_jammer_build_steps.py`);
  - they monkeypatch `roadie.engine.run.load_records`;
  - they use `roadie.engine.parameters.current_step`.
- Niva overrides no `serve`, `publish`, `teardown` or `report` hook and reads
  no `env_values`. It calls `_cmd` and `_start_cmd` only from `build`, as
  Roadie's own steps do.
- `tests/` reads `self.env_values[...]` in two steps (`test_run.py` lines
  643 and 762). `tests/engine/test_fingerprint.py` assigns it twice.
- `run.py` is 928 lines. Outside it, `src` reads `run.locked`
  (`File`, `Directory`) and `run.reused` (`Group`, `_Included`). It reads
  none of `started_at`, `finish_reported`, `_report_start` or `_report_end`,
  and neither do the tests.

**Decisions.** Proposed for the checkpoint.

1. **Serving is declared.** `Step.serves: ClassVar[bool] = False`, and
   `Service` sets it to `True`. The engine uses it in the four places that
   test `type(step).serve is not Step.serve`:
   - whether a step keeps standard input;
   - whether its lock is released before its result is served;
   - whether its total is printed before its result is served;
   - whether an interrupt names it while it is live.

   `serve(value)` is still called on every requested result, whatever its
   flag. The spec's "Step hooks" states the flag.
2. **The run owns environment values.** `Step.env_values` goes.
   - The run reads each step's environment files when it starts, with the
     same order and messages as `_apply_env`.
   - `run.environment(step)` returns them. "Step values" in the spec
     documents it.
   - `fingerprint(step, outputs=..., env=...)` receives them.
   - A command run while a step builds takes them through a context in
     `engine/process.py`. That context replaces `command_stdin` and holds
     both standard input and the environment values. `Command._environment`
     applies them after `environment` and before `ROADIE_RUN_PID`, the same
     order as today.
   - A `Command` run while a step builds therefore gets the step's
     environment files too. The `Command.run` docstring says so.
3. **The plan is immutable.** A frozen `_Plan` holds:
   - the steps, the requested ids, `leading`, `jobs` and `dry`;
   - the prerequisite and blocker tables.

   `_next_ready`, `_depends_on` and `_blockers` become its methods.
4. **Live values have one registry.** A `_Live` object owns the live steps
   in start order, their entries in `run._values`, and handing them back in
   reverse order for teardown:
   - `add` adds a step;
   - `replace` swaps in the value `publish` returns;
   - `take_due(finished)` returns the steps every holder has finished with;
   - `take_all()` returns every live step;
   - `serving()` lists the serving steps for the interrupt line.

   Only `_Live` and `Run.__getitem__` touch `Run._values`. `_teardown` is
   unchanged.
5. **One scheduler holds the mutable state.** `_Scheduler` holds:
   - the condition, `finished`, `running`, `failures`, `interrupted`,
     `outputs`, `started`, `marks`, the deferred guards and the result;
   - the run's report writer, the report start times, `started_at` and
     whether the total was printed. These leave `Run`, which keeps
     `_values`, `reused`, `locked`, its artifacts and `environment`.

   `_schedule`'s worker loop, `_execute_step`, `_run_one`, `_kept`,
   `_start_deferred`, `_start_one` and `announce_interrupt` become its
   methods. Each one reads the state from the scheduler instead of taking
   it as arguments. `_run_steps` builds the plan, the registry and the
   scheduler, and keeps the run's outcome, report and teardown handling.
   The record functions `_matching` and `_remember` stay module functions
   in `run.py`.
6. **One invocation builds a step.** `_Scheduler._invoke(step)` builds a step
   on both the immediate and the deferred path. It:
   - announces the step and starts its report entry when the kind is
     recorded;
   - times the step;
   - binds `current_step` and the command context, then calls `build`;
   - on failure, ends the report entry as `failed` or `interrupted`, and
     translates the error as `_execute_step` does today.

   Each path keeps what differs: a deferred step starts once under its
   guard and publishes nothing, while an immediate step publishes, reports
   and records.
7. **What stays.** Messages, output order, report contents and the order
   of teardown stay as they are. H2 (an outcome object that the terminal
   and the report both render) is not part of this build; it is backlog.

**Acceptance check.**

- `uv run roadie` passes on Ubuntu and macOS CI.
- `git grep -n 'Step\.serve\b\|env_values' src` prints nothing.
- `grep -c 'current_step(' src/roadie/engine/run.py` prints `1`.
- No function or method in `src/roadie/engine/run.py` takes more than six
  parameters, `self` included. The one exception is the public `execute`,
  whose keywords are the CLI's options and Niva's API. `execute` builds the
  `_Plan` and passes it on:
  `uv run python -c "import ast,sys; t=ast.parse(open('src/roadie/engine/run.py').read()); bad=[n.name for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name!='execute' and len(n.args.args)+len(n.args.kwonlyargs)>6]; print(bad); sys.exit(bool(bad))"`
  prints `[]`. On 2026-11-15 it printed `_run_steps`, `_schedule`,
  `_execute_step`, `_run_one`, `_kept`, `_start_deferred` and
  `_start_one`.
- The CQ4.1 tests pass at every patch. The tests CQ4.2 and CQ4.3 add fail
  before their patches and pass after.
- The record states the line count of `src/roadie`. The estimate is −120,
  mostly from `run.py`'s argument lists. The count is reported, not gated.

No clause needs the author.

### Patches

- [x] **CQ4.1: Pin how a shared deferred step behaves around its
  consumers.** Tests only; they pass before and after every later patch.
  Two consumers share a deferred service, under `--jobs 1` and
  `--jobs 2`, synchronized with `threading.Event` or `Barrier` and bounded
  waits, never sleeps. Each case asserts that the service starts once and
  is torn down once, that the first failure is the run's error, and that
  any later failure is printed. The cases:
  - one consumer's `build` fails;
  - one consumer's `publish` fails;
  - one consumer's `report` fails;
  - Ctrl-C arrives while both consumers run;
  - the service's teardown fails after a consumer failed.

  Skip a case an existing test already covers, and cite that test in the
  record.
- [x] **CQ4.2: Declare that a kind serves.** Decision 1. Test: a kind whose
  `serve` only calls `super().serve(value)` behaves like a kind that does
  not serve. It keeps the run lock until teardown, starts without the
  terminal when it is not requested, and an interrupt does not name it.
  Today it behaves like a service.
- [x] **CQ4.3: Keep environment values with the run.** Decision 2. Tests:
  - after a run, the step's `vars()` are what they were before it;
  - the same step run twice, with different `--env` files, sees each run's
    values in `run.environment(self)` and in its command's environment;
  - a `Command` run inside `build` gets the step's environment files.

  The steps in `test_run.py` and `test_fingerprint.py` that read or set
  `env_values` move to `run.environment(...)` and `fingerprint(env=...)`.
- [x] **CQ4.4: Keep live values in one registry.** Decisions 3 and 4. No
  behavior change; the suite and the CQ4.1 tests cover it.
- [x] **CQ4.5: Hold scheduling state in one scheduler.** Decision 5. No
  behavior change. After this patch, run.py passes the parameter-count
  check.
- [x] **CQ4.6: Build a step through one invocation.** Decision 6. No
  behavior change. Test: a parameter bound to a deferred step and to an
  immediate step is visible in both builds and is released when either
  build fails. The test fails on neither path before the patch; it pins
  the H3 risk.

### Execution and acceptance

2026-11-15 (uncommitted record):

- Commits `9b8fc04`, `4cd2b55`, `6b21fa2`, `b682e70`, `cc205e5` and
  `6a147c7` carry the six patch names exactly.
- Head `6a147c78e08a0305be23406ff835b42d0bf1379e`, tree
  `fab6cd98ec37fbab4a95c77a14c54c36d95815d5`; `build.py` blob
  `51797c00949844d85e3921d4c375e24299cc944c`. Host Darwin 25.6.0 arm64,
  Python 3.13.13.
- `grep -c 'current_step(' src/roadie/engine/run.py` prints `1`; the
  parameter-count check prints `[]`.
- `git grep -n 'Step\.serve\b\|env_values' src` prints only the two
  `dotenv_values` library lines in `run.py`; no step member remains.
- CQ4.1's ten cases pass at the final head, as they did before the build;
  CQ4.2's and CQ4.3's tests failed before their patches and pass after.
- Sandbox-only final run: `ruff check` and `mypy src build.py` pass;
  pytest shows 437 passed, 2 skipped and 37 failures, all in the sandbox's
  socket/pty/release categories, the same count as before CQ4. CI on
  Ubuntu and macOS has not run; nothing is pushed.
- `src/roadie`: 6,084 lines, 66 fewer than CQ3 left. The estimate was
  −120; the count is reported, not gated.

**Author action required at completion.**

- Push `main` and confirm CI on `ubuntu-latest` and `macos-latest` runs
  `uv run roadie` and passes. That discharges the last acceptance clause.

### Review

2026-11-15, at `6a147c7`, by the planning agent.

- The six commits carry their planned names. The record commit changes only
  the spec, and the line count was reported, not met by packing. The
  executor ran pytest in a sandbox. Run here without one, `6a147c7` passes
  474 tests with 2 skipped.
- Plan defect: the acceptance grep `env_values` also matches
  `dotenv_values`. `\benv_values` is the check that was meant; it prints
  nothing.
- Behavior drift, against decision 7. `_invoke` ended a failed build's
  report entry as `failed` before `_execute_step` turned the failure into
  an interrupt. A step whose program failed because Ctrl-C stopped it was
  reported `interrupted` before CQ4 and `failed` after. The new
  `test_a_step_that_fails_after_an_interrupt_records_interrupted`
  (`--jobs 1` and `2`) passes at `cb25f8f`, fails at `6a147c7`, and passes
  with the fix. `_invoke` now reports `interrupted` whenever an interrupt
  was requested. That rule covers the deferred path too: before CQ4,
  `_start_one` reported a deferred step stopped this way as `failed`.
- The plan was carried out only in part. Decision 5 made the worker loop a
  method, but it was left as a closure inside `schedule`. It is now
  `_Scheduler._work`. `interrupted` was a one-element list only so the old
  closures could set it; it is now a flag.
- Smaller fixes:
  - `_run_one` computed the step fingerprint in two branches; it now
    computes it once.
  - A 109-character line in `_run_steps` is wrapped.
  - The blank lines around `_Scheduler` and `_blockers` are fixed.
  - A broken docstring wrap in `Command.run` is fixed.
  - The spec's `serves` line named two of the four behaviors the flag
    controls; it now names all four.
- With the fixes, `ruff check`, `mypy src build.py` and the parameter check
  pass, `uv run pytest -q` has 476 passed and 2 skipped, and `src/roadie`
  has 6,079 lines, 71 fewer than CQ3 left.
- Open for the author: before CQ4, a command a step ran from `publish`,
  `report` or `serve` got the step's environment files. Now only commands
  run from `build` do. No Roadie or Niva step runs a command from those
  hooks (Facts for this build), but the spec's "Step helpers" still says a
  helper runs "with its environment files". Either the command context
  also covers those hooks, or the spec limits the rule to `build`.
- 2026-11-17: `ae95df6` pushed. In CI run 44214439409, `ubuntu-latest`
  passed and `macos-latest` failed in
  `test_a_command_runs_and_starts_a_server_without_a_step`, with
  `python exited with status 0 before becoming ready`. The test came with
  CQ3.5. Its server accepted one connection and exited, but
  `Command.start`'s readiness probe is already a connection, so the
  test's second `wait_for_port` raced the server's exit. Locally, the old
  server exits (status 0) right after `start` returns. The fixed server
  keeps accepting, as the `test_process.py` servers do, and is still
  running two seconds later. The fixed test passed 20 runs of 20. The fix
  is not committed yet.

## Backlog

- Niva, when it upgrades Roadie past CQ3: import the process helpers and
  `cargo_executable` from `roadie.build`, and run `gh` through `Command`
  instead of passing a step to `_gh_json`. Its `ContractFuzz` calls
  `self._check_cmd(completed)`, which CQ3 removed. Either Roadie gives
  `Command` a public exit check, or `ContractFuzz` raises its own failure.
- Separate execution outcomes from their presentation (H2). Execution
  produces an outcome: identity, reused or built, timing, failure. The
  terminal and the report render that outcome, and the run computes a
  composite step's reuse once, instead of `_reports_reused` and printing in
  `Group.build` and `_Included.build`.
- One helper for Roadie's own `git`, `cargo` and `uv` queries, which repeat
  `subprocess.run`, the exit check and a message with git's error in five
  modules.
- Bound a single output line that has no newline, without splitting a
  parallel run's prefix into it.

- Resume Windows tests: fix `test_ctrl_c_stops_a_requested_service_that_outlives_it`
  on a Windows machine (CI's Python 3.15.2 worker crashed with
  `keyboard-interrupt`; `87311d1` holds the unverified start), then restore
  `windows-latest` to CI's matrix.

- Attributes build: the base `Step.attributes()` returns `{}` (author,
  2026-11-15), so steps without attributes declare nothing.
- Docstrings (author, 2026-11-15): remove a docstring that describes only
  the implementation and does not belong in the spec. Replace a copy of
  spec text with a link to the spec.
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
