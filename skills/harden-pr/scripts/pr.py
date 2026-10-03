#!/usr/bin/env python3
"""GitHub state, review history, and posting for harden-pr and review-pr.

Bodies come from files and reach gh as JSON on stdin. Reviews are attributed
and posted against the commit actually reviewed. GitHub can reject inline
locations; review then folds findings into the body and reports that fallback.

Every posted body starts with:

    [<model id>] on behalf of <first name from git config user.name>

Status reports observations, including review coverage and stalled checks.
Wait polls pending checks and mergeability for a bounded interval. It returns
on changed state that needs attention; callers assess the resulting snapshot.
Exit code 2 means the API failed or the supplied review was invalid.
"""

import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys
import time

HEADER_MARK = "on behalf of "
LEGACY_HEADER_MARK = "RESPONDING ON BEHALF OF "
ATTRIBUTION_HEADER = re.compile(
    r"\[[^\]\r\n]+\] (?:" + HEADER_MARK + r"\S+|" + LEGACY_HEADER_MARK + r"\S[^\r\n]*)"
)
FOLD_MARK = "Findings on lines outside the diff:"
CHECK_GRACE_MINUTES = 10
PUSH_LISTING_TRIES = 3
PUSH_LISTING_RETRY_SECONDS = 2
FOLDED_ACT_ON = re.compile(r"^Folded act-on findings: (\d+)\r?$", re.M)
FOLDED_FINDING = re.compile(r"^.+:\d+ \((act on|consider)\)\r?$", re.M)

PR_FIELDS = ("number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,"
             "headRefOid,headRefName,headRepository,baseRefName,commits,statusCheckRollup,comments")

# Thread resolution, author kinds, the commit a review covers, and per-commit CI are GraphQL-only.
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          comments(first: 50) { nodes { author { login __typename } viewerDidAuthor body createdAt url } }
        }
      }
      reviews(last: 100) { nodes {
        author { login __typename } viewerDidAuthor state submittedAt body commit { oid }
        comments(first: 1) { nodes { viewerDidAuthor body replyTo { id } } }
      } }
      commits(last: 20) { nodes { commit { oid statusCheckRollup { state } } } }
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


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(2)


def gh(args, stdin=None):
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        fail("gh {} failed: {}".format(" ".join(args[:2]), result.stderr.strip()))
    return json.loads(result.stdout) if result.stdout.strip() else {}


def graphql(query, **variables):
    data = gh(["api", "graphql", "--input", "-"], stdin=json.dumps({"query": query, "variables": variables}))
    if data.get("errors"):
        fail("GraphQL error: {}".format(data["errors"][0].get("message")))
    return data["data"]


def fetch_pr(args):
    scope = ["--repo", args.repo] if args.repo else []
    return gh(["pr", "view", *([args.pr] if args.pr else []), *scope, "--json", PR_FIELDS])


def fetch_threads(owner, name, number):
    nodes, reviews, recent, after = [], None, None, None
    while True:
        pull = graphql(THREADS_QUERY, owner=owner, name=name, number=number, after=after)["repository"]["pullRequest"]
        threads = pull["reviewThreads"]
        nodes += threads["nodes"]
        if reviews is None:
            reviews, recent = pull["reviews"]["nodes"], pull["commits"]["nodes"]
        if not threads["pageInfo"]["hasNextPage"]:
            return nodes, reviews, recent
        after = threads["pageInfo"]["endCursor"]


def fetch_pushed_at(pr, run=subprocess.run, sleep=time.sleep):
    repo = (pr.get("headRepository") or {}).get("nameWithOwner")
    if not repo:
        return None
    for attempt in range(PUSH_LISTING_TRIES):
        if attempt:
            sleep(PUSH_LISTING_RETRY_SECONDS)
        result = run(["gh", "api", "--method", "GET", "repos/{}/activity".format(repo),
                      "-f", "ref=refs/heads/" + pr["headRefName"], "-F", "per_page=1"],
                     capture_output=True, text=True)
        pushes = json.loads(result.stdout or "[]") if result.returncode == 0 else []
        if not pushes:
            return None
        if pushes[0]["after"] == pr["headRefOid"]:
            return pushes[0]["timestamp"]
    return None


def classify_check(check):
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
    return {"name": name, "kind": kind, "state": state, "link": link,
            "started_at": check.get("startedAt") or check.get("createdAt")}


