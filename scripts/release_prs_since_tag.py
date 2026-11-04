import json
import sys
from pathlib import Path


def pull_requests_since_tag(pull_pages, commit_shas):
    commit_order = {sha: index for index, sha in enumerate(commit_shas)}
    pull_requests = [
        pull_request
        for page in pull_pages
        for pull_request in page
        if pull_request.get("merged_at")
        and pull_request.get("base", {}).get("ref") == "main"
        and pull_request.get("merge_commit_sha") in commit_order
    ]
    pull_requests.sort(key=lambda pull_request: commit_order[pull_request["merge_commit_sha"]])
    return [pull_request["number"] for pull_request in pull_requests]


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: release_prs_since_tag.py <pull-requests.json> <commit-shas.txt>")

    pull_pages = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    commit_shas = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
    try:
        pull_requests = pull_requests_since_tag(pull_pages, commit_shas)
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"Invalid pull request data: {error}") from error
    for pull_request in pull_requests:
        print(pull_request)


if __name__ == "__main__":
    main()
