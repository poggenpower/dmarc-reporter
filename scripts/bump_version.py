import json
import re
import sys
from pathlib import Path

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT_PATTERN = re.compile(r"^(major|feat|sec|fix|doc|chore):\s+\S")
PRIORITY = {"doc": 1, "fix": 1, "sec": 2, "feat": 2, "major": 3}


def next_version(current_version, commits):
    match = VERSION_PATTERN.fullmatch(current_version)
    if not match:
        raise ValueError(f"Invalid VERSION value: {current_version!r}")

    kinds = []
    for commit in commits:
        message = commit["commit"]["message"].splitlines()[0]
        prefix_match = COMMIT_PATTERN.match(message)
        if not prefix_match:
            raise ValueError(
                "Commit message must start with major:, feat:, sec:, fix:, doc:, or chore:: "
                f"{message!r}"
            )
        kind = prefix_match.group(1)
        if kind != "chore":
            kinds.append(kind)

    if not kinds:
        return None

    major, minor, patch = (int(part) for part in match.groups())
    patch += len(kinds)
    highest = max(kinds, key=PRIORITY.__getitem__)
    if highest == "major":
        major += 1
        minor = 0
    elif highest in {"feat", "sec"}:
        minor += 1
    return f"{major}.{minor}.{patch}"


def next_version_for_pull_requests(current_version, pull_requests):
    changed = False
    for commits in pull_requests:
        version = next_version(current_version, commits)
        if version is not None:
            current_version = version
            changed = True
    return current_version if changed else None


def read_pull_request_commits(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [commit for page in payload for commit in page]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: bump_version.py <pull-request-commits.json>... | "
            "--pull-request-files <file-list>"
        )

    if sys.argv[1] == "--pull-request-files":
        if len(sys.argv) != 3:
            raise SystemExit("Usage: bump_version.py --pull-request-files <file-list>")
        paths = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
    else:
        paths = sys.argv[1:]
    pull_requests = [read_pull_request_commits(path) for path in paths]
    current_version = Path("VERSION").read_text(encoding="utf-8").strip()
    try:
        version = next_version_for_pull_requests(current_version, pull_requests)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(version if version is not None else "skip")


if __name__ == "__main__":
    main()
