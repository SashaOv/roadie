from typing import Any

from roadie.engine.errors import RoadieError
from roadie.engine.step import Composite, Step


# (spec) Step kinds
class Group(Composite):
    """Depend on `steps`; the value is the tuple of their values.

    A member that is not a step fails with `Group takes only steps`. A group
    has no record. Roadie prints `reused <name>` when the group has members
    and every member was reused.
    `--dry` shows its members as its arguments.
    """

    def __init__(self, *steps: Step[Any]) -> None:
        if any(not isinstance(step, Step) for step in steps):
            raise RoadieError("Group takes only steps")
        super().__init__(steps)
