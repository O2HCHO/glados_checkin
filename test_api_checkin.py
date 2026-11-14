import hashlib
import json
import unittest
from unittest.mock import MagicMock, Mock, call, patch

import api_checkin
import checkin
import diag_status
from glados_cookie import FatalError

COOKIE = "koa:sess=" + "A" * 60 + "; koa:sess.sig=" + "B" * 40


def response(payload=None, status=200):
    item = Mock(status_code=status)
    item.json.return_value = {"code": 0, "message": "Checkin! Got 9 Points"} if payload is None else payload
    return item


def challenge(nonce="test-challenge", ttl=300_000):
    return response({"code": 0, "nonce": nonce, "ttl": ttl})


class NetworkError(Exception):
    pass


class APITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.logs = patch.object(api_checkin, "log")
        self.log = self.logs.start()
        self.addCleanup(self.logs.stop)
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.session.headers = {}
        self.session.request.side_effect = [
            challenge(), response({"code": 0, "message": "Checkin! Got 1 Points"}),
            response({"code": 0, "data": {"leftDays": "100", "email": "tester@example.com"}}),
            response({"points": "124.0000", "plans": {"plan100": {"points": 100, "days": 10}}}),
        ]
        sleep = patch.object(api_checkin.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.requests = Mock(RequestException=NetworkError)
        self.requests.Session.return_value = self.session
        self.loader = patch.object(api_checkin, "_load_requests", return_value=self.requests)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def test_cookie_api_sends_exact_header_and_current_token_without_browser(self):
        with patch.object(checkin.browser_checkin, "run") as browser:
            code, result = api_checkin.run(COOKIE)
        self.assertEqual(code, 0)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.earned_points, "1")
        self.assertEqual(result.account["account"], "te***@example.com")
        self.assertEqual(result.account["plans"], [{"points": 100, "days": 10}])
        browser.assert_not_called()
        self.sleep.assert_called_once_with(2.7)
        self.assertEqual(self.session.headers["Cookie"], COOKIE)
        self.assertNotIn("Authorization", self.session.headers)
        first, second, status, balance = self.session.request.call_args_list
        self.assertEqual(first.args, ("get", "https://glados.cloud/api/user/checkin/challenge"))
        self.assertEqual(second.args, ("post", "https://glados.cloud/api/user/checkin"))
        proof = hashlib.sha256(b"test-challenge.glados-observe-v1").hexdigest()
        self.assertEqual(second.kwargs["json"], {"token": "glados.cloud", "nonce": "test-challenge", "proof": proof})
        self.assertEqual(status.args, ("get", "https://glados.cloud/api/user/status"))
        self.assertEqual(balance.args, ("get", "https://glados.cloud/api/user/points"))
        self.assertTrue(all(not request.kwargs["allow_redirects"] for request in self.session.request.call_args_list))
        output = str(self.log.call_args_list)
        self.assertNotIn("A" * 60, output)
        self.assertNotIn(proof, output)
        self.assertNotIn("test-challenge", output)
        self.assertIn("points=124.0000", output)

    def test_optional_original_device_values_are_used_only_when_configured(self):
        fp = "fp1." + "a" * 48
        with patch.dict("os.environ", {"GLADOS_FP": fp, "GLADOS_USER_AGENT": "original-browser"}):
            api_checkin.run(COOKIE)
        self.assertEqual(self.session.headers["Authorization"], fp)
        self.assertEqual(self.session.headers["User-Agent"], "original-browser")

    def test_dry_run_only_checks_status_with_cookie(self):
        self.session.request.side_effect = [response({"code": 0, "data": {"leftDays": "100"}})]
        code, _ = api_checkin.run(COOKIE, dry_run=True)
        self.assertEqual(code, 0)
        self.session.request.assert_called_once()
        self.assertEqual(self.session.request.call_args.args, ("get", "https://glados.cloud/api/user/status"))
        self.assertEqual(self.session.headers["Cookie"], COOKIE)
        self.sleep.assert_not_called()

    def test_auth_device_and_captcha_failures_are_not_retried(self):
        for data, marker in (({"code": -2, "message": "没有权限"}, "更新完整"),
                             ({"code": 4, "reason": "device-mismatch"}, "device-mismatch"),
                             ({"code": 0, "captcha_required": True}, "人机验证"),
                             ({"code": 0, "message": "Automated check-in detected"}, "拒绝自动签到")):
            for phase in ([], [challenge()]):
                self.session.request.reset_mock()
                self.sleep.reset_mock()
                self.session.request.side_effect = [*phase, response(data)]
                code, result = api_checkin.run(COOKIE)
                self.assertEqual(code, 1)
                self.assertIn(marker, result.message)
                self.assertEqual(self.session.request.call_count, 1 + len(phase))
                self.assertEqual(self.sleep.call_args_list, [call(2.7)] if phase else [])

    def test_temporary_http_and_network_errors_retry_once(self):
        for error in (response(status=503), NetworkError("private-cookie")):
            self.session.request.reset_mock()
            self.sleep.reset_mock()
            self.session.request.side_effect = [
                challenge("first-nonce"), error, challenge("second-nonce"),
                response(), response({"code": 0}), response({"points": 9}),
            ]
            code, _ = api_checkin.run(COOKIE)
            self.assertEqual(code, 0)
            first_get, first_post, second_get, second_post = self.session.request.call_args_list[:4]
            self.assertEqual(first_get.args, second_get.args)
            self.assertEqual(first_post.kwargs["json"]["nonce"], "first-nonce")
            self.assertEqual(second_post.kwargs["json"]["nonce"], "second-nonce")
            self.assertEqual(self.sleep.call_args_list, [call(2.7), call(5), call(2.7)])

    def test_invalid_challenge_never_posts(self):
        for reply in (challenge(nonce=""), challenge(ttl=100), challenge(ttl=float("nan")),
                      challenge(ttl=True), challenge(nonce="\ud800"), response({"code": 5})):
            self.session.request.reset_mock()
            self.session.request.side_effect = [reply]
            code, result = api_checkin.run(COOKIE)
            self.assertEqual((code, result.status), (1, "fatal"))
            self.session.request.assert_called_once()
            self.sleep.assert_not_called()

    def test_challenge_expires_during_wait(self):
        self.session.request.side_effect = [challenge(ttl=3000)]
        with patch.object(api_checkin.time, "monotonic", side_effect=[10, 13]):
            code, result = api_checkin.run(COOKIE)
        self.assertEqual(code, 1)
        self.assertIn("已过期", result.message)
        self.session.request.assert_called_once()

    def test_temporary_failure_exit_code_two(self):
        self.session.request.side_effect = [response(status=429), response(status=503)]
        with patch.object(api_checkin.time, "sleep"):
            code, result = api_checkin.run(COOKIE)
        self.assertEqual(code, 2)
        self.assertEqual(result.status, "retryable")

    def test_redirect_and_html_are_never_treated_as_success(self):
        for status in (302, 403):
            self.session.request.reset_mock()
            self.session.request.side_effect = [response(status=status)]
            with self.assertRaises(FatalError):
                api_checkin.run(COOKIE)
            self.session.request.assert_called_once()
        html = response()
        html.json.side_effect = ValueError("private response")
        self.session.request.side_effect = [html]
        with self.assertRaises(FatalError) as caught:
            api_checkin.run(COOKIE)
        self.assertNotIn("private response", str(caught.exception))

    def test_repeat_success_and_unknown_payload_are_distinguished(self):
        repeat = api_checkin.classify_checkin({"code": 1, "message": "Checkin Repeats! Please Try Tomorrow"})
        self.assertEqual(repeat.status, "already")
        self.assertIsNone(repeat.earned_points)
        self.assertEqual(api_checkin.classify_checkin({"code": 1, "message": "Unknown business error"}).status, "fatal")
        cloudflare = api_checkin.classify_checkin({
            "code": 5, "message": "Cloudflare verification failed. Nothing was recorded — please retry.",
        })
        self.assertEqual(cloudflare.status, "fatal")
        self.assertIn("Cloudflare 验证失败", cloudflare.message)
        self.assertIn("code=5", cloudflare.message)
        unknown = api_checkin.classify_checkin({"code": 5, "message": "private reply"})
        self.assertIn("code=5", unknown.message)
        self.assertNotIn("private reply", unknown.message)
        self.assertEqual(api_checkin.classify_checkin({"code": 1, "message": "please checkin via https://glados.cloud"}).status, "fatal")

    def test_account_display_failure_does_not_undo_confirmed_checkin(self):
        self.session.request.side_effect = [challenge(), response(), response(status=503), response(status=403)]
        code, _ = api_checkin.run(COOKIE)
        self.assertEqual(code, 0)

    def test_legacy_token_is_updated(self):
        with patch.dict("os.environ", {"GLADOS_CHECKIN_TOKEN": "glados.one"}):
            api_checkin.run(COOKIE)
        self.assertEqual(self.session.request.call_args_list[1].kwargs["json"]["token"], "glados.cloud")

    def test_backend_selection_and_main_honor_cookie_api(self):
        self.assertEqual(api_checkin.resolve_backend(), "api")
        with patch.dict("os.environ", {"GLADOS_AUTH_MODE": "email"}):
            self.assertEqual(api_checkin.resolve_backend(), "browser")
        with patch.dict("os.environ", {"GLADOS_CHECKIN_METHOD": "browser"}):
            self.assertEqual(api_checkin.resolve_backend(), "browser")
        with patch.dict("os.environ", {"GLADOS_CHECKIN_METHOD": "invalid"}), self.assertRaises(FatalError):
            api_checkin.resolve_backend()
        with patch.dict("os.environ", {"GLADOS_COOKIE": COOKIE}), patch.object(checkin.browser_checkin, "run") as browser:
            self.assertEqual(checkin.main([]), 0)
        browser.assert_not_called()

    def test_browser_mode_is_still_available_explicitly(self):
        with patch.dict("os.environ", {"GLADOS_COOKIE": COOKIE, "GLADOS_CHECKIN_METHOD": "browser"}), \
                patch.object(checkin.browser_checkin, "run", return_value=(0, api_checkin.CheckinResult("success"))) as browser:
            self.assertEqual(checkin.main([]), 0)
        browser.assert_called_once()
        self.session.request.assert_not_called()


class DiagnosticTests(unittest.TestCase):
    def test_status_probe_really_sends_cookie_regression(self):
        reply = MagicMock(status=200)
        reply.__enter__.return_value = reply
        reply.read.return_value = json.dumps({"code": 0, "data": {"leftDays": "100"}}).encode()
        with patch.object(diag_status, "require_cookie", return_value=COOKIE), \
                patch.object(diag_status.urllib.request, "urlopen", return_value=reply) as open_url, \
                patch.object(diag_status, "log"):
            self.assertEqual(diag_status.main(), 0)
        self.assertEqual(open_url.call_args.args[0].get_header("Cookie"), COOKIE)


if __name__ == "__main__":
    unittest.main()
