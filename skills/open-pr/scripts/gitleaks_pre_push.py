#!/usr/bin/env python3
"""Installed pre-push runner. No astack checkout or agent harness is needed."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


VERSION = "8.30.1"
PAYLOAD = "astack-gitleaks"
MARKER = "# astack gitleaks pre-push v1"


def git(*args):
    # Push transfers original objects, even when local replace refs hide them.
    result = subprocess.run(["git", "--no-replace-objects", *args], capture_output=True, text=True)
    if result.returncode:
        # Git errors can contain remote URLs or file content. Don't echo them.
        raise ValueError("Git could not determine the outgoing history; fetch the remote and retry.")
    return result.stdout.strip()


def revisions(data):
    """Use Git's authoritative old tip, never another remote's tracking refs."""
    ranges = []
    for line in data.decode("utf-8").splitlines():
        fields = line.split()
        if len(fields) != 4 or any(not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", x)
                                   for x in (fields[1], fields[3])):
            raise ValueError("Malformed pre-push input; no scan was performed.")
        local, remote = fields[1], fields[3]
        if set(local) == {"0"} or local == remote:
            continue
        if git("rev-parse", "--is-shallow-repository") == "true":
            raise ValueError("Shallow history cannot be fully scanned; fetch --unshallow and retry.")
        # Annotated tags pointing to commits work too. Blob/tree tags fail closed.
        head = git("rev-parse", "--verify", local + "^{commit}")
        args = [head]
        if set(remote) != {"0"}:
            args.append("^" + git("rev-parse", "--verify", remote + "^{commit}"))
        if git("rev-list", "--count", *args) != "0":
            ranges.append(args)
    return ranges


def scan(payload, revs):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"  # Also applies to Git invoked by Gitleaks.
    with tempfile.TemporaryDirectory(prefix="astack-gitleaks-") as tmp:
        report = Path(tmp) / "report.json"
        opts = ["--full-history", "--diff-merges=separate", "--root", "--format=medium",
                "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--text", *revs]
        result = subprocess.run([
            str(payload / "gitleaks"), "git", git("rev-parse", "--absolute-git-dir"), "--no-banner", "--redact=100",
            "--exit-code=10", "--log-level=error", "--timeout=120",
            "--config", str(payload / "rules.toml"),
            "--gitleaks-ignore-path", str(payload / "ignore"), "--ignore-gitleaks-allow",
            "--report-format=json", "--report-path", str(report),
            "--log-opts", " ".join(opts),
        ], capture_output=True, env=env, timeout=150)
        if result.returncode not in (0, 10):
            raise ValueError("Gitleaks failed (exit {}). Push blocked; no clean scan was established."
                             .format(result.returncode))
        findings = json.loads(report.read_text())
        if not isinstance(findings, list) or (result.returncode == 10 and not findings):
            raise ValueError("Gitleaks returned an invalid report; push blocked.")
        for finding in findings:
            # Never print Secret, Match, commit messages, or scanner stderr.
            print("astack: secret detected: {} in {}:{} (commit {})".format(
                json.dumps(finding["RuleID"]), json.dumps(finding["File"]),
                finding["StartLine"], finding["Commit"][:12]), file=sys.stderr)
        return not findings


def run(hooks, argv, data):
    payload = hooks / PAYLOAD
    state = json.loads((payload / "state.json").read_text())
    # Validate all ref lines before invoking either scanner or the chained hook.
    ranges = revisions(data)
    if state["chain"]:
        previous = hooks / state["chain"]
        if previous.exists() and os.access(previous, os.X_OK):
            result = subprocess.run([str(previous), *argv], input=data)
            if result.returncode:
                raise ValueError("The existing pre-push hook rejected this push.")
        elif state["chain"] == "pre-push.astack-original":
            raise ValueError("The saved pre-push hook is missing or not executable.")
    if not ranges:
        return 0
    binary = payload / "gitleaks"
    version = subprocess.run([str(binary), "version"], capture_output=True, text=True, timeout=10)
    if version.returncode or version.stdout.strip().removeprefix("v") != VERSION:
        raise ValueError("The pinned Gitleaks {} executable is unavailable or changed.".format(VERSION))
    clean = True
    for revs in ranges:
        clean = scan(payload, revs) and clean
    if not clean:
        raise ValueError("Push blocked. Remove secrets from the outgoing commits, including earlier commits.")
    return 0


def main():
    try:
        return run(Path(__file__).resolve().parent.parent, sys.argv[1:], sys.stdin.buffer.read())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # JSON/file/OS errors may include arbitrary content; only our own ValueErrors
        # have safe, useful messages. JSONDecodeError subclasses ValueError.
        message = str(error) if type(error) is ValueError else "Hook could not complete its scan."
        print("astack: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
