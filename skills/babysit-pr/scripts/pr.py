#!/usr/bin/env python3
"""GitHub pull request helper for the babysit-pr skill.

Reads and writes through the GitHub CLI (gh). Reply bodies come from a file
and reach gh as JSON on stdin, so no comment text passes through a shell.

    pr.py status  [--pr N] [--repo o/r] [--since ISO]
    pr.py reply   --thread <id> --body-file <file> --model <id>
    pr.py resolve --thread <id>

Exit code 2 means gh itself failed (auth, network, rate limit). Retry once
before treating that as a result. Reviews are read up to the first 100.
"""

import argparse
import json
import subprocess
import sys

ON_BEHALF_OF = "ALAN"

PR_FIELDS = ("number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,"
             "headRefOid,headRefName,baseRefName,commits,statusCheckRollup,comments,autoMergeRequest")

# Thread resolution and author kinds are only exposed through GraphQL.
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          comments(first: 50) { nodes { author { login __typename } body createdAt url } }
        }
      }
      reviews(first: 100) { nodes { author { login __typename } state submittedAt body } }
    }
  }
}
"""

REPLY_MUTATION = """
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) {
    comment { url }
  }
}
"""

RESOLVE_MUTATION = """
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""

PASSED = {"SUCCESS", "NEUTRAL"}
PENDING = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", None}


def gh(args, stdin=None):
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        print("gh {} failed: {}".format(" ".join(args[:2]), result.stderr.strip()), file=sys.stderr)
        sys.exit(2)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def graphql(query, **variables):
    data = gh(["api", "graphql", "--input", "-"], stdin=json.dumps({"query": query, "variables": variables}))
    if data.get("errors"):
        sys.exit("GraphQL error: {}".format(data["errors"][0].get("message")))
    return data["data"]


def fetch_pr(args):
    scope = ["--repo", args.repo] if args.repo else []
    return gh(["pr", "view", *([args.pr] if args.pr else []), *scope, "--json", PR_FIELDS])


def fetch_threads(owner, name, number):
    nodes, reviews, after = [], None, None
    while True:
        pull = graphql(THREADS_QUERY, owner=owner, name=name, number=number, after=after)["repository"]["pullRequest"]
        threads = pull["reviewThreads"]
        nodes += threads["nodes"]
        reviews = reviews if reviews is not None else pull["reviews"]["nodes"]
        if not threads["pageInfo"]["hasNextPage"]:
            return nodes, reviews
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


def is_bot(node):
    return (node.get("author") or {}).get("__typename") == "Bot"


def summarize(pr, threads, reviews, since):
    """Pure: shape the gh payloads into the snapshot. `since` is the ISO-8601 UTC cut for new activity."""
    checks = [classify_check(check) for check in pr.get("statusCheckRollup") or []]
    unresolved = []
    for thread in threads:
        if thread.get("isResolved"):
            continue
        comments = thread["comments"]["nodes"]
        first = comments[0] if comments else {}
        unresolved.append({
            "id": thread["id"], "path": thread.get("path"), "line": thread.get("line"),
            "outdated": bool(thread.get("isOutdated")), "author": login(first), "bot": is_bot(first),
            "body": first.get("body", ""), "replies": max(len(comments) - 1, 0), "url": first.get("url"),
        })
    bots = {}
    for review in reviews:
        if is_bot(review) and review.get("submittedAt"):
            entry = bots.setdefault(login(review), {"passes": 0, "last": None, "since_head": False})
            entry["passes"] += 1
            entry["last"] = max(entry["last"] or "", review["submittedAt"])
            entry["since_head"] = entry["since_head"] or review["submittedAt"] > since
    return {
        "number": pr["number"], "url": pr["url"], "state": pr["state"], "draft": pr["isDraft"],
        "mergeable": pr["mergeable"], "merge_state": pr["mergeStateStatus"],
        "review_decision": pr.get("reviewDecision") or None,
        "auto_merge": bool(pr.get("autoMergeRequest")),
        "base": pr["baseRefName"],
        "head": {"sha": pr["headRefOid"], "ref": pr["headRefName"],
                 "committed_at": max(commit["committedDate"] for commit in pr["commits"])},
        "since": since,
        "checks": {
            "counts": {kind: sum(1 for check in checks if check["kind"] == kind)
                       for kind in ("passed", "failed", "pending", "skipped")},
            "failed": [check for check in checks if check["kind"] == "failed"],
            "pending": [check for check in checks if check["kind"] == "pending"],
        },
        "threads": {"unresolved": unresolved,
                    "resolved": sum(1 for thread in threads if thread.get("isResolved"))},
        "bots": bots,
        "new_comments": [
            {"author": login(comment), "at": comment["createdAt"], "body": comment["body"], "url": comment.get("url")}
            for comment in pr.get("comments") or [] if comment["createdAt"] > since
        ],
        "new_reviews": [
            {"author": login(review), "bot": is_bot(review), "at": review["submittedAt"],
             "state": review["state"], "body": review.get("body", "")}
            for review in reviews if review.get("submittedAt") and review["submittedAt"] > since
        ],
    }


def format_body(model, body):
    """Every reply an agent posts carries this header so readers know who wrote it."""
    return "[{}] RESPONDING ON BEHALF OF {}\n======\n\n{}".format(model, ON_BEHALF_OF, body.rstrip("\n") + "\n")


def command_status(args):
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    since = args.since or max(commit["committedDate"] for commit in pr["commits"])
    threads, reviews = fetch_threads(owner, name, pr["number"])
    json.dump(summarize(pr, threads, reviews, since), sys.stdout, indent=2)
    print()


def command_reply(args):
    with open(args.body_file, encoding="utf-8") as handle:
        body = format_body(args.model, handle.read())
    result = graphql(REPLY_MUTATION, thread=args.thread, body=body)
    print(result["addPullRequestReviewThreadReply"]["comment"]["url"])


def command_resolve(args):
    result = graphql(RESOLVE_MUTATION, thread=args.thread)
    print("resolved" if result["resolveReviewThread"]["thread"]["isResolved"] else "not resolved")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="print one JSON snapshot of the PR")
    status.add_argument("--pr", help="PR number or URL; defaults to the current branch's PR")
    status.add_argument("--repo", help="owner/name; defaults to the current repository")
    status.add_argument("--since", help="ISO-8601 cut for new activity; defaults to the head commit time")
    status.set_defaults(run=command_status)

    reply = commands.add_parser("reply", help="reply on a review thread from a file")
    reply.add_argument("--thread", required=True, help="thread id from the status snapshot")
    reply.add_argument("--body-file", required=True)
    reply.add_argument("--model", required=True, help="model id for the on-behalf-of header")
    reply.set_defaults(run=command_reply)

    resolve = commands.add_parser("resolve", help="resolve a review thread")
    resolve.add_argument("--thread", required=True, help="thread id from the status snapshot")
    resolve.set_defaults(run=command_resolve)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
