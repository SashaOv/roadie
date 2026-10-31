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

### Steps and values

A **step** is a unit of work. **Building** a step produces its **value**;
**tearing it down** releases or cleans up the value, if the value is temporary (as in running server or temporary directory). A step's **attributes** are what it
builds from: other steps, its **dependencies**, which build before it;
**inputs**, such as files, which the step reads but Roadie does not build;
and plain values. Each step has a **base directory**, the directory it runs
programs in and resolves its paths from.

### Kinds of step

Roadie provides these kinds of step: a **file step** builds a file; a file
that belongs in the source tree, such as committed generated code, is a
**generated file**; a **binary step**
builds an executable; a **service** starts a process that runs until the step
is torn down; a **test step** builds test results; a **group** stands for
several steps. **Step writers** add kinds of step and input in Python.

### Build files and settings

A **build file**, `<directory>/build.py`, is a Python file that constructs steps and
binds them to module variables that can be accessed by name. A
build file can include other build files. Its **settings** set the base
directory and the **environment files**, files of environment variables for the
programs a step runs, of the steps it constructs.

### Fingerprints and records

A step's **fingerprint** is a digest of everything its build reads,
including its inputs, its plain values, and each dependency's **output
fingerprint**, a digest of what that dependency built. A step's build reads nothing else, except the process environment and the tools it runs. A build file's **record** keeps, for each of its steps, the fingerprint and value of its last success. A step whose result is recorded can be reused instead of building, if fingerprints match. This is the core of **incremental builds**.

### Plans and runs

The **plan** of a result is the result and the steps it depends on, directly
or indirectly. A **run** builds or reuses a step once every step it takes is
done, several steps at once, and tears each step down after every step that
takes it has finished. The `roadie` command loads the **root build file** and
runs the plan of one of its results.

### Project root

The **project root** of a run is where Roadie keeps its state: the records,
the **artifacts** (the files steps build) and the run lock, under
`target/roadie/`. Paths inside it are fingerprinted relative to it, so a
moved checkout keeps its records. It is the git top level of the root build
file's directory, or that directory when it is not in a git work tree, so
every build file in a repository shares one state. When git cannot be
started, the run fails with `cannot run git to find the project root:
<reason>`.

## Command line

```
roadie [--dry-run | --dry] [--locked] [--clean] [--jobs N] [--base-dir DIR] [--env FILE]... [-f PATH] [name=value]... [result [name=value]...]...
```

`-f PATH`, `--file PATH` names the root build file. A file is that build
file; a directory means the `build.py` inside it. The default is `.`. A path
that does not exist fails with `build file does not exist: <path>`.

