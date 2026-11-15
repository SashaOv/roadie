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
| Nothing is written twice | G1 (docstring), G6 (test layout), the duplicates in Facts | −330 |
| One owner runs commands | 2, 6, G1, H1, the author's two finds | −30 |
| The scheduler owns its state | 4, H2, H3, the serving flag, `env_values` | −230 |
| A step declares its attributes | 5, the selected `attributes()` approach | +50 |
| Release plans before it changes anything | 8, H7 | −30 |

That is about 530 lines, 8% of the source. `run.py` gives the largest share,
from 912 lines to about 680.

Order constraints:

- "One owner runs commands" lands before R18, so that tracing wraps one
  command path.
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

Status: planned, approved 2026-11-15

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

No clause needs the author.

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
- [ ] **CQ1.4: Keep each value adapter with its step class.** Test: a step
  class defined in a function, used once and dropped, is gone after
  `gc.collect()`, observed through a weak reference.
- [ ] **CQ1.5: Wait on an event before interrupting the service test.**
  `test_ctrl_c_stops_a_requested_service_that_outlives_it` waits, with a
  timeout, for the service to reach `serve`, and its watcher thread ends on
  failure too. The `Sleeper.wait` fake blocks on an event instead of sleeping
  five seconds.

## Backlog

- Decide before the attributes build: explicit `attributes()` on every step
  adds about 320 lines to `tests/`, two per attribute-less subclass. The
  alternatives are a base `attributes()` that returns `{}`, or one that reads
  dataclass fields; the review rejected both as fallbacks.
<--A: `attributes()` that returns `{}` is OK -->
- Decide whether docstrings that restate `docs/spec.md` go, leaving the
  `# (spec) <section>` anchor. This is about 200 of the 330 lines the
  deletion build estimates.
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
