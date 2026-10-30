"""Store one value or many as a tuple."""

from collections.abc import Iterable
from typing import Any

from roadie.engine.errors import RoadieError


def one_or_many[T](
    value: object,
    kind: type[T] | tuple[type[Any], ...],
    *,
    owner: str,
    parameter: str,
    takes: str,
) -> tuple[T, ...]:
    """Return `value` as a tuple of `kind`.

    A single `kind` stays one element. Any other iterable is read once and
    stored, because a step's attributes are fingerprinted and must not change
    later. A value of the wrong kind fails with `<owner> <parameter> takes
    <takes>: got <type>`.
    """
    if isinstance(value, kind):
        return (value,)
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise RoadieError(f"{owner} {parameter} takes {takes}: got {type(value).__name__}")
    items: list[T] = []
    for item in value:
        if not isinstance(item, kind):
            raise RoadieError(f"{owner} {parameter} takes {takes}: got {type(item).__name__}")
        items.append(item)
    return tuple(items)
