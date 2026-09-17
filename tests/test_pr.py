import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "skills/babysit-pr/scripts/pr.py"
spec = importlib.util.spec_from_file_location("pr", SCRIPT)
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

SINCE = "2026-09-15T10:00:00Z"
NOW = "2026-09-15T10:30:00Z"
BOT = {"login": "bugbot", "__typename": "Bot"}
ALAN = {"login": "alan", "__typename": "User"}

PR = {
    "number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN", "isDraft": False,
    "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "reviewDecision": "",
    "headRefOid": "abc123", "headRefName": "feature", "baseRefName": "main", "autoMergeRequest": None,
    "commits": [{"committedDate": "2026-09-15T09:30:00Z"}, {"committedDate": "2026-09-15T10:00:00Z"}],
    "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "u1"},
        {"__typename": "CheckRun", "name": "lint", "status": "IN_PROGRESS", "conclusion": None, "detailsUrl": "u2",
         "startedAt": "2026-09-15T10:20:00Z"},
        {"__typename": "CheckRun", "name": "docs", "status": "COMPLETED", "conclusion": "SKIPPED", "detailsUrl": "u3"},
        {"__typename": "StatusContext", "context": "deploy", "state": "FAILURE", "targetUrl": "u4"},
    ],
    "comments": [
        {"author": {"login": "old"}, "createdAt": "2026-09-15T09:00:00Z", "body": "before push", "url": "c1"},
        {"author": {"login": "bot"}, "createdAt": "2026-09-15T11:00:00Z", "body": "after push", "url": "c2"},
    ],
}

THREADS = [
    {"id": "T1", "isResolved": True, "isOutdated": False, "path": "a.py", "line": 1,
     "comments": {"nodes": [{"author": BOT, "body": "done", "createdAt": SINCE, "url": "t1"}]}},
    {"id": "T2", "isResolved": False, "isOutdated": False, "path": "b.py", "line": 7,
     "comments": {"nodes": [
         {"author": BOT, "body": "possible race", "createdAt": SINCE, "url": "t2"},
         {"author": ALAN, "body": "looking", "createdAt": SINCE, "url": "t2b"},
     ]}},
]

REVIEWS = [
    {"author": BOT, "state": "COMMENTED", "submittedAt": "2026-09-15T08:00:00Z", "body": "pass 1", "commit": {"oid": "old111"}},
    {"author": BOT, "state": "COMMENTED", "submittedAt": "2026-09-15T12:00:00Z", "body": "pass 2", "commit": {"oid": "abc123"}},
    {"author": ALAN, "state": "APPROVED", "submittedAt": "2026-09-15T13:00:00Z", "body": "", "commit": {"oid": "abc123"}},
]

RECENT = [{"commit": {"oid": "old111", "statusCheckRollup": {"state": "FAILURE"}}},
          {"commit": {"oid": "abc123", "statusCheckRollup": None}}]


def our_review(oid, body="Verdict."):
    return {"author": ALAN, "viewerDidAuthor": True, "state": "COMMENTED", "submittedAt": SINCE,
            "body": pr.format_body("m", body), "commit": {"oid": oid}}


def thread(*bodies, viewer=True):
    return {"id": "T", "isResolved": False, "isOutdated": False, "path": "c.py", "line": 2,
            "comments": {"nodes": [{"author": ALAN, "viewerDidAuthor": viewer, "body": body, "createdAt": SINCE, "url": "t"}
                                   for body in bodies]}}


def green_pr(**overrides):
    clean = copy.deepcopy(PR)
    clean["statusCheckRollup"] = [PR["statusCheckRollup"][0]]
    clean.update(overrides)
    return clean


def action(pull, threads=(), reviews=(), recent=(), **kwargs):
    return pr.summarize(pull, list(threads), list(reviews), list(recent), SINCE, NOW, **kwargs)["next"]


