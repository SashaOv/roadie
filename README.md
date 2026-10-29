# Roadie

Wire up multi-language, incremental builds in a few lines of Python.

A step that does not need to run does not run. Roadie knows each step's
inputs, so when they are unchanged since the step last succeeded, it reuses
that result instead of running the step; a step whose result cannot be
reused, such as a fuzzing run, says so.

Like a roadie on stage, it sets up and connects the native tools (cargo,
pnpm, uv) and lets them play, instead of replacing them.

## Install

```sh
uv add roadie
uvx roadie
```

## A two-step build file

`roadie` runs the last step defined in `<directory>/build.py`, after the
steps it depends on. The second run reuses a step whose inputs have not
changed.

```python
from dataclasses import dataclass
from pathlib import Path

from roadie.build import *


@dataclass(eq=False)
class Emitter(File):
    text: str = "hello"

    def build(self, run: Run) -> Path:
        path = run.artifact(self, ".txt")
        path.write_text(self.text)
        return path


@dataclass(eq=False)
class Copy(File):
    source: File

    def build(self, run: Run) -> Path:
        path = run.artifact(self, ".txt")
        path.write_text(run[self.source].read_text())
        return path


api = Emitter()
copy = Copy(api)
```

```sh
roadie .
```

The design is in [docs/spec.md](docs/spec.md).
