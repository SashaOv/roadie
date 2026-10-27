"""Read test failures and messages from JUnit reports."""

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from wright.errors import WrightError


@dataclass(frozen=True)
class TestFailure:
    name: str
    message: str


@dataclass(frozen=True)
class TestResults:
    failures: tuple[TestFailure, ...] = ()
    messages: tuple[str, ...] = ()


def read_junit(path: Path) -> TestResults:
    try:
        root = ElementTree.parse(path).getroot()
    except OSError as error:
        raise WrightError(
            f"Cannot read JUnit report {path}: {error.strerror}"
        ) from error
    except ElementTree.ParseError as error:
        raise WrightError(f"Cannot read JUnit report {path}: malformed XML") from error
    if _tag(root) not in {"testsuites", "testsuite"}:
        raise WrightError(
            f"Cannot read JUnit report {path}: expected testsuite or testsuites"
        )
    failures: list[TestFailure] = []
    messages: list[str] = []
    for element in root.iter():
        if _tag(element) in {"system-out", "system-err"}:
            text = "".join(element.itertext()).strip()
            if text:
                messages.append(text)
        if _tag(element) != "testcase":
            continue
        details: list[str] = []
        for child in element:
            if _tag(child) not in {"failure", "error"}:
                continue
            message = child.get("message", "").strip()
            text = "".join(child.itertext()).strip()
            details.append(
                "\n".join(part for part in (message, text) if part) or "Test failed"
            )
        if details:
            name = element.get("name", "Unnamed test")
            classname = element.get("classname")
            failures.append(
                TestFailure(
                    f"{classname}.{name}" if classname else name, "\n".join(details)
                )
            )
    return TestResults(tuple(failures), tuple(messages))


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]
