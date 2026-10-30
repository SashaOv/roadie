# Roadie

Roadie is a build tool. It wires up multi-language, incremental builds in a few lines of Python. Roadie drives the native tools (cargo, pnpm, uv) instead of replacing them.

## Key Features

- Incremental -- only rebuild what needs to be rebuilt.
- Build pipeline is specified in plain Python; the work is done by native tools, if possible.
- Customization is done in plain Python: straightforward and testable.

## Document Conventions
- A message quoted in `code` is exact; `<…>` marks a part filled in.
- Planned work is in separate document: [todo/backlog.md](todo/backlog.md);

## Concepts

A **step** is a unit of work. **Building** a step produces its **value**;
**tearing it down** releases or cleans up the value, if the value is temporary (as in running server or temporary directory). A step's **attributes** are what it
builds from: other steps, its **dependencies**, which build before it;
**inputs**, such as files, which the step reads but Roadie does not build;
and plain values. Each step has a **base directory**, the directory it runs
programs in and resolves its paths from.

Roadie provides these kinds of step: a **file step** builds a file; a file
that belongs in the source tree, such as committed generated code, is a
**generated file**; a **binary step**
builds an executable; a **service** starts a process that runs until the step
is torn down; a **test step** builds test results; a **group** stands for
several steps. **Step writers** add kinds of step and input in Python.

A **build file**, `<directory>/build.py`, is a Python file that constructs steps and
binds them to module variables that can be accessed by name. A
build file can include other build files. Its **settings** set the base
directory and the **environment files**, files of environment variables for the
programs a step runs, of the steps it constructs.

A step's **fingerprint** is a digest of everything its build reads,
including its inputs, its plain values, and each dependency's **output
fingerprint**, a digest of what that dependency built. A step's build reads nothing else, except the process environment and the tools it runs. A build file's **record** keeps, for each of its steps, the fingerprint and value of its last success. A step whose result is recorded can be reused instead of building, if fingerprints match. This is the core of **incremental builds**.

The **plan** of a result is the result and the steps it depends on, directly
or indirectly. A **run** goes through a plan in order, building or reusing
each step, and tears each step down after the last step that takes it. The
`roadie` command loads the **root build file** and runs the plan of one of
its results.
The **project root** of a run holds the records and the **artifacts**, the
files steps build.

## Command line

```
roadie [--dry-run | --dry] [--locked] [--clean] [--base_dir DIR] [--env FILE]... [directory] [result]
```

`directory` (default `.`) holds the root build file. `result` names the
result to run; without it, `roadie` runs the result constructed last. (Note: that is
not the last name assigned: reassigning a name does not move the step it
held). Naming a result the file does not define is an error.

- `--base_dir DIR`: the base directory of every step whose build file's
  settings do not set one.
- `--locked`: fail instead of changing a generated file that git tracks.
- `--clean`: take the lock, delete `<root>/target/roadie/` (records, artifacts,
  and the lock), and exit without building. It exits before a dry run. Another
  run's lock fails it with the message in [One run at a time](#one-run-at-a-time),
  and nothing is deleted.
