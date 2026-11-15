# Acceptance integration

Tag: `AI`.

## Goal and design

A build's acceptance has two parts:

- **Durable checks** stay true of a module after the build and are wanted on
  every release: the CI gate on a head, the release checklist, live smoke
  drills, deploy checks. They belong to the module, as results of its
  `build.py`.
- **The in-flight part** matters only while one build is in progress:
  authorizations with their scope and attempt caps, the rollout order and the
  changes each step expects, and pinned values such as a release tag. It
  selects durable checks and records their evidence against one head. It
  changes with the build's plan and dies with it.

The in-flight part lives in the build record of the change spec, as a fenced
block whose info string is `python acceptance`. The block binds the build's
`<tag>_authorize`, `<tag>` and `<tag>_review` results. Only such blocks run.
Implementing the build retags its block to plain `python`, so the record keeps
what was authorized and nothing loads it.

Each directory that holds change specs with live blocks has one `build.py`. It
sets `base_dir` and calls `load_acceptance(globals(), <spec files>)`, which
runs every live block of the named specs in the `build.py`'s namespace. Roadie
keeps one build file per directory, and its records stay per directory. Tags
are unique, so several specs share the file. The commands are `uv run roadie
-f <dir> <tag>_authorize`, `<tag>` and `<tag>_review`.

`load_acceptance` compiles each block under the spec's path, with the block's
own line numbers, so a traceback names `<spec>.md:<line>`. A name bound by two
blocks fails with `<name> is bound in <spec>:<line> and <spec>:<line>`. A spec
that cannot be read fails with `cannot read <path>: <reason>`.

## Facts

- 2026-11-11: Roadie's `-f` has never loaded a file other than `build.py`. R13.1
  (`8937e82`) specified "A file is that build file", but `_build_directory`
  (`src/roadie/cli.py`) turns a file into its directory and `load` reads
  `<directory>/build.py`. Reproduced: `-f docs/ws_acceptance.py` fails with
  `<dir>/docs has no build.py`, and with a `build.py` beside it Roadie loads
  that file instead. `2895004` let `include` take a file and left this path
  alone. The only `include` of a file other than `build.py` is Roadie's
  `tests/engine/test_graph.py`.
- 2026-11-11: Roadie keeps records and artifacts in `target/roadie/<directory>/`,
  and the sweep keeps a record directory only while `<directory>/build.py`
  exists (`engine/records.py`, `_build_file`).
- 2026-11-11: Niva's WR12 acceptance ran because `docs/todo/build.py` was a
  symlink to `roadie_acceptance.py` (added in `ac664ae`, WR12.1). Retiring the
  spec (`f136c88`) moved the file to `docs/todo/history/` and deleted the
  symlink. `jammer/docs/todo/` has no `build.py`, so `milestone_acceptance.py`
  (WS25) has never loaded through `-f`.
- 2026-11-11: Niva's acceptance files mix both parts. WS25's
  `milestone_acceptance.py` holds three authorizations with caps, the L1–L3
  rollout order, and the drills `jtool drill 10`, `11` and `12` against dev and
  prod. WR12's held the package, release-asset and install checks, which went
  to `history/` with the spec. `forge/forge/evidence.py`'s `dispatch_passed`
  hardcodes branch `wr12`.
- 2026-11-11: with Roadie at `4ee2b6a`, a `build.py` that `exec`s a block
  compiled under `spec.md`, padded to its line, into `globals()` gives `roadie
  --dry` both bound steps. A misspelled name in the block fails with
  `File "spec.md", line 4`, the line in the Markdown file.
- 2026-11-11: Niva is on branch `ws25`. `jammer/docs/todo/milestone.md` is
  modified and `milestone_acceptance.py` is untracked: the WS25 plan is not
  committed.

--- Implemented / Next ---

## Builds

### AI1: WS25's acceptance runs from a block in its change spec

Status: planned, awaiting author checkpoint

**Improvement.** `uv run roadie -f jammer/docs/todo ws25_authorize` loads
WS25's acceptance from a `python acceptance` block in `milestone.md`. Today
`-f jammer/docs/todo/milestone_acceptance.py` fails with `jammer/docs/todo has
no build.py`, so the author cannot authorize WS25. The prototype also measures
the design's costs, so the backlog can be confirmed or dropped.

**Decisions.** Proposed for the checkpoint.

1. The patches land in Niva, on branch `ws25`, after the WS25 plan is
   committed there. The block moves WS25's acceptance, which that plan owns.
2. The WS25 code moves unchanged, apart from its imports. Durable checks stay
   where they are; moving them into module results is backlog.
3. `AGENTS.md` and `docs/dev.md` keep the `*_acceptance.py` commands until the
   backlog's convention change. WS25's record names the directory commands.

**Acceptance check.**

- Before AI1.2, a temporary `jammer/docs/todo/build.py` copied from
  `milestone_acceptance.py` gives the baseline: `uv run roadie --dry -f
  jammer/docs/todo <result>` for `ws25_authorize`, `ws25` and `ws25_review`,
  saved to `target/ai1/before-<result>.txt`. After AI1.2, the same commands
  print the same lines, and `diff` of each pair is empty.
- After a misspelled name is put into the block, `roadie --dry` fails with a
  traceback naming `milestone.md` and the block's line. Retagging the block to
  plain `python` makes `roadie --dry` fail with `<dir>/build.py defines no
  results`. Both edits are reverted.
- Niva's commit gate (`uv run roadie`) passes, with AI1.1's tests.
- The record notes the costs it observed: how the block reads in
  `milestone.md`, how long it is, and what ruff and mypy do not check.

**Author action required at completion.**

- The author reads WS25's record in `milestone.md` with its block, and decides
  whether the backlog goes ahead. This discharges the cost clause, which is a
  judgement only the author can make.

### Patches

- [ ] **AI1.1: Load acceptance blocks from change specs.** `load_acceptance`
  in `forge/forge/acceptance.py`, as the design says. Tests in
  `forge/tests/test_acceptance.py`: one block; two specs; a plain `python`
  block, which is not run; a traceback's file and line; a name bound in two
  blocks; a missing spec; a spec with no live block.
- [ ] **AI1.2: Run WS25's acceptance from milestone.md.** The block goes into
  WS25's build record. `jammer/docs/todo/build.py` sets `base_dir` and loads
  `milestone.md`. `milestone_acceptance.py` goes. The acceptance check's
  baseline is taken before this patch.

## Backlog

- Implementing a build retags its block to plain `python`. The oneplan skill
  gains an optional paragraph on `python acceptance` blocks.
- Niva's `AGENTS.md` and `docs/dev.md` name `-f <dir> <tag>…`, and the
  `*_acceptance.py` convention goes.
- Niva's durable checks become module results: the release checklist, the live
  drills, the deploy checks. `dispatch_passed` loses its `wr12` branch.
- Niva's commit gate loads every directory with live blocks under `--dry`.
- The retired `docs/todo/history/roadie_acceptance.py` goes: WR12's record
  holds its evidence.
- Roadie's `-f` and `include` take a directory, or a path to a `build.py`; any
  other file fails, `2895004`'s file form goes, and the spec's Concepts,
  Records and Artifacts state the one-file-per-directory rule plainly.
- `roadie -s <dir>` builds from the `python acceptance` blocks of the Markdown
  files in `<dir>`, so the spec directory's `build.py` goes; each spec's front
  matter names the module it builds (the project root when absent), which sets
  its blocks' base directory.
