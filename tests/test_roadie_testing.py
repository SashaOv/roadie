from pathlib import Path

import pytest
from roadie import RoadieError
from roadie.testing import TestFailure as Failure
from roadie.testing import TestResults as Results
from roadie.testing import read_junit


def test_read_junit_reads_nested_failures_errors_and_log_messages(
    tmp_path: Path,
) -> None:
    report = tmp_path / "tests.xml"
    report.write_text("""<testsuites><testsuite>
      <testcase name="pass" />
      <testcase name="skip"><skipped message="disabled" /></testcase>
      <testsuite><testcase classname="api" name="failure">
        <failure message="wrong status">Expected 200</failure>
        <system-out>request log</system-out>
      </testcase></testsuite>
      <testcase name="error"><error message="connection failed" /></testcase>
      <system-err>stderr log</system-err>
    </testsuite></testsuites>""")
    assert read_junit(report) == Results(
        failures=(
            Failure("api.failure", "wrong status\nExpected 200"),
            Failure("error", "connection failed"),
        ),
        messages=("request log", "stderr log"),
    )


@pytest.mark.parametrize(
    "xml",
    ["<testsuite/>", '<testsuite xmlns="urn:junit"><testcase name="ok" /></testsuite>'],
)
def test_read_junit_accepts_a_passing_suite(xml: str, tmp_path: Path) -> None:
    report = tmp_path / "tests.xml"
    report.write_text(xml)
    assert read_junit(report) == Results()


@pytest.mark.parametrize(
    ("xml", "reason"),
    [
        (None, "Cannot read"),
        ("<bad", "malformed XML"),
        ("<not-junit/>", "expected testsuite or testsuites"),
    ],
)
def test_read_junit_reports_unreadable_or_invalid_report(
    xml: str | None, reason: str, tmp_path: Path
) -> None:
    report = tmp_path / "tests.xml"
    if xml is not None:
        report.write_text(xml)
    with pytest.raises(RoadieError) as error:
        read_junit(report)
    assert reason in str(error.value)
    assert str(report) in str(error.value)
