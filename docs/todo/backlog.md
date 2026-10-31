# Backlog

Planned work that [the spec](../spec.md) does not describe. Each item is one
bullet, except the input-enforcement design, which is moved whole.

## Features

- A `Check` or `Pytest` takes a step in `inputs`: the step is a dependency, and
  its output fingerprint is part of the check's.
- `AuthorTask`: a `Tests` step that waits for the author to run checks and
  report the results.
- `AuthorApproval`: an author task that continues the run or cancels it.
- Release process can take Rust and JS manifests as sources
- `Release` takes a custom commit message, once steps take parameters.
- Enforce declared inputs (the design below).
- A project-root override (`config(root=...)`) for a project whose root is not
  its git top level.
- Reuse unnamed steps: an inline or loop-built step takes a derived identity,
  its consumer's variable and the path to it (`copy.source`, `checks[2]`).
- A cargo-nextest test step that reads the runner's JUnit report into
  `TestResults`.
- A Windows CI job, then stop a Windows process tree (`taskkill /T`) and
  replace the `SIGKILL` that Windows lacks.
## User manual

Guidance taken out of the spec because it follows from the rules.

- For a step to rerun when an environment variable changes, take an `Env`
  input, as in `Bundle(mode=Env("MODE"))`. An unset variable differs from
  an empty one.
- A rebuilt `File` whose content is unchanged leaves its consumers reusable.
- A Python upgrade reruns every step: the code hash covers `abc` and `typing`.
- An inline step, as in `Copy(Emitter())`, is never reused; bind it to a
  variable.
- A requested service keeps the terminal's standard input, so a dev server
  can read keystrokes.
- A failed `Tests` step records nothing, so failures are never replayed.
- A step that is not reusable, such as a service, builds on every run, and
  its consumers can still be reused.
- A `Group` lets one result stand for several steps.

## Input and output kinds not built

- `Dir(path)` input: a directory's tracked content, fingerprinted by git tree
  id.
- `Toolchain` input: the pinned toolchain file and the compiler version it
  resolves to.
- `NodeEnv` input: the `package.json` files, the pnpm lockfile and the Node
  version.
- `Host(label)` input: the class of machine a step needs.
- `GitCommit` input: a commit SHA, which differs on every commit, so a step
  that takes it never matches an earlier run.
- `Dir(path)` output: a directory a step produced, fingerprinted from its
  content.
- `Image(tag)` output: a container image a step built.
- `Log(path)` output: a log file a step wrote.

## Enforcing declared inputs

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
