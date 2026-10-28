# Roadie

## Goal

A step that does not need to run does not run. Roadie knows each step's
inputs, so when they are unchanged since the step last succeeded, it reuses
that result instead of running the step; a step whose result cannot be
reused, such as a running service, says so. Everything else in this spec
serves that goal.

Roadie wires up multi-language, incremental builds in a few lines of Python.
Like a roadie on stage, it sets up and connects the native tools (cargo,
pnpm, uv) and lets them play, instead of replacing them.

Planned work is in [todo/future.md](todo/future.md).

## Build files

### Loading a build file

A build file is `<directory>/build.py`: Python that constructs steps and binds
them to names. Roadie runs it with `runpy.run_path`; the file's directory is
not on `sys.path`. A build file starts with `from roadie import *`, which
provides `Binary`, `CargoPackage`, `Deployment`, `File`, `Files`, `Group`,
`HttpService`, `RoadieError`, `RustBinary`, `Service`, `Step`, `TestFailure`,
`TestResults`, `Tests`, `config` and `include`.

Loading a build file constructs its steps and builds none. An exception a
build file raises while loading propagates unchanged. A directory without a
`build.py` fails with `<directory> has no build.py`.

### Results

Every module-level name of a build file that is bound to a step is a
**result**. A build file that binds no step fails with `<path> defines no
results`.

`roadie` runs one result: the one the command names, or else the step
constructed last, which is not the last name assigned. Reassigning a name
does not move the step it held. Naming a result the file does not define
fails with the file and its result names.

