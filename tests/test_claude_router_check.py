import json
import re
import signal
import subprocess
import sys

from test_claude_router_activate import HELPER_WITH_MANAGED_SETTINGS, SDK, SLEEPY_CLAUDE, ActivationFixture, timed

GOLDEN = "components/claude-router/cmd/claude-router/testdata/check/"


class CheckFixture(ActivationFixture):
    def setUp(self):
        super().setUp()
        self.use_accounts("work", "alumni", "personal")
        self.fake_router.account = "alumni"
        self.accept(platform="darwin/arm64")
        (self.repo / "components/claude-router/accepted.json").write_text(self.acceptance.read_text())
        self.component()
        self.assertEqual(self.run_activate().returncode, 0)
        (self.root / "claude.log").write_text("")

    def use_accounts(self, *names, enabled=()):
        config = json.loads((self.state_dir / "config.json").read_text())
        config["accounts"] = [{"id": name, "capacity": 1} for name in names]
        (self.state_dir / "config.json").write_text(json.dumps(config))
        self.fake_router.status["accounts"] = [
            {"id": name, "registered": True, "needs_login": False, "overage": {"state": "enabled" if name in enabled else "disabled"}}
            for name in names]

    def check(self, *arguments, **env):
        return self.helper("check", *arguments, **env)

    def shown(self, result):
        text = timed(result.stdout).replace("127.0.0.1:{}".format(self.fake_router.port), "127.0.0.1:18787")
        return text.replace("({})".format(self.manager_name), "(launchd)")

    def golden(self, name):
        return (self.repo / GOLDEN / name).read_text()

    def claude_calls(self):
        return [json.loads(line) for line in (self.root / "claude.log").read_text().splitlines()]


class CheckTests(CheckFixture):
    def test_check_passes_every_line_and_finds_its_own_request_among_side_requests(self):
        result = self.check()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.shown(result), self.golden("passed.txt"))
        call, = self.claude_calls()
        self.assertEqual(call[:2], ["-p", "ping"])
        self.assertEqual(call[call.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", call)
        self.assertIn("--no-session-persistence", call)
        self.assertNotIn("ping", result.stdout + result.stderr)
        self.assertNotIn("a reply", result.stdout + result.stderr)

    def test_check_fails_and_sends_no_message_while_paid_overflow_is_on(self):
        self.use_accounts("work", "alumni", "personal", enabled=("alumni",))

        result = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(result), self.golden("failed.txt"))
        self.assertEqual(self.claude_calls(), [])

    def test_check_fails_when_only_another_session_is_answered(self):
        result = self.check(FAKE_CLAUDE_STATUS="529")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(result).splitlines()[-3:], [
            "fail      test message got no answer through the router (claude exited 0)",
            "check     failed",
            "next      claude-router status",
        ])
        self.assertIn('"status": 200', self.events.read_text())

    def test_check_fails_when_the_settings_do_not_point_at_the_router(self):
        self.assertEqual(self.direct().returncode, 0)

        result = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(result).splitlines()[3:], [
            "fail      Claude Code settings do not point at the router",
            "ok        code matches the accepted record (darwin/arm64)",
            "skip      test message, not sent while a check fails",
            "check     failed",
            "next      claude-router setup",
        ])

    def test_lines_fit_eighty_columns_with_three_eight_character_accounts(self):
        names = ("abcdefgh", "ijklmnop", "qrstuvwx")
        self.use_accounts(*names, enabled=names)
        failing = self.check()
        self.use_accounts(*names)
        passing = self.check()

        self.assertIn("fail      paid overflow on for abcdefgh, ijklmnop, and qrstuvwx\n", failing.stdout)
        for result in (failing, passing):
            for line in result.stdout.splitlines():
                self.assertLessEqual(len(re.sub(r":\d+ ", ":18787 ", line)), 80, line)


class SetupActivationTests(CheckFixture):
    def test_brief_activation_through_the_recorded_manifest_prints_the_check_and_the_result(self):
        self.assertEqual(self.direct().returncode, 0)
        manifest = self.home / ".local/state/astack/manifest.json"
        command = [sys.executable, "-c", HELPER_WITH_MANAGED_SETTINGS, "activate", "--brief", "--manifest", str(manifest)]

        result = subprocess.run(command, capture_output=True, text=True, env=self.activate_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.shown(result), self.golden("passed.txt").replace("check     passed\n",
                                                                               "activated Claude Code routes through the router\n"))
        self.assertEqual(self.base_url(), self.listen)

    def brief(self, *arguments, **env):
        self.assertEqual(self.direct().returncode, 0)
        command = [sys.executable, "-c", HELPER_WITH_MANAGED_SETTINGS, "activate", "--brief",
                   "--manifest", str(self.home / ".local/state/astack/manifest.json"), *arguments]
        return subprocess.run(command, capture_output=True, text=True, env=dict(self.activate_env(), **env))

    def test_brief_activation_pads_its_refusal_and_undo_labels_like_the_check_lines(self):
        self.accept(sdk="v0.0.1")
        refused = self.brief("--acceptance", str(self.acceptance))
        failed = self.brief(FAKE_CLAUDE_STATUS="529")

        self.assertEqual((refused.returncode, refused.stderr), (1, "\n".join([
            "claude-router: activate: the acceptance record does not match this installation",
            "          sdk: accepted v0.0.1, running {}".format(SDK),
            "next      run the acceptance matrix at this installation, or install the accepted head",
        ]) + "\n"))
        self.assertEqual((failed.returncode, failed.stderr), (1, "\n".join([
            "undo      ~/.claude/settings.json restored; the router keeps its logins and assignments",
            "next      claude-router status",
        ]) + "\n"))
        self.assertEqual(self.shown(failed).splitlines()[-1], "check     failed")



class CheckEdgeTests(CheckFixture):
    def test_with_the_router_stopped_check_prints_every_line_and_skips_what_it_cannot_read(self):
        self.fake_router.close()

        result = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.shown(result), "\n".join([
            "fail      router not answering on 127.0.0.1:18787",
            "skip      accounts, not read while the router is stopped",
            "skip      paid overflow, not read while the router is stopped",
            "ok        Claude Code settings point at the router",
            "skip      accepted record, not compared while the router is stopped",
            "skip      test message, not sent while a check fails",
            "check     failed",
            "next      claude-router start; claude-router check",
        ]) + "\n")

    def test_a_signal_ends_the_test_message_client_with_check(self):
        sleepy = self.root / "sleepy-claude"
        sleepy.write_text(SLEEPY_CLAUDE)
        sleepy.chmod(0o755)
        for sent in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sent):
                pidfile = self.root / "sleepy-{}.pid".format(int(sent))
                command = [sys.executable, "-c", HELPER_WITH_MANAGED_SETTINGS, "check", "--home", str(self.home), "--claude", str(sleepy)]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           env=dict(self.activate_env(), SLEEPY_PIDFILE=str(pidfile)))
                pid = self.wait_for_pid(pidfile)
                process.send_signal(sent)
                stdout, _ = process.communicate(timeout=30)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stdout.splitlines()[-1], "check     interrupted")
                self.assertTrue(self.gone(pid), "the test message client {} still runs".format(pid))

    def test_home_and_manifest_together_are_refused(self):
        result = self.check("--manifest", str(self.home / ".local/state/astack/manifest.json"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("argument --manifest: not allowed with argument --home", result.stderr)