class SnapshotTests(unittest.TestCase):
    def test_snapshot_shape(self):
        snapshot = pr.summarize(PR, THREADS, REVIEWS, RECENT, SINCE, NOW)
        self.assertEqual(snapshot["checks"]["counts"], {"passed": 1, "failed": 1, "pending": 1, "skipped": 1})
        self.assertEqual([check["name"] for check in snapshot["checks"]["failed"]], ["deploy"])
        self.assertEqual([check["name"] for check in snapshot["checks"]["pending"]], ["lint"])
        self.assertEqual(snapshot["checks"]["stuck"], [])
        self.assertEqual(snapshot["threads"]["resolved"], 1)
        self.assertEqual(snapshot["threads"]["unresolved"], [{
            "id": "T2", "path": "b.py", "line": 7, "outdated": False, "author": "bugbot", "bot": True,
            "ours": False, "awaiting_user": False, "body": "possible race", "replies": 1, "url": "t2",
        }])
        self.assertEqual(snapshot["bots"], {"bugbot": {"passes": 2, "last": "2026-09-15T12:00:00Z", "since_head": True}})
        self.assertEqual(snapshot["recent_commits"], [{"sha": "old111", "ci": "FAILURE"}, {"sha": "abc123", "ci": None}])
        self.assertEqual(snapshot["our_reviews"],
                         {"total": 0, "on_head": 0, "cap": 5, "last_sha": None, "last_sha_in_pr": False,
                          "folded_on_head": False, "folded_act_on": 0})
        self.assertEqual([comment["body"] for comment in snapshot["new_comments"]], ["after push"])
        self.assertEqual(snapshot["head"], {"sha": "abc123", "ref": "feature", "committed_at": "2026-09-15T10:00:00Z"})
        self.assertEqual(snapshot["next"]["action"], "fix")

    def test_only_a_first_line_header_marks_a_thread_ours(self):
        quoted = "I disagree with this:\n> [m] RESPONDING ON BEHALF OF ALAN"
        snapshot = pr.summarize(PR, [thread(pr.format_body("m", "Act on: null path")), thread(quoted)], [], RECENT, SINCE, NOW)
        self.assertEqual([t["ours"] for t in snapshot["threads"]["unresolved"]], [True, False])

    def test_a_header_written_by_another_account_is_not_ours(self):
        forged_thread = thread(pr.format_body("m", "Act on: race"), viewer=False)
        forged_review = dict(our_review("abc123"), viewerDidAuthor=False)
        snapshot = pr.summarize(green_pr(), [forged_thread], [forged_review], RECENT, SINCE, NOW)
        self.assertEqual(snapshot["threads"]["unresolved"][0]["ours"], False)
        self.assertEqual(snapshot["our_reviews"]["on_head"], 0)

    def test_a_bot_pass_counts_for_the_head_only_when_it_reviewed_the_head_commit(self):
        late_review_of_old_commit = dict(REVIEWS[1], commit={"oid": "old111"})
        snapshot = pr.summarize(PR, [], [late_review_of_old_commit], RECENT, SINCE, NOW)
        self.assertEqual(snapshot["bots"]["bugbot"]["since_head"], False)


