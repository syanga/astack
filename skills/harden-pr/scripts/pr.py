#!/usr/bin/env python3
"""GitHub state, review history, test results, and posting for harden-pr, review-pr, and open-pr.

Bodies come from files and reach gh as JSON on stdin. Reviews are attributed
and posted against the commit the review covers. GitHub can reject inline
locations; review then folds findings into the body and reports that fallback.

A test result is a PR comment whose last line is a hidden JSON record, plus an
astack/tests commit status that links to it. A record counts only when its
author also set that status. Creating a status needs push access. The status
on a commit reflects the newest result for each suite on that commit.

Every posted body starts with:

    [<model id>] on behalf of <first name from git config user.name>

Status reports observations, including review coverage and stalled checks.
Wait polls pending checks and mergeability for a bounded interval. It returns
on changed state that needs attention; callers assess the resulting snapshot.
Exit code 2 means the API failed, the supplied review or result file was invalid,
or a precondition failed, such as a commit outside the PR or missing push access.
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
    r"\[[^\r\n]+\] (?:" + HEADER_MARK + r"\S+|" + LEGACY_HEADER_MARK + r"\S[^\r\n]*)"
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

TEST_CONTEXT = "astack/tests"
RECORD_PREFIX = "<!-- astack-test-result "
TEST_RECORD = re.compile(re.escape(RECORD_PREFIX) + r"(\{.*\}) -->")
OUTCOMES_WORST_FIRST = ("fail", "incomplete", "pass")
STATUS_BY_OUTCOME = {"fail": "failure", "incomplete": "error", "pass": "success"}
FULL_SHA = re.compile(r"[0-9a-f]{40}")
STATUS_DESCRIPTION_LIMIT = 140

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


def pr_has_commit(pr, sha):
    return sha in {commit.get("oid") for commit in pr["commits"]}


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
    return bool(node.get("viewerDidAuthor")) and ATTRIBUTION_HEADER.fullmatch(first) is not None


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
                        "last_sha_in_pr": last_sha is not None and pr_has_commit(pr, last_sha),
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
            and (review.get("body") or not review.get("viewerDidAuthor"))
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
    return {
        "event": "COMMENT", "commit_id": commit,
        "body": format_body(model, review["body"], name),
        "comments": [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": format_body(model, c["body"], name)}
                     for c in review.get("comments", [])],
    }


def load_json_file(path, label):
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as error:
            fail("{}: it is not valid JSON: {}".format(label, error))


def check_review(review):
    if not isinstance(review, dict) or not isinstance(review.get("body"), str):
        fail("review file: it must be an object whose body is a string")
    if not review["body"].strip():
        fail("review file: the body is empty; summarize the review in it, as posting.md describes")
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
    review = load_json_file(args.review_file, "review file")
    check_review(review)
    endpoint = "repos/{}/{}/pulls/{}/reviews".format(owner, name, pr["number"])
    wanted = len(review.get("comments", []))
    if not pr_has_commit(pr, args.commit):
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


def test_result_problem(result):
    if not isinstance(result, dict):
        return "it must be an object"
    for key in ("tested", "base"):
        if not isinstance(result.get(key), str) or not FULL_SHA.fullmatch(result[key]):
            return "{} must be a full 40-character commit SHA".format(key)
    for key in ("command", "summary", "scope"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            return "{} must be a nonempty string".format(key)
    if not isinstance(result.get("limits", ""), str):
        return "limits must be a string"
    suites = result.get("suites")
    if not isinstance(suites, dict) or not suites or not all(
            name.strip() and isinstance(outcome, str) and outcome in STATUS_BY_OUTCOME for name, outcome in suites.items()):
        return "suites must map each suite name to pass, fail, or incomplete"
    heads = result.get("heads", [])
    if not isinstance(heads, list) or not all(isinstance(head, str) and FULL_SHA.fullmatch(head) for head in heads):
        return "heads must be a list of full commit SHAs"
    texts = [result[key] for key in ("command", "summary", "scope", "limits") if key in result] + list(suites)
    if any("\n" in text or "\r" in text for text in texts):
        return "command, summary, scope, limits, and suite names must each fit on one line"
    return None


def fetch_pages(endpoint):
    items, page = [], 1
    while True:
        batch = gh(["api", "--method", "GET", endpoint, "-F", "per_page=100", "-F", "page={}".format(page)])
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def comments_endpoint(owner, name, number):
    return "repos/{}/{}/issues/{}/comments".format(owner, name, number)


def read_test_records(owner, name, comments):
    found, ignored = [], 0
    for comment in comments:
        lines = (comment.get("body") or "").rstrip().splitlines()
        match = TEST_RECORD.fullmatch(lines[-1]) if lines else None
        if not match:
            continue
        try:
            record = json.loads(match.group(1))
        except (ValueError, RecursionError):
            record = None
        if test_result_problem(record) or not FULL_SHA.fullmatch(str(record.get("commit"))):
            ignored += 1
            continue
        found.append((comment, record))
    statuses = {sha: fetch_pages("repos/{}/{}/commits/{}/statuses".format(owner, name, sha))
                for sha in {record["commit"] for _, record in found}}
    records = []
    for comment, record in found:
        author = (comment.get("user") or {}).get("login")
        if not any(status.get("context") == TEST_CONTEXT and status.get("target_url") == comment.get("html_url")
                   and (status.get("creator") or {}).get("login") == author for status in statuses[record["commit"]]):
            ignored += 1
            continue
        records.append(dict(record, url=comment.get("html_url"), author=author, at=comment.get("created_at")))
    records.sort(key=lambda record: record["at"] or "")
    return records, ignored


def latest_by_suite(records):
    latest = {}
    for record in records:
        for suite, outcome in record["suites"].items():
            latest[suite] = dict(record, outcome=outcome)
    return latest


def worst_outcome(outcomes):
    return next(outcome for outcome in OUTCOMES_WORST_FIRST if outcome in outcomes)


def commit_status(records):
    latest = latest_by_suite(records)
    outcomes = [entry["outcome"] for entry in latest.values()]
    counts = ", ".join("{} {}".format(outcomes.count(outcome), outcome)
                       for outcome in OUTCOMES_WORST_FIRST if outcome in outcomes)
    description = "{} {}: {}.".format(len(outcomes), "suite" if len(outcomes) == 1 else "suites", counts)
    if set(records[-1]["suites"]) == set(latest):
        description += " " + records[-1]["summary"]
    if len(description) > STATUS_DESCRIPTION_LIMIT:
        description = description[:STATUS_DESCRIPTION_LIMIT - 3] + "..."
    return STATUS_BY_OUTCOME[worst_outcome(outcomes)], description


def render_test_result(record):
    hidden = json.dumps(record, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e")
    lines = ["**Tests: {}** for `{}`. Ran on `{}` with base `{}`.".format(
        worst_outcome(record["suites"].values()), record["commit"][:12], record["tested"][:12], record["base"][:12]), "",
        "| Suite | Outcome |", "| --- | --- |"]
    lines += ["| {} | {} |".format(suite.replace("|", "\\|"), outcome) for suite, outcome in sorted(record["suites"].items())]
    lines += ["", "Result: " + record["summary"], "", "Scope: " + record["scope"]]
    if record.get("limits"):
        lines += ["", "Limits: " + record["limits"]]
    if record.get("heads"):
        lines += ["", "Heads merged onto the base: " + ", ".join("`{}`".format(head[:12]) for head in record["heads"])]
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", record["command"])), default=0) + 1)
    lines += ["", fence + "sh", record["command"], fence, "", RECORD_PREFIX + hidden + " -->"]
    return "\n".join(lines)


def command_tests(args):
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    records, ignored = read_test_records(owner, name, fetch_pages(comments_endpoint(owner, name, pr["number"])))
    json.dump({"head": pr["headRefOid"], "records": records, "latest_by_suite": latest_by_suite(records),
               "ignored": ignored}, sys.stdout, indent=2)
    print()


def command_post_tests(args):
    identity = git_user_name()
    result = load_json_file(args.result_file, "result file")
    problem = test_result_problem(result)
    if problem:
        fail("result file: " + problem)
    pr = fetch_pr(args)
    owner, name = pr["url"].split("/")[3:5]
    if not pr_has_commit(pr, args.commit):
        fail("commit {} is not in the PR; post results for a PR commit".format(args.commit))
    if not (gh(["api", "--method", "GET", "repos/{}/{}".format(owner, name)]).get("permissions") or {}).get("push"):
        fail("post-tests needs push access to {}/{} to set the {} status".format(owner, name, TEST_CONTEXT))
    record = dict(result, commit=args.commit)
    endpoint = comments_endpoint(owner, name, pr["number"])
    earlier, _ = read_test_records(owner, name, fetch_pages(endpoint))
    state, description = commit_status([entry for entry in earlier if entry["commit"] == args.commit]
                                       + [dict(record, at=None)])
    body = format_body(args.model, render_test_result(record), identity)
    comment = gh(["api", "--method", "POST", endpoint, "--input", "-"], stdin=json.dumps({"body": body}))
    status = subprocess.run(
        ["gh", "api", "--method", "POST", "repos/{}/{}/statuses/{}".format(owner, name, args.commit), "--input", "-"],
        input=json.dumps({"state": state, "context": TEST_CONTEXT, "description": description,
                          "target_url": comment["html_url"]}), capture_output=True, text=True)
    report = {"url": comment["html_url"], "commit": args.commit, "on_head": args.commit == pr["headRefOid"],
              "state": state if status.returncode == 0 else None, "description": description}
    if status.returncode != 0:
        report["status_error"] = status.stderr.strip()
    json.dump(report, sys.stdout)
    print()
    if status.returncode != 0:
        sys.exit(2)


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
    review.add_argument("--commit", required=True, help="the SHA the review covers; the review is posted on it")
    review.set_defaults(run=command_review)

    tests = scoped("tests", help="print the PR's published test results as JSON")
    tests.set_defaults(run=command_tests)

    post_tests = scoped("post-tests", help="publish a test result as a PR comment and an astack/tests status")
    post_tests.add_argument("--commit", required=True, help="the PR commit the result applies to, usually the head")
    post_tests.add_argument("--result-file", required=True, help="JSON with tested, base, command, suites, summary, and scope")
    post_tests.add_argument("--model", required=True, help="model id for the on-behalf-of header")
    post_tests.set_defaults(run=command_post_tests)

    resolve = commands.add_parser("resolve", help="resolve a review thread")
    resolve.add_argument("--thread", required=True, help="thread id from the status snapshot")
    resolve.set_defaults(run=command_resolve)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
