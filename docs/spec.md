# Roadie

## Goal

A step that does not need to run does not run. Roadie knows each step's
inputs, so when they are unchanged since the step last succeeded, it reuses
that result instead of running the step; a step whose result cannot be
reused, such as a fuzzing run, says so. Everything else in this spec serves
that goal.

**Roadie**: "Wire up multi-language, incremental builds in a
few lines of Python." Like a roadie on stage, it sets up and connects the
native tools (cargo, pnpm, uv) and lets them play, instead of replacing
them.

## Concepts

Roadie defines and executes graphs of steps.

- Job: a directed acyclic graph of connected steps.
- Step: a class whose `build(run)` produces a value and whose
  `teardown(value)` releases it. Attributes that are steps declare dependencies.
- Run: one execution of a plan. `run[step]` is a dependency's value;
  `run.artifact(step, suffix)` names a file under `target/roadie/`.
- Result: the value a step produces. Kinds: `Binary` and `File` produce
  paths, `Service` produces a running deployment, and `Tests` produces
  `TestResults` containing failing test names and messages plus log messages.
  Any failing test fails its step.

The executor builds dependencies first and tears down each step immediately
following its last consumer in the plan. On failure, SIGTERM or Ctrl-C it
tears down the remaining steps in reverse order. A failed step names itself
and its reason; a command failure includes the command, exit status and end
of stderr.

`CargoPackage(name)` names one crate by its Cargo name. Its input set is
the crate's local dependency closure.
A binary is fingerprinted from its inputs, not its bytes.

## User experience

```
roadie [--dry-run | --dry] [--locked] [--base_dir DIR] [directory] [result]
```

- A build file is `<directory>/build.py`: Python step instances assigned to
  variables. It starts with `from roadie import *`.
- Loading a build file constructs its steps and runs none. Roadie loads it
  with `runpy`, so the file's directory is not on `sys.path`.
- `result` names a variable of the build file. Without it, Roadie selects the
  step instantiated last. Reassigning a name does not move its original step.
- `include(directory)` loads `<directory>/build.py`, relative to the
  including file, once per load, and returns its steps by variable name.
  Included steps are named `<directory>:<name>`.
- A result needs only its dependencies: `api` builds the server and generates
  the contract without deploying it. `--dry-run` (`--dry`) prints the plan
  and runs none.
- Commands belong in step classes and Roadie utilities. `Step._cmd` echoes
  and runs a command; `Step._start_cmd` starts a process, waits for its port,
  and returns a `RunningProcess` to stop at teardown.

## Enforcing declared inputs

Planned; not implemented.

A missing declaration has one of two effects:

- An input the step receives through its signature (a path, a value, an
  environment variable, an earlier step's Output) is simply absent, and the
  step fails.
- An input the step finds in the working tree is still on disk, so the step
  passes and the fingerprint misses it. A later run replays a stale verdict.

`Files` separates a step's code (its tests and scripts) from the data it
checks: `Files(code=..., data=...)`. Undeclared reads enter through code
changes, such as a new test that opens a new file.

Enforcement runs, with the cache off, in two situations:

- nightly, for every step;
- on a change to Roadie or to the step definitions (all steps), or to a
  step's code inputs (that step).

An enforcement run uses either of two modes:

- Restricted tree: the step runs in a directory holding only its declared
  inputs, its Outputs-as-Inputs, and its toolchain environment. It catches a
  read of a named file. A step that enumerates files can pass on the smaller
  tree.
- Read tracing: the step runs in the full checkout under a file-access
  tracer, and any read outside its declared inputs fails it. It catches
  enumeration too. Linux only for now.

When an enforcement run finds an undeclared read, it fails and names the step
and the file. Every cached verdict for that step since the last clean
enforcement run becomes unverified, so the next run executes the step again.
Between enforcement runs, a missing declaration can replay a stale verdict
for that one step for at most a day.

## Backlog

- `AuthorTask`: a `Tests` step that waits for the author to run checks and
  report the results.
- `AuthorApproval`: an author task that continues the run or cancels it.
- Enforce declared inputs (the design above).
- A project-root override (`config(root=...)`) when a project's root is not
  its git top level.
