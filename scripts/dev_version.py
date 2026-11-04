import re
import sys

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


def dev_version(current_version, branch_name, build_number, commit_sha):
    match = VERSION_PATTERN.fullmatch(current_version)
    if not match:
        raise ValueError(f"Invalid VERSION value: {current_version!r}")

    try:
        build_number = int(build_number)
    except (TypeError, ValueError) as error:
        raise ValueError("Build number must be a positive integer.") from error
    if build_number < 1:
        raise ValueError("Build number must be a positive integer.")
    if not COMMIT_PATTERN.fullmatch(commit_sha):
        raise ValueError("Commit SHA must be a hexadecimal Git object ID.")

    branch_slug = re.sub(r"[^a-z0-9]+", "-", branch_name.lower()).strip("-")[:40].rstrip("-")
    if not branch_slug:
        branch_slug = "branch"

    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}-{branch_slug}.{build_number}.{commit_sha[:7]}"


def main():
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: dev_version.py <version> <branch-name> <build-number> <commit-sha>"
        )
    try:
        version = dev_version(*sys.argv[1:])
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(version)


if __name__ == "__main__":
    main()
