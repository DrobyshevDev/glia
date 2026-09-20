#!/usr/bin/env python3
"""What the READMEs say about the test suite has to be what the suite is.

Two numbers appear in both READMEs, in two languages, and nothing recomputed
either of them: how many tests there are, and the coverage floor. Both are the
kind of claim a stranger reads to decide whether the project is serious, and
both drift silently -- a test file lands, the sentence does not change, and the
number is wrong from that commit until somebody counts by hand.

The test count comes from pytest's own collection rather than from counting
`def test_`, because they are not the same number: one parametrized function
here is four collected tests, and a check that counted functions would report
164 and be confidently wrong.

The floor comes from pyproject.toml, which is where it is enforced. It is a
guarantee rather than a measurement, which is why it is the number that belongs
in prose at all -- the measured figure belongs on the Codecov badge, recomputed
per run. A README cannot honestly carry a figure that changes on every commit.

A claim that has gone missing counts as a discrepancy too. Rewrite the sentence
and the pattern stops matching, the check stops checking, and says nothing about
having stopped -- which is how this kind of check rots. So an unfound claim is
reported exactly like a wrong number.

Standard library plus pytest, which the dev extra already installs.

    python scripts/check_claims.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every place the two numbers are written down, and the shape each is written
#: in. The shape is part of the check: a claim that no longer matches is a
#: claim that is no longer being checked.
READMES = {
    "README.md": {
        "tests": (r"\((\d+) offline tests", "(N offline tests"),
        "floor": (r"a (\d+)% coverage floor", "a N% coverage floor"),
    },
    "README.ru.md": {
        "tests": (r"\((\d+) офлайн-тестов", "(N офлайн-тестов"),
        "floor": (r"порог покрытия (\d+)%", "порог покрытия N%"),
    },
}

#: How each claim is named in a report. `subject` finishes "… is now
#: unchecked"; `mismatch` is the whole sentence when the number is wrong.
NAMES = {
    "tests": ("the test count", "says {said} tests, pytest collects {real}"),
    "floor": ("the coverage floor", "says a {said}% floor, pyproject enforces {real}%"),
}


def collected_tests() -> int | None:
    """How many tests pytest actually collects. None when it could not be asked."""
    try:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"^(\d+) tests? collected", run.stdout or "", re.M)
    return int(match.group(1)) if match else None


#: `fail_under` inside `[tool.coverage.report]`, and nowhere else: `fail_under`
#: is a plausible key in more than one section, and reading the first one in the
#: file would eventually read somebody else's.
FLOOR = re.compile(
    r"^\[tool\.coverage\.report\]\s*$(?:(?!^\[).)*?^fail_under\s*=\s*(\d+)\s*$",
    re.M | re.S,
)


def configured_floor() -> int | None:
    # Read with a regular expression rather than tomllib: tomllib arrived in
    # 3.11 and glia supports 3.10, so importing it would make this script
    # crash for a contributor on the oldest Python the project promises.
    try:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    found = FLOOR.search(text)
    return int(found.group(1)) if found else None


def main() -> int:
    problems: list[str] = []

    tests = collected_tests()
    if tests is None:
        print("  could not ask pytest how many tests there are", file=sys.stderr)
        return 2

    floor = configured_floor()
    if floor is None:
        # Not a missing number but a missing gate: codecov.yml tells the reader
        # the floor lives in pyproject.toml, and the Codecov statuses are
        # informational because of it.
        problems.append(
            "pyproject.toml: no [tool.coverage.report] fail_under, so `pytest --cov` "
            "gates at nothing — and codecov.yml says this is where the floor lives"
        )

    truth = {"tests": tests, "floor": floor}
    for name, claims in READMES.items():
        text = (ROOT / name).read_text(encoding="utf-8")
        for key, (pattern, shape) in claims.items():
            expected = truth[key]
            if expected is None:
                continue
            found = re.search(pattern, text)
            subject, mismatch = NAMES[key]
            if found is None:
                problems.append(
                    f"{name}: no longer says \"{shape}\" — the sentence was rewritten, "
                    f"so {subject} is now unchecked"
                )
            elif int(found.group(1)) != expected:
                problems.append(
                    f"{name}: " + mismatch.format(said=found.group(1), real=expected)
                )

    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"\n  discrepancies: {len(problems)}", file=sys.stderr)
        return 1

    print(f"  {tests} tests, a {floor}% coverage floor — and both READMEs say so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