def login(node):
    return (node.get("author") or {}).get("login")


def is_bot(node):
    return (node.get("author") or {}).get("__typename") == "Bot"


def is_ours(node):
    first = (node.get("body") or "").split("\n", 1)[0].strip()
    return bool(node.get("viewerDidAuthor")) and (
        ATTRIBUTION_HEADER.fullmatch(first) is not None
        or any(not comment.get("replyTo") and is_ours(comment) for comment in (node.get("comments") or {}).get("nodes", [])))


def minutes_between(start, now):
    begun = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return (datetime.fromisoformat(now.replace("Z", "+00:00")) - begun).total_seconds() / 60


def summarize(pr, threads, reviews, recent, since, now, stuck_minutes=60, pushed_at=None):
    head = pr["headRefOid"]
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
            "ours": is_ours(first),
            "body": first.get("body", ""), "replies": max(len(comments) - 1, 0), "url": first.get("url"),
        })
    bots = {}
    for review in reviews:
        if is_bot(review) and review.get("submittedAt"):
            entry = bots.setdefault(login(review), {"passes": 0, "last": None, "since_head": False})
            entry["passes"] += 1
            entry["last"] = max(entry["last"] or "", review["submittedAt"])
            entry["since_head"] = entry["since_head"] or (review.get("commit") or {}).get("oid") == head
    ours = [review for review in reviews if is_ours(review)]
    on_head = [review for review in ours if (review.get("commit") or {}).get("oid") == head]
    latest = max(ours, key=lambda review: review.get("submittedAt") or "", default=None)
    last_sha = (latest.get("commit") or {}).get("oid") if latest else None
    folded_act_on = sum(int(found) for review in on_head for found in FOLDED_ACT_ON.findall(review.get("body", "")))
    folded_act_on += sum(FOLDED_FINDING.findall(review.get("body", "")).count("act on")
                         for review in on_head if FOLD_MARK not in review.get("body", ""))
    committed_at = max(commit["committedDate"] for commit in pr["commits"])
    head_time = pushed_at or committed_at
    pending = [check for check in checks if check["kind"] == "pending"]
    snapshot = {
        "number": pr["number"], "url": pr["url"], "state": pr["state"], "draft": pr["isDraft"],
        "mergeable": pr["mergeable"], "merge_state": pr["mergeStateStatus"],
        "review_decision": pr.get("reviewDecision") or None,
        "base": pr["baseRefName"],
        "head": {"sha": head, "ref": pr["headRefName"],
                 "committed_at": committed_at, "pushed_at": pushed_at},
        "since": since,
        "mergeable_overdue": pr["mergeable"] == "UNKNOWN" and minutes_between(head_time, now) > stuck_minutes,
        "checks": {
            "counts": {kind: sum(1 for check in checks if check["kind"] == kind)
                       for kind in ("passed", "failed", "pending", "skipped")},
            "failed": [check for check in checks if check["kind"] == "failed"],
            "pending": pending,
            "stuck": [check for check in pending
                      if minutes_between(check["started_at"] or head_time, now) > stuck_minutes],
            "unregistered": not checks and minutes_between(head_time, now) <= CHECK_GRACE_MINUTES and any(
                node["commit"]["oid"] != head and node["commit"].get("statusCheckRollup") for node in recent),
        },
        "recent_commits": [{"sha": node["commit"]["oid"],
                            "ci": (node["commit"].get("statusCheckRollup") or {}).get("state")} for node in recent],
        "threads": {"unresolved": unresolved,
                    "resolved": sum(1 for thread in threads if thread.get("isResolved"))},
        "our_reviews": {"total": len(ours), "on_head": len(on_head),
                        "last_sha": last_sha,
                        "last_sha_in_pr": last_sha is not None and last_sha in {commit.get("oid") for commit in pr["commits"]},
                        "folded_on_head": any(FOLD_MARK in review.get("body", "") or FOLDED_FINDING.search(review.get("body", ""))
                                              for review in on_head),
                        "folded_act_on": folded_act_on},
        "bots": bots,
        "new_comments": [
            {"author": login(comment), "at": comment["createdAt"], "body": comment["body"], "url": comment.get("url")}
            for comment in pr.get("comments") or [] if comment["createdAt"] > since
        ],
        "new_reviews": [
            {"author": login(review), "bot": is_bot(review), "ours": is_ours(review),
             "at": review["submittedAt"], "state": review["state"], "body": review.get("body", "")}
            for review in reviews if review.get("submittedAt") and review["submittedAt"] > since
            and (review.get("body") or is_ours(review) or not review.get("viewerDidAuthor"))
        ],
    }
    return snapshot



