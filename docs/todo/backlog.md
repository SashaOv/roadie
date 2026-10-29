# Backlog

Planned work that [the spec](../spec.md) does not describe. Each item is one
bullet, except the input-enforcement design, which is moved whole.

## Features

- `AuthorTask`: a `Tests` step that waits for the author to run checks and
  report the results.
- `AuthorApproval`: an author task that continues the run or cancels it.
- Enforce declared inputs (the design below).
- Hash step classes defined in a build file, so editing one reruns its steps;
  today only classes in imported modules are hashed.
- A project-root override (`config(root=...)`) for a project whose root is not
  its git top level.
- `Env(*names)` input: the values of the named process environment variables.
- `Files` follows git: a directory expands to the files git lists, skipping
  ignored ones; a path named explicitly counts even when ignored, so an ignored
  build output can still be an input.
- Reuse unnamed steps: an inline or loop-built step takes a derived identity,
  its consumer's variable and the path to it (`copy.source`, `checks[2]`).
- Test steps for pytest and cargo-nextest that read the runner's JUnit report
  into `TestResults`.
- A Windows CI job, then stop a Windows process tree (`taskkill /T`) and
  replace the `SIGKILL` that Windows lacks.
- Rename `--base_dir` to `--base-dir`, keeping `--base_dir` as a hidden alias
  for one release.

## Gaps

Code that departs from the spec or from general engineering practice.

- Roadie's bookkeeping shares the step's attributes: it stores `name`,
  `variable`, `serial`, `base_dir`, `build_dir`, `build_variables`,
  `env_files` and `env_values` on the step and leaves them out of the
  fingerprint and `--dry`. A step attribute with one of those names overwrites
  Roadie's value and is not fingerprinted. The spec reserves no names. The
  step hooks (`publish`, `report`, `serve`, `encode`, `matches`, `decode`,
  `output_fingerprint`, `call_arguments`) and the class attributes
  `reusable`, `recorded` and `deferred` share the same namespace: a dataclass
  field named `report` hides the hook.
- `Files` skips a directory it cannot read without failing, so the
  directory's files silently drop out of the fingerprint.
- Ctrl-C or SIGTERM during a build prints a Python traceback.
- Without `git` installed, finding the project root prints a Python traceback
  instead of a message.
- `Cannot check whether …` is capitalized; every other message is not.

## User manual

Guidance taken out of the spec because it follows from the rules.

- For a step to rerun when an environment variable changes, the build file
  reads it and passes it as a plain attribute, as in
  `Bundle(mode=os.environ.get("MODE", "dev"))`.
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

## Tests to add

- Input fingerprints: a step whose `Input` subclass changes its digest gets a
  new fingerprint.
- Results: an unknown result name fails with the file and its result names.
- Including build files: `include` outside a loading build file fails.
- Binaries: cargo reporting several different executables fails the step.
- Cargo packages: a package the workspace does not contain fails.
- Cargo workspace queries: a changed root `Cargo.toml` or `Cargo.lock` selects
  every package.
- Services: `HttpService` waits on `/` unless it sets `health_path`.

## Input and output kinds not built

- `Dir(path)` input: a directory's tracked content, fingerprinted by git tree
  id.
- `Toolchain` input: the pinned toolchain file and the compiler version it
  resolves to.
- `PythonEnv` input: the Python lockfile and the interpreter version.
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
