# Backlog

Planned work that [the spec](../spec.md) does not describe. Each item is one
bullet, except the input-enforcement design, which is moved whole.

## Features

- The rest of input enforcement (the design below).
- An unreadable trace log fails the step instead of counting as no reads,
  and enforcement takes the `Files` listing rather than its own `git
  ls-files`, failing when git fails; the `--enforce` refusals live only in
  the engine, not also in `cli.py`; tests cover each of these failures
  (R18 review).
- A project-root override (`config(root=...)`) for a project whose root is not
  its git top level.
- A cargo-nextest test step that reads the runner's JUnit report into
  `TestResults`.
- The interrupt tests pass when pytest starts in the background: a shell's
  `&` leaves SIGINT ignored, so `raise_signal(SIGINT)` and
  `_thread.interrupt_main()` do nothing, and each test waits out its child's
  60-second sleep (7.5 minutes for the suite). A session fixture installs
  `signal.default_int_handler` and restores the previous handler; child
  sleeps match the watchers' 10-second limit.
- `test_a_server_that_prints_five_megabytes_keeps_under_one` fails in the
  full suite on `bbox` (`assert 2180174 < 1000000`, two runs of two) and
  passes alone; the cause is unconfirmed (`docs/history/builds.md` Facts,
  2026-11-22).
- A build file names modules; every manifest with its own version in a
  module's tree, down to any nested module, follows the module's version or
  has none; vendored manifests can be left out.

## User manual

Guidance taken out of the spec because it follows from the rules.

- For a step to rerun when an environment variable changes, take an `Env`
  input, as in `Bundle(mode=Env("MODE"))`. An unset variable differs from
  an empty one.
- A rebuilt `File` whose content is unchanged leaves its consumers reusable.
- A Python upgrade reruns every step: the code hash covers `abc` and `typing`.
- A requested service keeps the terminal's standard input, so a dev server
  can read keystrokes.
- A failed `Tests` step records nothing, so failures are never replayed.
- A step that is not reusable, such as a service, builds on every run, and
  its consumers can still be reused.
- A `Group` lets one result stand for several steps.

## Input and output kinds not built

- `Dir(path)` input: a directory's tracked content, fingerprinted by git tree
  id.
- `Host(label)` input: the class of machine a step needs.
- `GitCommit` input: a commit SHA, which differs on every commit, so a step
  that takes it never matches an earlier run.
- `Image(tag)` output: a container image a step built.

## Enforcing declared inputs

What R18 leaves out:

- When an enforcement run happens (nightly, or on a change to Roadie
  or a step's code).
- `Files(code=..., data=...)`: separating a step's code from the data
  it checks.
- The restricted tree mode: running a step in a directory holding
  only its declared inputs.
- Marking earlier verdicts unverified when an enforcement run finds
  an undeclared read.
- Tracing on macOS or Windows.
- Tracing services and reads by Roadie's own process.
- Niva's move to `--enforce`, which is Niva's change.
