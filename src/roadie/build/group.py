"""A step that stands for several steps."""

from typing import TYPE_CHECKING, Any

from roadie.engine.errors import RoadieError
from roadie.engine.step import Step

if TYPE_CHECKING:
    from roadie.engine.run import Run


# (spec) Step kinds
class Group(Step[tuple[object, ...]]):
    """Depend on `steps`; the value is the tuple of their values.

    A member that is not a step fails with `Group takes only steps`. A group
    has no record: it prints `reused <name>` when all its members were reused.
    `--dry` shows its members as its arguments.
    """

    recorded = False

    def __init__(self, *steps: Step[Any]) -> None:
        if any(not isinstance(step, Step) for step in steps):
            raise RoadieError("Group takes only steps")
        self.steps = steps

    def build(self, run: "Run") -> tuple[object, ...]:
        values = tuple(run[step] for step in self.steps)
        if self.steps and all(id(step) in run.reused for step in self.steps):
            print(f"reused {self.name or type(self).__name__}", flush=True)
        return values

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        return list(self.steps), {}