def git_user_name():
    result = subprocess.run(["git", "config", "--get", "user.name"], capture_output=True, text=True)
    name = result.stdout.strip()
    if result.returncode or not name or len(name.splitlines()) != 1:
        fail("Set git config user.name to a nonempty, single-line name before posting a review or reply.")
    return name


def format_body(model, body, name):
    first_name = name.split(maxsplit=1)[0]
    return "[{}] {}{}\n\n{}".format(model, HEADER_MARK, first_name, body.rstrip("\n") + "\n")


def build_review(model, review, commit, name):
    payload = {
        "event": "COMMENT", "commit_id": commit,
        "comments": [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": format_body(model, c["body"], name)}
                     for c in review.get("comments", [])],
    }
    if review["body"].strip():
        payload["body"] = format_body(model, review["body"], name)
    return payload


def check_review(review):
    if not isinstance(review, dict) or not isinstance(review.get("body"), str):
        fail("review file: it must be an object whose body is a string")
    if not isinstance(review.get("comments", []), list):
        fail("review file: comments must be a list")
    for index, c in enumerate(review.get("comments", [])):
        well_formed = (isinstance(c, dict) and isinstance(c.get("path"), str) and type(c.get("line")) is int
                       and isinstance(c.get("body"), str) and c["body"].strip() and c.get("bucket") in ("act on", "consider"))
        if not well_formed:
            fail("review file: comment {} needs a path, an integer line, a body, and a bucket of "
                 "'act on' or 'consider': {}".format(index, json.dumps(c)))


def fold_comments(review):
    comments = review.get("comments", [])
    moved = "\n\n".join("{}:{} ({})\n{}".format(c["path"], c["line"], c["bucket"], c["body"]) for c in comments)
    return {"body": "\n\n".join(part for part in (review["body"].strip(), moved) if part), "comments": []}


