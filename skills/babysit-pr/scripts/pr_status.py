#!/usr/bin/env python3
"""Print one JSON snapshot of a pull request for the babysit-pr skill.

Reads through the GitHub CLI (gh), so it needs no token of its own. The
snapshot covers merge state, every check on the head commit, unresolved
review threads, and the comments and reviews newer than the head commit,
which is the "since the last push" cut the babysit loop keys on.

    pr_status.py                 the current branch's PR
    pr_status.py --pr 12         another PR in this repository
    pr_status.py --repo o/r --pr 12 --since 2026-09-15T00:00:00Z
"""

import argparse
import json
import subprocess
import sys

PR_FIELDS = ("number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,"
             "headRefOid,headRefName,baseRefName,commits,statusCheckRollup,"
             "reviews,comments,autoMergeRequest")

# Thread resolution is only exposed through GraphQL.
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          comments(first: 50) { nodes { author { login } body createdAt url } }
        }
      }
    }
  }
}
"""

PASSED = {"SUCCESS", "NEUTRAL"}
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", None}


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("gh {} failed: {}".format(" ".join(args[:2]), result.stderr.strip()))
    return json.loads(result.stdout)


def fetch_threads(owner, name, number):
    nodes, after = [], None
    while True:
        variables = ["-F", "owner=" + owner, "-F", "name=" + name, "-F", "number={}".format(number)]
        if after:
            variables += ["-F", "after=" + after]
        page = gh("api", "graphql", "-f", "query=" + THREADS_QUERY, *variables)
        threads = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes += threads["nodes"]
        if not threads["pageInfo"]["hasNextPage"]:
            return nodes
        after = threads["pageInfo"]["endCursor"]


def classify_check(check):
    """Map a status context or check run from `gh pr view` onto passed, failed, pending, or skipped."""
    if check.get("__typename") == "StatusContext":
        name, state, link = check.get("context"), check.get("state"), check.get("targetUrl")
    else:
        name, link = check.get("name"), check.get("detailsUrl")
        state = check.get("conclusion") if check.get("status") == "COMPLETED" else check.get("status")
    if state in PASSED:
        kind = "passed"
    elif state == "SKIPPED":
        kind = "skipped"
    elif state in PENDING:
        kind = "pending"
    else:
        kind = "failed"
    return {"name": name, "kind": kind, "state": state, "link": link}


def login(node):
    return (node.get("author") or {}).get("login")


def summarize(pr, threads, since):
    """Pure: shape the gh payloads into the snapshot. `since` is an ISO-8601 UTC cut."""
    checks = [classify_check(check) for check in pr.get("statusCheckRollup") or []]
    unresolved = []
    for thread in threads:
        if thread.get("isResolved"):
            continue
        comments = thread["comments"]["nodes"]
        first = comments[0] if comments else {}
        unresolved.append({
            "id": thread["id"], "path": thread.get("path"), "line": thread.get("line"),
            "outdated": bool(thread.get("isOutdated")), "author": login(first),
            "body": first.get("body", ""), "replies": max(len(comments) - 1, 0), "url": first.get("url"),
        })
    return {
        "number": pr["number"], "url": pr["url"], "state": pr["state"], "draft": pr["isDraft"],
        "mergeable": pr["mergeable"], "merge_state": pr["mergeStateStatus"],
        "review_decision": pr.get("reviewDecision") or None,
        "auto_merge": bool(pr.get("autoMergeRequest")),
        "base": pr["baseRefName"],
        "head": {"sha": pr["headRefOid"], "ref": pr["headRefName"], "committed_at": since},
        "checks": {
            "counts": {kind: sum(1 for check in checks if check["kind"] == kind)
                       for kind in ("passed", "failed", "pending", "skipped")},
            "failed": [check for check in checks if check["kind"] == "failed"],
            "pending": [check for check in checks if check["kind"] == "pending"],
        },
        "threads": {"unresolved": unresolved,
                    "resolved": sum(1 for thread in threads if thread.get("isResolved"))},
        "new_comments": [
            {"author": login(comment), "at": comment["createdAt"], "body": comment["body"], "url": comment.get("url")}
            for comment in pr.get("comments") or [] if comment["createdAt"] > since
        ],
        "new_reviews": [
            {"author": login(review), "at": review["submittedAt"], "state": review["state"], "body": review.get("body", "")}
            for review in pr.get("reviews") or [] if review.get("submittedAt") and review["submittedAt"] > since
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", help="PR number or URL; defaults to the current branch's PR")
    parser.add_argument("--repo", help="owner/name; defaults to the current repository")
    parser.add_argument("--since", help="ISO-8601 cut for new comments; defaults to the head commit time")
    args = parser.parse_args(argv)
    scope = ["--repo", args.repo] if args.repo else []
    pr = gh("pr", "view", *([args.pr] if args.pr else []), *scope, "--json", PR_FIELDS)
    owner, name = pr["url"].split("/")[3:5]
    since = args.since or max(commit["committedDate"] for commit in pr["commits"])
    json.dump(summarize(pr, fetch_threads(owner, name, pr["number"]), since), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
