import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

import browser_checkin
import api_checkin
import checkin
import checkin_push

VALID_COOKIE = "gld:sess=" + "A" * 180 + "; gld:sess.sig=" + "B" * 120


def clean_environment(test_case):
    environment = patch.dict("os.environ", {}, clear=True)
    environment.start()
    test_case.addCleanup(environment.stop)
    logging = patch.object(checkin, "log")
    logging.start()
    test_case.addCleanup(logging.stop)


class MainTests(unittest.TestCase):
    def setUp(self):
        clean_environment(self)

    def test_success_returns_zero_without_email(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run",
                             return_value=(checkin.EXIT_OK,
                                           browser_checkin.CheckinResult("success", "Checkin! Got 12"))) as run, \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_OK)
        email.assert_not_called()
        run.assert_called_once()

    def test_fatal_result_returns_one_and_emails(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run",
                             return_value=(checkin.EXIT_FATAL,
                                           browser_checkin.CheckinResult("fatal", "没有权限"))), \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_FATAL)
        email.assert_called_once()

    def test_retryable_result_returns_two(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run",
                             return_value=(checkin.EXIT_RETRYABLE,
                                           browser_checkin.CheckinResult("retryable", "timeout"))), \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_RETRYABLE)
        email.assert_called_once()

    def test_bad_cookie_fails_before_starting_a_browser(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": "koa:sess=abc; koa:sess.sig=def"}), \
                patch.object(api_checkin, "run") as run, \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_FATAL)
        run.assert_not_called()
        email.assert_called_once()

    def test_unexpected_exception_is_reported_as_fatal(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run", side_effect=RuntimeError("boom")), \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_FATAL)
        email.assert_called_once()

    def test_dry_run_does_not_send_notifications(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run",
                             return_value=(1, browser_checkin.CheckinResult("fatal", "invalid"))), \
                patch.object(checkin, "send_push") as push, \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main(["--dry-run"]), checkin.EXIT_FATAL)
        push.assert_not_called()
        email.assert_not_called()

    def test_push_failure_preserves_successful_exit(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE}), \
                patch.object(api_checkin, "run",
                             return_value=(0, browser_checkin.CheckinResult("success", "Checkin! Got 1 Points"))), \
                patch.object(checkin, "send_push", side_effect=RuntimeError("private-token")) as push:
            self.assertEqual(checkin.main([]), checkin.EXIT_OK)
        push.assert_called_once_with("success", "1", account={}, message="Checkin! Got 1 Points")
        self.assertNotIn("private-token", str(checkin.log.call_args_list))

    def test_configured_push_replaces_failure_email(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": VALID_COOKIE, "PUSHPLUS_TOKEN": "test-token"}), \
                patch.object(api_checkin, "run",
                             return_value=(2, browser_checkin.CheckinResult("retryable", "timeout"))), \
                patch.object(checkin_push, "_send") as send, \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main([]), checkin.EXIT_RETRYABLE)
        send.assert_called_once()
        email.assert_not_called()


class PushTests(unittest.TestCase):
    def setUp(self):
        clean_environment(self)
        push_log = patch.object(checkin_push, "log")
        self.push_log = push_log.start()
        self.addCleanup(push_log.stop)
        post = patch.object(requests, "post")
        self.post = post.start()
        self.addCleanup(post.stop)

    def test_default_only_pushes_failures(self):
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {"code": 200}
        with patch.dict("os.environ", {"PUSHPLUS_TOKEN": "test-token"}):
            self.assertTrue(checkin_push.send_push("success", "1"))
            self.assertTrue(checkin_push.send_push("already"))
            self.post.assert_not_called()
            self.assertTrue(checkin_push.send_push("fatal"))
        self.assertIn("本次运行失败 0/1", self.post.call_args.kwargs["json"]["title"])

    def test_all_channels_send_reward_and_continue_after_provider_failure(self):
        server_reply = Mock(status_code=200)
        server_reply.json.return_value = {"code": 0}
        telegram_reply = Mock(status_code=200)
        telegram_reply.json.return_value = {"ok": True}
        self.post.side_effect = [requests.Timeout("private-token"), server_reply, telegram_reply]
        env = {
            "PUSHPLUS_TOKEN": "private-token", "SERVERCHAN_SENDKEY": "sctp42ttest-key",
            "TELEGRAM_BOT_TOKEN": "123:bot_key", "TELEGRAM_CHAT_ID": "-100",
            "PUSH_LEVEL": "all", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "123",
        }
        account = {
            "account": "te***@example.com", "points": Decimal("311.0000"), "leftDays": Decimal("479"),
            "plans": [{"points": 500, "days": 100}, {"points": 100, "days": 10}],
        }
        with patch.dict("os.environ", env):
            self.assertTrue(checkin_push.send_push("success", "1", account=account,
                                                  message="Checkin! Got 1 Points"))
        pushplus, serverchan, telegram = self.post.call_args_list
        self.assertIn("当前积分：311（+1）", pushplus.kwargs["json"]["content"])
        self.assertEqual(serverchan.args[0], "https://42.push.ft07.com/sctp42ttest-key.send")
        self.assertIn("actions/runs/123", serverchan.kwargs["data"]["desp"])
        self.assertIn("剩余天数：479 天", serverchan.kwargs["data"]["desp"])
        self.assertIn("100 分 → 10 天（可兑换）", serverchan.kwargs["data"]["desp"])
        self.assertIn("500 分 → 100 天（差 189 积分）", serverchan.kwargs["data"]["desp"])
        self.assertIn("北京时间 UTC+8", serverchan.kwargs["data"]["desp"])
        self.assertEqual(telegram.kwargs["json"]["chat_id"], "-100")
        self.assertIn("Checkin! Got 1 Points", telegram.kwargs["json"]["text"])
        self.assertTrue(all(not entry.kwargs["allow_redirects"] for entry in self.post.call_args_list))
        self.assertIn("PushPlus 推送失败", str(self.push_log.call_args_list))
        self.assertNotIn("private-token", str(self.push_log.call_args_list))

    def test_serverchan_turbo_checks_business_response(self):
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {"code": 1, "message": "private-key"}
        with patch.dict("os.environ", {"SERVERCHAN_SENDKEY": "SCTtest_key", "PUSH_LEVEL": "all"}):
            self.assertTrue(checkin_push.send_push("already"))
        self.assertEqual(self.post.call_args.args[0], "https://sctapi.ftqq.com/SCTtest_key.send")
        self.assertIn("今日已签到", self.post.call_args.kwargs["data"]["title"])
        self.assertIn("Server 酱 推送失败", str(self.push_log.call_args_list))
        self.assertNotIn("private-key", str(self.push_log.call_args_list))


class FailureEmailTests(unittest.TestCase):
    def setUp(self):
        clean_environment(self)

    def test_missing_smtp_configuration_skips_email(self):
        with patch.object(checkin.smtplib, "SMTP") as smtp:
            checkin.send_failure_email("failed")
        smtp.assert_not_called()

    def test_configured_smtp_sends_redacted_body(self):
        environment = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "password",
            "MAIL_TO": "someone@example.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "123",
        }
        server = Mock()
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        with patch.dict("os.environ", environment), \
                patch.object(checkin.smtplib, "SMTP", return_value=server) as smtp:
            checkin.send_failure_email("session " + VALID_COOKIE)
        smtp.assert_called_once()
        server.login.assert_called_once()
        body = server.send_message.call_args.args[0].get_content()
        self.assertNotIn("A" * 180, body)
        self.assertIn("actions/runs/123", body)


if __name__ == "__main__":
    unittest.main()