def take_snapshot(args):
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    since = getattr(args, "since", None) or max(commit["committedDate"] for commit in pr["commits"])
    threads, reviews, recent = fetch_threads(owner, name, pr["number"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return summarize(pr, threads, reviews, recent, since, now, args.stuck_minutes,
                     fetch_pushed_at(pr))


def command_status(args):
    json.dump(take_snapshot(args), sys.stdout, indent=2)
    print()


def wait_until_settled(snapshot_once, sleep, clock, seconds, interval):
    deadline = clock() + seconds
    snapshot = snapshot_once()
    head = snapshot["head"]["sha"]
    while (snapshot["state"] == "OPEN" and snapshot["head"]["sha"] == head
           and not snapshot["checks"]["failed"] and not snapshot["checks"]["stuck"]
           and not snapshot["mergeable_overdue"]
           and (snapshot["checks"]["pending"] or snapshot["checks"]["unregistered"]
                or snapshot["mergeable"] == "UNKNOWN")):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
        snapshot = snapshot_once()
    return snapshot


def command_wait(args):
    snapshot = wait_until_settled(lambda: take_snapshot(args), time.sleep, time.monotonic,
                                  args.max_minutes * 60, max(args.interval, 1))
    json.dump(snapshot, sys.stdout, indent=2)
    print()


def signed(node):
    by = "ours" if is_ours(node) else "this account, by hand" if node.get("viewerDidAuthor") else "another account"
    return "{} ({})".format(login(node), by)


def shown(node):
    body = (node.get("body") or "").strip()
    if node.get("viewerDidAuthor"):
        return body
    return "\n".join(("> " + line).rstrip() for line in body.split("\n"))


def render_history(threads, reviews):
    lines = ["# Earlier review rounds on this PR", ""]
    for review in reviews:
        if review.get("body"):
            lines += ["## Review by {} of {} at {}".format(signed(review), (review.get("commit") or {}).get("oid", "?")[:7],
                                                         review.get("submittedAt")), "", shown(review), ""]
    lines += ["## Threads", ""]
    for thread in threads:
        comments = thread["comments"]["nodes"]
        if not comments:
            continue
        lines.append("### {}:{} ({})".format(thread.get("path"), thread.get("line") or "outdated line",
                                             "resolved" if thread.get("isResolved") else "open"))
        for comment in comments:
            lines += ["", "**{}**:".format(signed(comment)), "", shown(comment)]
        lines.append("")
    return "\n".join(lines) + "\n"


def command_history(args):
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    threads, reviews, _ = fetch_threads(owner, name, pr["number"])
    sys.stdout.write(render_history(threads, reviews))


def command_reply(args):
    identity = git_user_name()
    with open(args.body_file, encoding="utf-8") as handle:
        body = format_body(args.model, handle.read(), identity)
    result = graphql(REPLY_MUTATION, thread=args.thread, body=body)
    print(result["addPullRequestReviewThreadReply"]["comment"]["url"])


def command_review(args):
    identity = git_user_name()
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    with open(args.review_file, encoding="utf-8") as handle:
        review = json.load(handle)
    check_review(review)
    if not review["body"].strip() and not review.get("comments"):
        json.dump({"url": None, "inline": 0, "folded": False}, sys.stdout)
        print()
        return
    endpoint = "repos/{}/{}/pulls/{}/reviews".format(owner, name, pr["number"])
    wanted = len(review.get("comments", []))
    if args.commit not in {commit.get("oid") for commit in pr["commits"]}:
        fail("commit {} is not in the PR; review the current commits again".format(args.commit))
    for attempt in (review, fold_comments(review)):
        payload = build_review(args.model, attempt, args.commit, identity)
        result = subprocess.run(["gh", "api", "--method", "POST", endpoint, "--input", "-"],
                                input=json.dumps(payload), capture_output=True, text=True)
        line_outside_diff = "422" in result.stderr or "Unprocessable" in result.stderr
        if result.returncode != 0 and not line_outside_diff:
            break
        if result.returncode == 0:
            inline = len(payload["comments"])
            json.dump({"url": json.loads(result.stdout)["html_url"], "inline": inline,
                       "folded": wanted > 0 and inline == 0}, sys.stdout)
            print()
            return
        if not payload["comments"]:
            break
    fail("gh api {} failed: {}".format(endpoint, result.stderr.strip()))


def command_resolve(args):
    result = graphql(RESOLVE_MUTATION, thread=args.thread)
    if not result["resolveReviewThread"]["thread"]["isResolved"]:
        fail("thread {} did not resolve".format(args.thread))
    print("resolved")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def scoped(name, **kwargs):
        sub = commands.add_parser(name, **kwargs)
        sub.add_argument("--pr", help="PR number or URL; defaults to the current branch's PR")
        sub.add_argument("--repo", help="owner/name; defaults to the current repository")
        return sub

    status = scoped("status", help="print one JSON snapshot of the PR")
    status.add_argument("--since", help="ISO-8601 cut for new activity; defaults to the head commit time")
    status.set_defaults(run=command_status)

    wait = scoped("wait", help="wait for checks or mergeability to settle, then print the snapshot")
    wait.add_argument("--max-minutes", type=float, default=0.5, help="return after this long even if still waiting")
    wait.add_argument("--interval", type=int, default=30, help="seconds between polls")
    wait.set_defaults(run=command_wait)

    for sub in (status, wait):
        sub.add_argument("--stuck-minutes", type=int, default=60, help="a check pending longer than this is stuck")

    history = scoped("history", help="print earlier review bodies and threads as Markdown")
    history.set_defaults(run=command_history)

    reply = commands.add_parser("reply", help="reply on a review thread from a file")
    reply.add_argument("--thread", required=True, help="thread id from the status snapshot")
    reply.add_argument("--body-file", required=True)
    reply.add_argument("--model", required=True, help="model id for the on-behalf-of header")
    reply.set_defaults(run=command_reply)

    review = scoped("review", help="post a PR review with inline comments from a findings file")
    review.add_argument("--review-file", required=True, help="JSON with body and comments")
    review.add_argument("--model", required=True, help="model id for the on-behalf-of header")
    review.add_argument("--commit", required=True, help="the SHA the reviewers read; the review is posted on it")
    review.set_defaults(run=command_review)

    resolve = commands.add_parser("resolve", help="resolve a review thread")
    resolve.add_argument("--thread", required=True, help="thread id from the status snapshot")
    resolve.set_defaults(run=command_resolve)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