- `--env FILE`: an environment file for every step; may be repeated.
- `--dry-run`, `--dry`: print the plan, marking the steps that will be reused,
  and build nothing. A step is marked when its dependencies are all marked
  and, if its kind keeps a record, its record matches. It takes no lock; see
  [One run at a time](#one-run-at-a-time).

There is no version option. `roadie --help` prints usage.

## Build files

### Loading a build file

Roadie runs a build file with `runpy.run_path`; the file's directory is not on
`sys.path`. A build file usually starts with `from roadie.build import *`. The
`roadie.build` package holds everything a build file can use, from the
directives `config` and `include` to the step kinds and inputs; its `__all__`
is the complete list.

Loading a build file constructs its steps. An exception in a build file stops execution. 

### Results

A build file that binds no step fails with `<path> defines no results`.

A result's name is the name of the **variable** the result is assigned to. When used in messages, the name is prefixed with `<directory>:` if the build file is not the root build file. If relative file paths used in the build file (most cases), they are resolved relative to the build file in which they are resolved.

### Including build files

`include(directory)` loads `<directory>/build.py`, and returns its results as attributes of a namespace. A file is loaded once per load, however many files include it.

Circular includes of build files are an error. `include` outside a build file that Roadie loads is an error.

### Settings

`config(base_dir=..., env=[...])` sets the settings of the steps constructed
after the call, in the build file that makes it:

- `base_dir`: the base directory, relative to the build file's directory. By
  default a step's base directory is its build file's directory, or the
  directory given to `--base_dir`; `config` overrides both for the steps
  after the call.
- `env`: a list of environment files, relative to the build file's directory.

Settings do not leak to other build files, and are released after the file
loads. An unknown setting fails with `unknown config settings: <names>; known
settings: base_dir, env`; a `base_dir` that is not a path fails with `base_dir
must be a directory path`, and an `env` that is not a list of paths with `env
must be a list of environment files`. `config` outside a loading build file
fails with `config() works only in a build.py that Roadie loads`.

### Environment files

An environment file holds `KEY=VALUE` lines in python-dotenv syntax; keys
without a value are ignored. A step receives the values of its build file's
`config` files, then the `--env` files; later files win. A relative `--env`
path is relative to the working directory.

A file that does not exist fails the run with `environment file does not
exist: <path>` before any step builds, also under `--dry`.

### Project root

The project root of a run is the git top level of the root build file's
directory, or that directory when it is not in a git work tree. A directory
that does not exist fails with `directory does not exist: <path>`. When git
cannot be started, finding the root fails with `cannot run git to find the
project root: <reason>`. `load` and `execute` set the project root; code that
asks for it outside them fails with `no project root: run steps through
roadie's load and execute`.

## Inputs

### Files input

`Files(*paths)` names files and directories, relative to the step's base
directory. A directory stands for every file under it, recursively,
git-ignored files included. Symbolic links are followed, and a directory
reached by several links counts once; empty directories do not count. The fingerprint covers each file's path, relative
to the project root when inside it, and the SHA-256 of its content; a path
that does not exist counts as missing, so creating it changes the
fingerprint.

### Cargo packages

`CargoPackage(name)` names a Cargo package of the workspace found from the
step's base directory with `cargo metadata`. Its fingerprint covers:

- the output of `rustc --version`;
- the `RUSTFLAGS`, `RUSTDOCFLAGS`, `CARGO_ENCODED_RUSTFLAGS`,
  `CARGO_PROFILE_*` and `CARGO_BUILD_*` variables of the process environment;
- the content of every file git lists, tracked or untracked and not ignored,
  under the package and under the workspace packages it depends on, directly
  or indirectly;
- the content of the workspace's `Cargo.toml`, `Cargo.lock`,
  `rust-toolchain.toml` and `.cargo/config.toml`, where they exist.

Changes to unrelated packages, and to `CARGO_TARGET_DIR`, do not change it.
A package the workspace does not contain fails with `cargo package '<name>'
is not in the workspace`. Within one run, `cargo metadata` and
`rustc --version` run once per workspace.

### Cargo workspace queries

`cargo_rust_packages(directory)` returns the workspace root and
its packages: each with its name, its directory relative to the root, and the
workspace packages it depends on.
`select_rust_packages(packages, changed_paths)` returns the names
of the packages that contain a changed path, and of every package that
depends on them; a change to the root `Cargo.toml` or `Cargo.lock` selects
all.

## Steps

### Step kinds

Roadie's step kinds are the step classes of `roadie.build`. Each
class's docstring specifies its kind: its value, how it is recorded and
reused, and its output fingerprint.

### Generated files

A file step with `output` publishes its built file after it builds: the
destination is `output` resolved from the step's base directory, and the
step's value becomes the destination. A destination whose content already
equals the built file is left alone. Otherwise Roadie writes it and prints
`updated <destination>`, creating its directories. A reused step does not
publish.

Under `--locked`, a destination that git tracks and that would change is left
unwritten: Roadie prints a unified diff and fails with `generated file is
stale: <destination>`. An untracked destination is written. If git cannot say
whether the destination is tracked, the step fails with `Cannot check
whether <destination> is tracked: <git's message>`.

### Deferred steps

A step of a deferred kind, such as a service, is built late. One that is not
the requested result is built just before the first step that takes it and
must build, so a plan whose consumers are all reused does not build it; it
then prints `not started <name>: no step that takes it runs`. Its output
fingerprint is its own fingerprint, and its dependencies stay live until its
last consumer.

## Running a plan

### Plan order

A plan holds each step once, each after the steps it depends on, in the order
of the attributes that name them, and ends with the requested result. Steps
that depend on each other fail with `steps depend on each other: <a> -> <b>
-> <a>`.

### Teardown

A step that no step takes is torn down right after it. Steps due at the same
point are torn down in reverse build order. A reused step is torn down with
its restored value.

When a step fails, or the run is interrupted by Ctrl-C or SIGTERM, every step
still live is torn down in reverse build order. A teardown that fails does
not stop the others from being torn down. The first teardown failure fails
the run with `<name> failed: teardown: <reason>` and the others are printed
to standard error; when the run is already failing, all are printed and the
original failure stands.

### Failures

An exception raised while Roadie fingerprints, builds, publishes, reports,
records or serves a step fails the run with `<name> failed: <reason>`, where `<name>` is the step's name, or its class name when
it has none, and `<reason>` is the exception's message, or its class name
when the message is empty. A string passed to `SystemExit` is its own reason.
An interrupt is not a failure. The command prints a `RoadieError` message to
standard error and exits with status 1.

### One run at a time

A run takes `<root>/target/roadie/run.lock`, created exclusively, and writes
its PID and start time. Another run fails with `another roadie run holds
<lock> (pid <n>, started <time>)`. A lock whose process is gone, which is
older than 24 hours, or which cannot be read, is stale and taken over. The
lock is released before a requested result is served, so a running dev server
does not block builds. `--dry` takes no lock.

### Requested result

The requested result is built in plan order, even when deferred. After it
builds or is reused, its kind may serve it before the run tears it down; a
service, for one, waits on its process.

### Reusing a step

A step is reusable when it is bound to a variable of a build file and its
kind keeps a record and allows reuse. Roadie reuses a reusable step when its
record has the same fingerprint and the step's kind accepts the recorded
value. It prints `reused <name>`, and the step's value is restored from the
record.

Before a step that keeps a record builds, Roadie prints
`build <name> = <Class>(<arguments>)`, or `build <Class>(<arguments>)` when
the step has no name, with the same arguments `--dry` shows. A deferred step
prints that line when it starts. A reused step prints only `reused <name>`.
A step that keeps no record, such as a group, prints no build line.

## Customization

### Dependencies and attributes

A step is an instance of a `Step` subclass. `Step.build(run)` builds the step
and returns its value; `Step.teardown(value)` tears it down and does nothing
by default. When constructed, a step records its base directory, the
directory of the build file loading it, and the environment files of its
settings.

An attribute that is a step, or a tuple holding steps, makes those steps
dependencies. An attribute that is an input, or a tuple holding inputs, is
fingerprinted. The remaining attributes must be plain values: `None`, `str`,
`int`, `float`, `bool`, a `Path`, or a tuple of them. Any other attribute
fails with `<step> attribute <name> must be a plain value, an input, or a
step`, when the step is fingerprinted or shown by `--dry`.

### Step values

`run[step]` returns the value of a dependency built or restored earlier in
the run. A step the run has not built, or has already torn down, fails with
`<name> is not available in this run`. `run.artifact(step, suffix)` returns
a path for an artifact of the step, unique to the step and the suffix, and
creates its parent directory.

### Step helpers

`Step` provides helpers that run a program in the step's base directory with
its environment files: `_cmd` runs it to completion, and `_start_cmd` starts
a server and returns a `RunningProcess` once it is ready. Their docstrings
specify them.

Stopping a server signals its process group and every descendant. On Linux
the descendants come from `/proc`; on macOS, where `/proc` is absent, from
`ps -A -o pid=,ppid=`. A `ps` that cannot be run leaves the group signal as
the only one. Windows signals the process alone, which is unverified until
the Windows CI job in the backlog.

### Step hooks

A step writer subclasses `Step` or one of its subclasses, and
overrides what its kind needs. Roadie calls:

- `publish(run, value)` after the step builds, not when it is reused; its
  result becomes the step's value. By default it returns `value`.
- `report(value)` after the step builds or is reused; raising fails the step.
- `serve(value)` on the requested result, before the run tears it down.
- `encode(value)` for the JSON value the record keeps, `matches(recorded)` to
  accept a recorded value for reuse, and `decode(recorded)` to restore the
  value from it. By default both use a Pydantic `TypeAdapter` for the step's
  value type `T`, so a tuple comes back as a tuple and a dictionary key comes
  back as its declared type. A step class with no concrete `T` keeps the JSON
  value as decoded. A recorded value that is not a valid `T` fails the step.
- `output_fingerprint(fingerprint, value)` for the step's output fingerprint;
  by default a digest of the value.
- `call_arguments()` for the positional and keyword arguments `--dry` shows;
  by default every attribute that is not `None`, as `name=value`. A group
  shows its members positionally.

A step class sets these class attributes:

- `reusable = False`: the step is never reused.
- `recorded = False`: the kind keeps no record; the step's output fingerprint
  is its own fingerprint.
- `deferred = True`: the kind is deferred.

### Input fingerprints

An input is an instance of a subclass of `Input`, which
implements `fingerprint(base_dir) -> str`: a digest of what it names, seen
from the step's base directory. `run_state` keeps an input's
state for the length of a run; its docstring specifies it.

## Implementation

### Packages

`roadie.build` is the build-file API. `roadie.engine` loads and runs build
files and defines the extension points, `Step` and `Input`; it never imports
`roadie.build` or `roadie.cli`. `roadie.cli` is the command.

### Step fingerprints

A step's fingerprint is a SHA-256 digest of:

- its class, by module and qualified name; a class defined in a build file
  is `roadie:<path relative to the project root>.<qualname>`;
- a hash of the source files of the modules that define the classes of the
  step's class hierarchy, and of Roadie's own modules, each keyed by its
  module name. The hierarchy includes standard-library modules (`abc`,
  `typing`). A class defined in a build file itself is not hashed;
- its base directory, relative to the project root;
- the values of its environment files;
- its plain attributes; a `Path` inside the project root counts as its
  relative path in POSIX form;
- the fingerprints of its inputs;
- the output fingerprints of its dependencies.

### Dry run output

`--dry` prints one line per step in plan order:
`<name> = <Class>(<arguments>)  # reuse` for a marked step, `# run` for any
other, without `<name> = ` for a step with no name. `<arguments>` are the
step's call arguments: by default every attribute that is not `None`, as
`name=value`. A group shows its members positionally. A step argument is
shown by its name, or as its own call when it has none.

### Records

A build file's record is `<root>/target/roadie/<directory>/build.record.json`,
where `<directory>` is the build file's directory relative to the project
root (none for the root itself; `_outside/<12 hex digits of a digest of the
path>` when outside it). The file holds a format version and its entries,
both read through a Pydantic model. Each entry maps a reusable variable to
its step fingerprint, its output fingerprint, and its encoded value. A record
of another version counts as empty, so its steps rebuild. A record that
cannot be read counts as empty too, and Roadie prints `ignoring unreadable
record <path>: <reason>`, so a damaged file never fails the run.

A successful step replaces its entry; entries of variables the build file no
longer defines are dropped. A failed step writes nothing. The file is
written through a temporary file and renamed.

### Artifacts

`run.artifact(step, suffix)` returns
`<root>/target/roadie/<directory>/<variable><suffix>`, with `<directory>` as
for the step's record. A step without a variable uses
`<ClassName>-<serial>`, a number unique to the step within the process.

After a successful run, while the run still holds its lock, Roadie reads
`<root>/target/roadie/swept`. When the timestamp is missing, Roadie writes it
and does not sweep. When it is missing a timezone, cannot be read, or is
older than 24 hours, Roadie sweeps and then writes it. A failed run does not
sweep. The sweep deletes each artifact whose stem is not a variable in that
directory's record, which includes every artifact of an unnamed step, and
deletes each record directory whose build file no longer exists. The root
directory is kept, along with the lock and the timestamp; only its record
and artifacts go. A directory under `_outside` is kept, because the sweep
cannot recover the build file's path; unreferenced artifacts there are still
deleted.