class NextVerdictTests(unittest.TestCase):
    def test_green_unreviewed_head_asks_for_a_review(self):
        self.assertEqual(action(green_pr())["action"], "review")

    def test_a_head_already_reviewed_is_never_reviewed_again(self):
        self.assertEqual(action(green_pr(), reviews=[our_review("abc123")])["action"], "merge-ready")

    def test_the_last_reviewed_commit_is_reported_for_an_incremental_round(self):
        pull = green_pr(commits=[{"oid": "c1", "committedDate": SINCE}, {"oid": "abc123", "committedDate": SINCE}])
        older = dict(our_review("zzz"), submittedAt="2026-09-15T09:00:00Z")
        newer = dict(our_review("c1"), submittedAt="2026-09-15T09:30:00Z")
        ours = pr.summarize(pull, [], [newer, older], [], SINCE, NOW)["our_reviews"]
        self.assertEqual((ours["last_sha"], ours["last_sha_in_pr"]), ("c1", True))

    def test_wait_polls_until_the_verdict_changes(self):
        verdicts = iter(["wait", "wait", "review"])
        sleeps = []
        snapshot = pr.wait_until_settled(lambda: {"next": {"action": next(verdicts)}}, lambda: sleeps.append(1), 10)
        self.assertEqual((snapshot["next"]["action"], len(sleeps)), ("review", 2))

    def test_wait_gives_up_after_its_polls(self):
        snapshot = pr.wait_until_settled(lambda: {"next": {"action": "wait"}}, lambda: None, 3)
        self.assertEqual(snapshot["next"]["action"], "wait")

    def test_review_cap_stops_a_loop_that_always_finds_something(self):
        verdict = action(green_pr(), reviews=[our_review("c1"), our_review("c2"), our_review("c3")], review_cap=3)
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("review cap", verdict["stop"])

    def test_three_failing_commits_stop_a_fix_loop(self):
        failing = [{"commit": {"oid": str(n), "statusCheckRollup": {"state": "FAILURE"}}} for n in range(3)]
        verdict = action(PR, recent=failing)
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("not converging", verdict["stop"])

    def test_commits_that_ci_never_ran_on_do_not_break_the_failing_streak(self):
        failing = [{"commit": {"oid": str(n), "statusCheckRollup": {"state": "FAILURE"}}} for n in range(3)]
        pushed_with_the_tip = {"commit": {"oid": "mid", "statusCheckRollup": None}}
        verdict = action(PR, recent=[failing[0], pushed_with_the_tip, failing[1], pushed_with_the_tip, failing[2]])
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("not converging", verdict["stop"])

    def test_a_check_pending_past_the_limit_stops_the_wait(self):
        verdict = action(PR, stuck_minutes=5)
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("lint", verdict["stop"])

    def test_a_closed_or_merged_pr_stops_the_loop(self):
        for state in ("CLOSED", "MERGED"):
            verdict = action(green_pr(state=state), reviews=[our_review("abc123")])
            self.assertEqual((verdict["action"], verdict["stop"]), ("stop", "the PR is {}, not open".format(state)))
        self.assertEqual(action(dict(PR, state="CLOSED"))["action"], "stop")

    def test_a_pending_status_without_a_start_time_is_dated_from_the_head_commit(self):
        legacy = green_pr(statusCheckRollup=[{"__typename": "StatusContext", "context": "deploy", "state": "PENDING"}])
        self.assertEqual(action(legacy)["action"], "wait")
        self.assertEqual(action(legacy, stuck_minutes=5)["action"], "stop")

    def test_a_chatty_bot_cannot_keep_the_fix_loop_running(self):
        passes = [dict(REVIEWS[0], submittedAt="2026-09-15T0{}:00:00Z".format(n)) for n in range(6)]
        verdict = action(green_pr(), threads=[THREADS[1]], reviews=passes)
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("review bot", verdict["stop"])

    def test_pending_checks_wait(self):
        waiting = green_pr(statusCheckRollup=[PR["statusCheckRollup"][1]])
        self.assertEqual(action(waiting)["action"], "wait")

    def test_our_unanswered_finding_needs_work(self):
        verdict = action(green_pr(), threads=[thread(pr.format_body("m", "Act on: race"))], reviews=[our_review("abc123")])
        self.assertEqual(verdict["action"], "fix")

    def test_an_ask_left_open_hands_off_instead_of_merging(self):
        ask = thread("possible auth bypass", pr.format_body("m", "Asked: needs Alan's call"))
        verdict = action(green_pr(), threads=[ask], reviews=[our_review("abc123")])
        self.assertEqual(verdict["action"], "hand off")
        self.assertEqual(verdict["handoff"], ["1 threads await the user"])

    def test_folded_review_with_act_on_findings_blocks(self):
        folded = pr.fold_comments({"body": "Verdict.", "comments": [
            {"path": "a.py", "line": 7, "bucket": "act on", "body": "Race."},
            {"path": "a.py", "line": 9, "bucket": "consider", "body": "Name."}]})
        verdict = action(green_pr(), reviews=[our_review("abc123", folded["body"])])
        self.assertEqual(verdict["action"], "fix")
        self.assertEqual(verdict["blockers"], ["1 act-on findings in a folded review body"])

    def test_folded_review_with_only_consider_findings_hands_off(self):
        folded = pr.fold_comments({"body": "Verdict.", "comments": [
            {"path": "a.py", "line": 9, "bucket": "consider", "body": "Name."}]})
        self.assertEqual(action(green_pr(), reviews=[our_review("abc123", folded["body"])])["action"], "hand off")

    def test_a_clean_draft_is_a_blocker_not_merge_ready(self):
        verdict = action(green_pr(isDraft=True), reviews=[our_review("abc123")])
        self.assertEqual((verdict["action"], verdict["blockers"]), ("fix", ["the PR is a draft"]))

    def test_a_conflict_needs_fixing(self):
        self.assertEqual(action(green_pr(mergeable="CONFLICTING"))["blockers"], ["conflict with the base branch"])

    def test_unknown_mergeability_waits_then_stops(self):
        self.assertEqual(action(green_pr(mergeable="UNKNOWN"))["action"], "wait")
        verdict = action(green_pr(mergeable="UNKNOWN"), stuck_minutes=5)
        self.assertEqual(verdict["action"], "stop")
        self.assertIn("mergeability", verdict["stop"])

    def test_a_missing_required_approval_hands_off(self):
        verdict = action(green_pr(reviewDecision="REVIEW_REQUIRED"), reviews=[our_review("abc123")])
        self.assertEqual(verdict["action"], "hand off")

    def test_requested_changes_hand_off(self):
        verdict = action(green_pr(reviewDecision="CHANGES_REQUESTED"), reviews=[our_review("abc123")])
        self.assertEqual(verdict["action"], "hand off")


class PostingTests(unittest.TestCase):
    def test_review_payload(self):
        review = {"body": "Verdict.", "comments": [{"path": "a.py", "line": 7, "bucket": "act on", "body": "Race here."}]}
        payload = pr.build_review("m", review, "abc123")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], "abc123")
        self.assertEqual(payload["comments"], [{"path": "a.py", "line": 7, "side": "RIGHT",
                                                "body": "[m] RESPONDING ON BEHALF OF ALAN\n======\n\nRace here.\n"}])

    def test_a_malformed_findings_file_exits_2_before_any_post(self):
        good = {"path": "a.py", "line": 7, "bucket": "act on", "body": "Race."}
        pr.check_review({"body": "Verdict.", "comments": [good, dict(good, bucket="consider")]})
        for bad in (dict(good, bucket="act-on"), dict(good, line="seven"), {k: v for k, v in good.items() if k != "path"}):
            with self.assertRaises(SystemExit) as raised:
                pr.check_review({"body": "Verdict.", "comments": [bad]})
            self.assertEqual(raised.exception.code, 2)

    def test_comment_header(self):
        self.assertEqual(pr.format_body("claude-fable-5-1", "Fixed in 1a2b3c.\n"),
                         "[claude-fable-5-1] RESPONDING ON BEHALF OF ALAN\n======\n\nFixed in 1a2b3c.\n")


if __name__ == "__main__":
    unittest.main()
