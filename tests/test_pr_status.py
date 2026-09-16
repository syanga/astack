"""Shape the babysit-pr snapshot from recorded gh payloads, without touching the network."""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "skills/babysit-pr/scripts/pr_status.py"
spec = importlib.util.spec_from_file_location("pr_status", SCRIPT)
pr_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_status)

SINCE = "2026-09-15T10:00:00Z"

PR = {
    "number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN", "isDraft": False,
    "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED", "reviewDecision": "",
    "headRefOid": "abc123", "headRefName": "feature", "baseRefName": "main", "autoMergeRequest": None,
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
    "reviews": [
        {"author": {"login": "alan"}, "submittedAt": "2026-09-15T12:00:00Z", "state": "COMMENTED", "body": "looks off"},
    ],
}

THREADS = [
    {"id": "T1", "isResolved": True, "isOutdated": False, "path": "a.py", "line": 1,
     "comments": {"nodes": [{"author": {"login": "bot"}, "body": "done", "createdAt": SINCE, "url": "t1"}]}},
    {"id": "T2", "isResolved": False, "isOutdated": False, "path": "b.py", "line": 7,
     "comments": {"nodes": [
         {"author": {"login": "bot"}, "body": "possible race", "createdAt": SINCE, "url": "t2"},
         {"author": {"login": "alan"}, "body": "looking", "createdAt": SINCE, "url": "t2b"},
     ]}},
]


class SummarizeTests(unittest.TestCase):
    def test_snapshot_shape(self):
        snapshot = pr_status.summarize(PR, THREADS, SINCE)
        self.assertEqual(snapshot["checks"]["counts"], {"passed": 1, "failed": 1, "pending": 1, "skipped": 1})
        self.assertEqual(snapshot["checks"]["failed"], [{"name": "deploy", "kind": "failed", "state": "FAILURE", "link": "u4"}])
        self.assertEqual([check["name"] for check in snapshot["checks"]["pending"]], ["lint"])
        self.assertEqual(snapshot["threads"]["resolved"], 1)
        self.assertEqual(snapshot["threads"]["unresolved"], [{
            "id": "T2", "path": "b.py", "line": 7, "outdated": False, "author": "bot",
            "body": "possible race", "replies": 1, "url": "t2",
        }])
        self.assertEqual([comment["body"] for comment in snapshot["new_comments"]], ["after push"])
        self.assertEqual(snapshot["new_reviews"][0]["author"], "alan")
        self.assertIsNone(snapshot["review_decision"])
        self.assertEqual(snapshot["head"], {"sha": "abc123", "ref": "feature", "committed_at": SINCE})


if __name__ == "__main__":
    unittest.main()
