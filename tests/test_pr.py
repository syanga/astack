"""Shape the babysit-pr snapshot and comment bodies from recorded gh payloads, without the network."""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "skills/babysit-pr/scripts/pr.py"
spec = importlib.util.spec_from_file_location("pr", SCRIPT)
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

SINCE = "2026-09-15T10:00:00Z"
BOT = {"login": "bugbot", "__typename": "Bot"}
ALAN = {"login": "alan", "__typename": "User"}

PR = {
    "number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN", "isDraft": False,
    "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "reviewDecision": "",
    "headRefOid": "abc123", "headRefName": "feature", "baseRefName": "main", "autoMergeRequest": None,
    "commits": [{"committedDate": "2026-09-15T09:30:00Z"}, {"committedDate": "2026-09-15T10:00:00Z"}],
    "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "u1"},
        {"__typename": "CheckRun", "name": "lint", "status": "IN_PROGRESS", "conclusion": None, "detailsUrl": "u2"},
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
    {"author": BOT, "state": "COMMENTED", "submittedAt": "2026-09-15T08:00:00Z", "body": "pass 1"},
    {"author": BOT, "state": "COMMENTED", "submittedAt": "2026-09-15T12:00:00Z", "body": "pass 2"},
    {"author": ALAN, "state": "APPROVED", "submittedAt": "2026-09-15T13:00:00Z", "body": ""},
]


class SnapshotTests(unittest.TestCase):
    def test_snapshot_shape(self):
        snapshot = pr.summarize(PR, THREADS, REVIEWS, SINCE)
        self.assertEqual(snapshot["checks"]["counts"], {"passed": 1, "failed": 1, "pending": 1, "skipped": 1})
        self.assertEqual(snapshot["checks"]["failed"], [{"name": "deploy", "kind": "failed", "state": "FAILURE", "link": "u4"}])
        self.assertEqual([check["name"] for check in snapshot["checks"]["pending"]], ["lint"])
        self.assertEqual(snapshot["threads"]["resolved"], 1)
        self.assertEqual(snapshot["threads"]["unresolved"], [{
            "id": "T2", "path": "b.py", "line": 7, "outdated": False, "author": "bugbot", "bot": True,
            "body": "possible race", "replies": 1, "url": "t2",
        }])
        self.assertEqual(snapshot["bots"], {"bugbot": {"passes": 2, "last": "2026-09-15T12:00:00Z", "since_head": True}})
        self.assertEqual([comment["body"] for comment in snapshot["new_comments"]], ["after push"])
        self.assertEqual([(review["author"], review["bot"]) for review in snapshot["new_reviews"]],
                         [("bugbot", True), ("alan", False)])
        self.assertIsNone(snapshot["review_decision"])
        self.assertEqual(snapshot["head"], {"sha": "abc123", "ref": "feature", "committed_at": "2026-09-15T10:00:00Z"})
        self.assertEqual(snapshot["since"], SINCE)

    def test_comment_header(self):
        self.assertEqual(pr.format_body("claude-fable-5-1", "Fixed in 1a2b3c.\n"),
                         "[claude-fable-5-1] RESPONDING ON BEHALF OF ALAN\n======\n\nFixed in 1a2b3c.\n")


if __name__ == "__main__":
    unittest.main()