A result's **variable** is the name the file that defined it binds it to (the
first, when it binds several); its **name**, used in messages, is the variable
with the prefix of [Including build files](#including-build-files).

### Including build files

`include(directory)` loads `<directory>/build.py`, relative to the including
file, and returns its results as attributes of a namespace. A file is loaded
once per load, however many files include it. An included step keeps the
variable of the file that defined it and is named `<directory>:<variable>`,
with `<directory>` relative to the directory given to `roadie` in POSIX form,
or absolute when outside it.

Build files that include each other fail with `build files include each
other: <a> -> <b> -> <a>`. `include` outside a loading build file fails with
`include() works only in a build.py that Roadie loads`.

### Settings

`config(base_dir=..., env=[...])` changes the settings of the steps
constructed after the call, in the build file that makes it:

- `base_dir`: the directory a step's commands run in and its paths resolve
  from, relative to the build file's directory. By default a step's base
  directory is its build file's directory, or the directory given to
  `--base_dir`, which applies to every build file of the run; `config` still
  overrides it for the steps after the call.
- `env`: a list of [environment files](#environment-files), relative to the
  build file's directory.

Settings do not leak to other build files, and are released after the file
loads. An unknown setting fails with `unknown config settings: <names>; known
settings: base_dir, env`; a `base_dir` that is not a path fails with `base_dir
must be a directory path`, and an `env` that is not a list of paths with `env
must be a list of environment files`. `config` outside a loading build file
fails with `config() works only in a build.py that Roadie loads`.

### Project root

The **project root** of a run is the git top level of the directory given to
`roadie`, or that directory when it is not in a git work tree. Included build
files share it. [Records](#records) and [artifacts](#artifacts) live under
`<root>/target/roadie/`. `load` and `execute` set the project root; code that
asks for it outside them fails with `no project root: run steps through
roadie's load and execute`.

## Steps

### Dependencies and attributes

A step is an instance of a `Step` subclass. `Step.build(run)` produces the
step's value from its dependencies' values; `Step.teardown(value)` releases
the value and does nothing by default. A step also records, when constructed,
its base directory, the directory of the build file loading it, and the
environment files of [Settings](#settings).

The attributes of a step are its inputs. An attribute that is a step, or a
tuple holding steps, makes those steps its **dependencies**: they build
before it. An attribute that is an [input](#input-fingerprints), or a tuple
holding inputs, is fingerprinted. The remaining attributes must be plain
values: `None`, `str`, `int`, `float`, `bool`, a `Path`, or a tuple of them.
Any other attribute fails with `<step> attribute <name> must be a plain value,
an input, or a step`, when the step is fingerprinted or shown by `--dry`.

### Commands

`Step._cmd(*args, capture_output=False, environment=None, check=True)` prints
`+ <command>` and runs the command in the step's base directory. Its
environment is the process environment, then `environment`, then the
[environment files](#environment-files). Standard output is shown, or
returned when `capture_output` is set. Standard error is always captured.

A command that exits non-zero fails the step, unless `check` is false, with
`<name> failed: <command> exited with status <n>: <last 2000 characters of
standard error>`.

### Started processes

`Step._start_cmd(*args, port, environment=None, timeout=120,
health_path=None)` prints `+ <command>`, starts the command in the step's base
directory in a new session (POSIX) so its whole process tree can be signalled,
and returns a `RunningProcess` with the process and the URL
`http://127.0.0.1:<port>`. It returns once the port accepts connections, or,
with `health_path`, once `GET <url><health_path>` answers 200.

If the process exits first, the step fails with `<process> exited with status
<n> before becoming ready`; if `timeout` seconds pass, with `<process> did not
start listening on port <port> within <timeout>s` (or `did not become ready
at <url> within <timeout>s`). `<process>` is the executable's file name and
up to two following arguments that are not options. In both cases the process
tree is stopped.

`RunningProcess.stop` signals the process tree with SIGTERM, waits up to 10
seconds, then sends SIGKILL, and kills any descendant that outlives its
parent.

### File steps

A `File` step builds a file and returns its path. It has an optional keyword
attribute `output`; see [Generated files](#generated-files). A reused `File`
step returns the path it recorded.

### Binaries

A `Binary` step builds an executable and returns its path.
`RustBinary(package, files=None)` takes a `CargoPackage` and an optional
`Files` input. It runs `cargo build --locked -p <package> --message-format=json`,
finds the executable that cargo reports for the package's binary target, and
copies it, keeping its mode, to the step's [artifact](#artifacts) (with `.exe`
on Windows). It fails when cargo reports no executable
(`cargo build --locked -p <package> produced no executable`) or several
different ones (`... produced multiple executables`).

### Services

A `Service` step builds a running **deployment**: an object with `process`,
`url` and `stop()`; tearing the step down calls `stop()`. A service is never
reused: `Service` sets the class attribute `reusable` to false, as any step
class may. `HttpService` is a service whose `_start_cmd` waits for its
`health_path` class attribute (default `/`) unless the call names another.

Roadie starts a service late. A service the command did not name is built
just before the first step that takes it and must build, so a plan whose
consumers are all reused starts nothing; it then prints `not started <name>:
no step that takes it runs`. A service's own dependencies stay live until its
last consumer. A service's output fingerprint is its step fingerprint, so its
consumers can be reused without it running.

### Tests

A `Tests` step builds a `TestResults`: failures, each a `TestFailure(name,
message)`, and log messages. After it builds, Roadie prints the messages; any
failure fails the step with one `<name>: <message>` line per failure.
`roadie.testing.read_junit(path)` reads a JUnit report into `TestResults`: a
`<failure>` or `<error>` under a `<testcase>` is a failure named
`<classname>.<name>`, and the text of `<system-out>` and `<system-err>` is a
message. An unreadable report, malformed XML, or a root other than
`<testsuite>` or `<testsuites>` fails with `Cannot read JUnit report <path>:
<reason>`.

A reused `Tests` step prints the messages recorded when it last passed. A
failed step records nothing, so failures are never replayed.

### Groups

`Group(*steps)` depends on its steps and builds the tuple of their values,
so one result can stand for several steps. A member that is not a step fails
with `Group takes only steps`. A group is never recorded: it is reused when
all its members are, printing `reused <name>`, and its output fingerprint is
a digest of its members'.

## Running a plan

### Plan order

The **plan** of a result is the result and its dependencies, each step once,
each after the steps it depends on, in the order of the attributes that name
them. Steps that depend on each other fail with `steps depend on each other:
<a> -> <b> -> <a>`. The requested result is the last step of the plan.

### Step values

`run[step]` returns the value of a dependency built or restored earlier in
the run. A step the run has not built, or has already torn down, fails with
`<name> is not available in this run`. `run.artifact(step, suffix)` names a
file for the step; see [Artifacts](#artifacts).

### Teardown

Roadie tears a step down right after the last step in the plan that takes it
as a dependency; a step nothing takes is torn down right after it. Steps due
at the same point are torn down in reverse build order. A reused step is torn
down with its restored value.

When a step fails, or the run is interrupted by Ctrl-C or SIGTERM, every step
still live is torn down in reverse build order. A teardown that fails does
not stop the others from being torn down. The first teardown failure fails
the run with `<name> failed: teardown: <reason>` and the others are printed
to standard error; when the run is already failing, all are printed and the
original failure stands.

### Failures

An exception raised while Roadie fingerprints, starts, builds, publishes,
reports or records a step fails the run with `<name> failed: <reason>`,
where `<name>` is the step's name, or its class name when it has none, and
`<reason>` is the exception's message, or its class name when the message is
empty. A string passed to `SystemExit` is its own reason. An interrupt is not
a failure. The command prints a `RoadieError` message to standard error and
exits with status 1.

### Requested services

When the requested result is a service, Roadie builds it in plan order,
prints `<name> is up: <url> (Ctrl-C to stop)`, and waits on its process.
Ctrl-C and SIGTERM end the wait; the service is torn down and the command
exits normally. A process that exits with status 0 prints `<name> exited`; any
other status fails the step with `exited with status <n>`. A service that has
no `process` is not waited on.

Roadie leaves the terminal's standard input to the service's process, so a
dev server can read keystrokes.

### Environment files

`--env <file>` (repeatable) and `config(env=[...])` name files of `KEY=VALUE`
lines in python-dotenv syntax; keys without a value are ignored. A step
receives the values of its build file's `config` files, then the `--env`
files; later files win. These values override the process environment and
the `environment` argument of a command, and are part of the step's
fingerprint. A relative `--env` path is relative to the working directory.

A file that does not exist fails the run with `environment file does not
exist: <path>` before any step builds, also under `--dry`.

## Reuse

### Reusing a step

A step is **reusable** when it is bound to a variable of a build file, its
class does not set `reusable` to false (`Service` does), and it is not a
`Group`. Roadie reuses a reusable step, instead of building it, when its
[record](#records) has the same [fingerprint](#step-fingerprints) and, for a
`File` or `Binary`, the recorded file still exists with the recorded content.
It prints `reused <name>` and the step's value is the recorded one: a path for
`File` and `Binary`, the recorded messages for `Tests`, the recorded value for
any other step, which must be JSON-serializable.

A step that builds is recorded after it succeeds, and its consumers are
fingerprinted from its output. A step that is not reusable is built on every
run, and its consumers can still be reused.

### Step fingerprints

A step's fingerprint is a SHA-256 digest of:

- its class, by module and qualified name;
- a hash of the source files of the modules that define the classes of the
  step's class hierarchy, and of Roadie's own modules;
- its base directory, relative to the project root;
- the values of its [environment files](#environment-files);
- its plain attributes;
- the [fingerprints](#input-fingerprints) of its inputs;
- the [output fingerprints](#output-fingerprints) of its dependencies.

### Input fingerprints

An **input** is an instance of a subclass of `roadie.inputs.Input`, which
implements `fingerprint(base_dir) -> str`: a digest of what it names, seen
from the step's base directory. Roadie provides `Files` and `CargoPackage`.

### Files input

`Files(*paths)` names files and directories, relative to the step's base
directory. A directory stands for every file under it, recursively. The
fingerprint covers each file's path, relative to the project root when inside
it, and the SHA-256 of its content; a path that does not exist counts as
missing, so creating it changes the fingerprint.

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

`roadie.cargo.cargo_rust_packages(directory)` returns the workspace root and
its packages: each with its name, its directory relative to the root, and the
workspace packages it depends on.
`roadie.cargo.select_rust_packages(packages, changed_paths)` returns the names
of the packages that contain a changed path, and of every package that
depends on them; a change to the root `Cargo.toml` or `Cargo.lock` selects
all.

### Output fingerprints

A step's **output fingerprint**, which its consumers' fingerprints include,
is:

- for a `File`, the SHA-256 of the file;
- for a `Binary`, `Service` or `Tests` step, the step's own fingerprint;
- for a group, a digest of its members' output fingerprints;
- for any other step, a digest of its value.

A rebuilt `File` whose content is unchanged therefore leaves its consumers
reusable.

### Records

Each build file keeps a **record** of its steps' last successes in
`<root>/target/roadie/<directory>/build.record.json`, where `<directory>` is
the build file's directory relative to the project root (none for the root
itself; `_outside/<12 hex digits of a digest of the path>` when outside it).
The file maps each reusable variable to its step fingerprint, output
fingerprint, the SHA-256 of its file, and its value; a path is stored
relative to the project root when inside it.

A successful step replaces its entry; entries of variables the build file no
longer defines are dropped. A failed step writes nothing. The file is
written through a temporary file and renamed.

### Artifacts

`run.artifact(step, suffix)` returns
`<root>/target/roadie/<directory>/<variable><suffix>`, with `<directory>` as
for [Records](#records), and creates its parent directory. A step without a variable uses `<ClassName>-<serial>`, a number
unique to the step within the process, so two unnamed steps of one class
never share a file.

## Generated files

A `File` step with `output` publishes its built file after it builds: the
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

## Command line

```
roadie [--dry-run | --dry] [--locked] [--base_dir DIR] [--env FILE]... [directory] [result]
```

`directory` (default `.`) holds the `build.py` to load; `result` is a result
of [Results](#results). Without `--dry`, `roadie` builds the plan of the
result and exits with status 0 when it succeeds.

- `--base_dir DIR`: the default base directory of every step; see
  [Settings](#settings).
- `--locked`: fail instead of changing a tracked generated file; see
  [Generated files](#generated-files).
- `--env FILE`: an [environment file](#environment-files); may be repeated.
- `--dry-run`, `--dry`: print the plan and build nothing.

With `--dry`, `roadie` prints one line per step in plan order:
`<name> = <Class>(<arguments>)  # reuse` or `# run`, without `<name> = ` for
a step with no name. Arguments are the step's attributes that are not `None`:
positional ones in order, keyword-only ones as `name=value`; a step argument
is shown by its name, or as its own call when it has none; a group shows its
members. `# reuse` promises the step will not build: its dependencies are all
`# reuse` and its record matches (for a group, its members are all `# reuse`).
`# run` means it may build.

There is no version option. `roadie --help` prints usage.

## Open questions

Non-normative. Each item is behavior found in the code that no design states.
Answer *intended* and the item moves into its section as a rule, or *change*
and it becomes an item in [todo/future.md](todo/future.md).

- **Classes defined in a build file are not hashed.** Editing a step class
  written in `build.py` itself does not change the step's fingerprint, so the
  step is reused (verified: a changed command in such a class was still
  `reused`). Classes in imported modules are hashed.
  Code: `fingerprint._code_hash` looks the class's module up in
  `sys.modules`, where `runpy.run_path` leaves no entry.
- **The hash covers standard-library sources.** `abc.py` and `typing.py`
  are in every step's code hash, because `Step` derives from `ABC` and
  `Generic`, so a Python upgrade reruns every step.
  Code: `fingerprint._code_hash`.
- **`Files` includes git-ignored files; `CargoPackage` does not.** A new
  ignored file under a directory named by `Files` changes its fingerprint and
  reruns the step (verified); `CargoPackage` hashes only files git lists.
  Code: `inputs._files_under`, `cargo._git_files`.
- **The process environment is not fingerprinted.** A step that reads an
  environment variable is reused when the variable changes (verified); only
  environment-file values and the Cargo variables of `CargoPackage` count.
  Code: `fingerprint.fingerprint`.
- **Standard error of a command that succeeds is discarded.** `_cmd` always
  captures it and shows at most 2000 characters of it on failure.
  Code: `Step._cmd`.
- **A step bound to no variable is never reused or recorded.** An inner step
  written inline, as in `Copy(Emitter())`, builds on every run, though
  `Copy` is reused when the emitted file is unchanged.
  Code: `run._reusable`.
- **`--base_dir` is the only option spelled with an underscore**; the others
  are `--dry-run`, `--locked` and `--env`.
  Code: `cli.run`.