A word containing `=` is a parameter: the name is the text before the first
`=`, and the value is the rest, which may be empty. A name that is not an
identifier fails with `parameter name must be an identifier: <word>`. The
same name given twice in one place fails with `parameter <name> is given
twice`. A parameter before every result is run-wide. A parameter after a
result belongs to that result, overrides a run-wide value for that step
only, and does not reach the result's dependencies. Before any step builds,
including under `--dry`, a run-wide name no step takes fails with `unknown
parameter <name>; the plan takes: <names>`, or `the plan takes no parameters`
when nothing in the plan takes one. A result's own name that result does not
take fails with `<result> takes no parameter <name>; it takes: <names>`, or
`<result> takes no parameters`. A required parameter a step does not receive
fails with `<step> needs parameter <name>`. See [Parameters](#parameters).

Each other word names a result. The run builds each, in the order given,
over the union of their plans, and a step shared by two plans is built once.
A requested result whose class sets `once_per_run` waits until every step
that does not depend on it has finished, so a result that keeps the terminal
is last. With no word, `roadie` runs the default result. `default_step(step)`
returns `step` and marks it as that result; only the root build file's mark
counts, and an included file's mark is ignored. Marking two results fails
with `<path> marks more than one default result: <a>, <b>`. Marking a step
that no variable of the file holds fails with `<path>: the default result is
not bound to a variable`. Calling it outside a loading build file fails with
`default_step() works only in a build.py that Roadie loads`. Without a mark,
`roadie` runs the result constructed last. (Note: that is not the last name
assigned: reassigning a name does not move the step it held).

`setup(step)` returns `step` and marks it so every run builds it, after its
own plan and before any other step, whichever results the run builds, also
under `--dry`. A build file marks at most one, and the step is bound to a
variable. A second mark fails with `<path> marks more than one setup step:
<names>`. A step no variable holds fails with `<path>: the setup step is not
bound to a variable`. Calling it outside a loading build file fails with
`setup() works only in a build.py that Roadie loads`. A mark in an included
file counts, and a file's setup step builds after the setup steps of the
files it includes. A failed setup step stops the run before any other step
builds. Reuse is unchanged.

A word that
names no result and is an existing directory fails with `<path> has no result
named '<word>'; to use the build file in <word>, pass -f <word>`. Naming any
other missing result is an error that lists the file's results.

A step class sets `once_per_run = True` when it may be requested once per
run. `Service` sets it. Requesting two such results fails with `<a> and <b>
can each be requested only once per run; request one of them`.

- `--base-dir DIR`: the base directory of every step whose build file's
  settings do not set one. `--base_dir` is the same option and is omitted
  from `--help`.
- `--locked`: fail instead of changing a generated file that git tracks.
- `--clean`: take the lock, delete everything under `<root>/target/roadie/`
  except `run.lock`, and exit without building. It exits before a dry run.
  When another run holds the lock, it waits as in
  [One run at a time](#one-run-at-a-time), and nothing is deleted until this
  run holds the lock.
- `--env FILE`: an environment file for every step; may be repeated.
- `--jobs N`, `-j N`: run up to N steps at once. The default is the number
  of CPUs Roadie may use, or 1 when that count is unavailable; see
  [Parallel steps](#parallel-steps). A value below 1 fails with `--jobs must
  be at least 1`. Output is prefixed when N is above 1, as described there.
- `--dry-run`, `--dry`: walk the plan through the same scheduler as a run:
  environment files, fingerprints, record matching, and deferred steps. It
  marks each step the way that run reports it: `# reuse` where the run
  prints `reused <name>`, `# not started` where it prints `not started
  <name>`, and `# run` otherwise. Where the run would build a step or start
  a deferred one, the dry run keeps a placeholder output fingerprint
  instead. It takes the lock like a run, writes no records, and does not
  publish, serve, sweep, or tear down. See [One run at a time](#one-run-at-a-time).

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

`include(path)` loads a build file and returns a step that stands for that
file's default result, as a group of that one step. A file is that build
file; a directory means the `build.py` inside it. The file's results are
attributes of the step (`lib.test`) and are not its dependencies, so
`Group(lib, app)` runs each file's default, and `roadie lib` runs `lib`'s.
A file is loaded once per load, however many files include it, and naming
the file or its directory loads it once.
A plan that holds the step fails, before any step builds, with `<path> marks
no default result` when the file has no `default_step` mark. A result named
like a member of `Step` fails at include with `<path>: result <name> is
reserved by Roadie in an included file`.

Circular includes of build files are an error. `include` outside a build file that Roadie loads is an error.

### Settings

`config(base_dir=..., env=[...])` sets the settings of the steps constructed
after the call, in the build file that makes it:

- `base_dir`: the base directory, relative to the build file's directory. By
  default a step's base directory is its build file's directory, or the
  directory given to `--base-dir`; `config` overrides both for the steps
  after the call.
- `env`: one environment file, or several, relative to the build file's
  directory.

Settings do not leak to other build files, and are released after the file
loads. An unknown setting fails with `unknown config settings: <names>; known
settings: base_dir, env`; a `base_dir` that is not a path fails with `base_dir
must be a directory path`, and an `env` value of the wrong kind fails with
`config env takes paths: got <type>`. `config` outside a loading build file
fails with `config() works only in a build.py that Roadie loads`.

### Environment files

An environment file holds `KEY=VALUE` lines in python-dotenv syntax; keys
without a value are ignored. A step receives the values of its build file's
`config` files, then the `--env` files; later files win. A relative `--env`
path is relative to the working directory.

A file that does not exist fails the run with `environment file does not
exist: <path>` before any step builds, also under `--dry`.

## Inputs

### Files input

`Files(*paths)` names files and directories, relative to the step's base
directory. In a git work tree, a directory stands for the files
`git ls-files --cached --others --exclude-standard` lists under it. Outside
a work tree, a directory stands for every file under it, recursively:
symbolic links are followed, a directory reached by several links counts
once, and empty directories do not count. A file named explicitly counts
even when it is ignored. A directory that cannot be read fails with
`cannot read <path>: <reason>`; under git, `<reason>` is the warning
`git ls-files` prints, because git skips an unreadable directory and exits
0. The fingerprint covers each file's path, relative to the project root
when inside it, and the SHA-256 of its content. A path that does not exist
counts as missing, so creating it changes the fingerprint.

### Cargo packages

`CargoPackage(name)` names a Cargo package of the workspace found from the
step's base directory with `cargo metadata`. Its fingerprint covers:

- the output of `rustc --version`;
- the `RUSTFLAGS`, `RUSTDOCFLAGS`, `CARGO_ENCODED_RUSTFLAGS`,
  `CARGO_PROFILE_*` and `CARGO_BUILD_*` variables of the process environment;
- the content of every file git lists, tracked or untracked and not ignored,
  under the package and under the workspace packages it depends on, directly
  or indirectly, using the same listing as a `Files` directory; an
  unreadable directory fails with `cannot read <path>: <reason>`;
- the content of the workspace's `Cargo.toml`, `Cargo.lock`,
  `rust-toolchain.toml` and `.cargo/config.toml`, where they exist.

Changes to unrelated packages, and to `CARGO_TARGET_DIR`, do not change it.
A package the workspace does not contain fails with `cargo package '<name>'
is not in the workspace`. Within one run, `cargo metadata` and
`rustc --version` run once per workspace.

### Python environment

`PythonEnv()` fingerprints the version of the interpreter running Roadie and
the name and version of every distribution installed in its environment.

### Environment variables

`Env(*names)` fingerprints the value of each named process environment
variable, in the order given. An unset variable differs from an empty one.
No names fails with `Env takes at least one variable name`. A name that is
not a string fails with `Env takes variable names: got <type>`.

### Parameters

`Param(name, default=...)` is an input. `name` must be an identifier;
otherwise construction fails with `Param takes an identifier: got <repr>`.
`default`, when given, is a string or `None`; another kind fails with
`Param default takes a string or None: got <type>`. Without `default` the
parameter is required.

A step's value of a `Param` is its own parameter, when the command gave that
result one, else the run-wide value, else that `Param`'s default. Each
`Param` keeps its own default. Inside `build`, `self.<attr>.value` is that
string, or `None` when an optional parameter was not set. The value lasts
for the run. Outside a run, reading it fails with `parameter <name> has no
value outside a run`. The step's fingerprint covers each of its parameters'
values. An unset optional parameter differs from an empty one.

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
whether the destination is tracked, the step fails with `cannot check
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

### Parallel steps

A run builds up to `--jobs` steps at once. `--jobs` defaults to the number of
CPUs Roadie may use (`os.process_cpu_count()`), or 1 when that count is
unavailable. With `--jobs` above 1, each line a step prints, and each line of
output from the programs its `_cmd` and `_start_cmd` run, is printed as it
arrives, prefixed with `[<name>] `, where `<name>` is the step's name or its
class name when it has none, and lines of different steps are not mixed. With
`--jobs` 1, output is unprefixed.

### Teardown

A step is torn down after every step that takes it has finished. A step that
no step takes is torn down right after it. Steps due at the same point are
torn down in reverse build order. A reused step is torn down with its
restored value.

When a step fails, or the run is interrupted by Ctrl-C or SIGTERM, every step
still live is torn down in reverse build order. Ctrl-C and SIGTERM stop the
programs the steps are running, then wait for those steps to finish. A
teardown that fails does not stop the others from being torn down. The first teardown failure fails
the run with `<name> failed: teardown: <reason>` and the others are printed
to standard error; when the run is already failing, all are printed and the
original failure stands.

### Failures

An exception raised while Roadie fingerprints, builds, publishes, reports,
records or serves a step fails the run with `<name> failed: <reason>`, where `<name>` is the step's name, or its class name when
it has none, and `<reason>` is the exception's message, or its class name
when the message is empty. A string passed to `SystemExit` is its own reason.
An interrupt is not a failure. When a step fails, no new step starts. Roadie
lets the steps still running finish, then fails the run with the first
failure. When any are still running it prints `waiting for <names>` once,
naming them. Later failures are printed to standard error. Steps that finish
are recorded. The command prints a `RoadieError` message to standard error
and exits with status 1.

### One run at a time

A run takes an operating-system lock on `<root>/target/roadie/run.lock`
and writes its PID and start time into that file. A run, a dry run, or
`--clean` that finds the lock held prints `waiting for another roadie run:
<lock> (pid <n>, started <time>)` once, without the parenthesis when the
file cannot be read yet, and blocks until the holder releases it. The
operating system releases the lock when the holder exits, including when it
is killed. Releasing the lock unlocks the file and leaves it in place.
A run sets `ROADIE_RUN_PID` to its PID in the environment of the programs
its steps run. A run that finds the lock held by that PID fails at once
with `<lock> is held by the roadie run that started this one (pid <n>)`
instead of waiting.
`--clean` deletes everything under `target/roadie/` except `run.lock`. The
lock is released before a requested result is served, so a running dev server
does not block builds.

### Requested result

The requested result is built in plan order, even when deferred. After it
builds or is reused, its kind may serve it before the run tears it down; a
service, for one, waits on its process. A run may request several results.
It builds the union of their plans, each shared step once, and serves each
requested result whose kind serves. A class that sets `once_per_run`, as
`Service` does, may be requested once in that run.

### Reusing a step

A step is reusable when it is bound to a variable of a build file and its
kind keeps a record and allows reuse. Roadie reuses a reusable step when its
record has the same fingerprint and the step's kind accepts the recorded
value. It prints `reused <name>`, and the step's value is restored from the
record.

Before a step that keeps a record builds, Roadie prints
`build <name> = <Class>(<arguments>)`, or `build <Class>(<arguments>)` when
the step has no name, with the same arguments `--dry` shows. A deferred step
prints that line when it starts. After that step finishes building, Roadie
prints `built <name> in <m:ss>`, or `built <Class> in <m:ss>` when the step
has no name. `<m:ss>` is whole minutes and two digits of seconds, rounded
down. A deferred step's duration is its start. With `--jobs` above 1 the
`built` line is prefixed like the step's other lines. A reused step prints
only `reused <name>`. A step that keeps no record, such as a group, prints
no build line. A reused step, a step that keeps no record, and a failed step
print no `built` line. A run that succeeds prints `finished in <m:ss>`,
unprefixed, as its last line, or before it serves the requested result. A
failed run and a dry run print none.

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
step`, when the step is fingerprinted or shown by `--dry`. An attribute
whose name Roadie keeps for itself — its bookkeeping, such as `name`,
`base_dir` and `env_values`, its hooks, such as `report`, and its class
attributes `reusable`, `recorded` and `deferred` — fails with `<step>
attribute <name> is reserved by Roadie`, at the same point. A subclass may
still override those class attributes and hooks.

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
  `typing`). A class defined in a build file adds that file, keyed by
  `roadie:<path relative to the project root>`;
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
