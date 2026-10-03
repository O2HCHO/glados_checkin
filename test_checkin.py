import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import requests

import checkin


def response(data=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = data or {"code": 0, "message": "Checkin! Got 9 points"}
    return result


class CookieTests(unittest.TestCase):
    def test_signed_header_is_preserved_byte_for_byte(self):
        cookie = "extra=a&amp%2F; koa:sess=abc+/==; koa:sess.sig=sig-_; device=current"
        self.assertEqual(checkin.normalize_cookie(cookie), cookie)

    def test_raw_chat_values_with_html_space_and_padding(self):
        self.assertEqual(
            checkin.normalize_cookie("“abc+/== &#x20;\n sig-_  ”"),
            "koa:sess=abc+/==; koa:sess.sig=sig-_",
        )

    def test_prefix_and_quotes_are_removed_without_changing_values(self):
        self.assertEqual(
            checkin.normalize_cookie('"Cookie: koa:sess=value; koa:sess.sig=signature"'),
            "koa:sess=value; koa:sess.sig=signature",
        )

    def test_cookie_editor_export_retains_all_values(self):
        exported = [
            {"name": "koa:sess", "value": "abc+/==", "domain": ".glados.cloud"},
            {"name": "koa:sess.sig", "value": "signature", "domain": "glados.cloud"},
            {"name": "device", "value": "a&amp", "domain": "glados.cloud"},
        ]
        self.assertEqual(
            checkin.normalize_cookie(json.dumps(exported)),
            "koa:sess=abc+/==; koa:sess.sig=signature; device=a&amp",
        )

    def test_rejects_ambiguous_or_injected_input(self):
        for value in ("", "not a valid cookie", "koa:sess=value;\r\nX-Header=injected"):
            with self.subTest(value=value), self.assertRaises(checkin.FatalError):
                checkin.normalize_cookie(value)

    def test_logs_do_not_expose_session_values(self):
        secret = "koa:sess=secret-session; koa:sess.sig=secret-signature"
        output = io.StringIO()
        with patch.dict("os.environ", {"GLADOS_COOKIE": secret}), \
                patch.object(checkin, "SECRET_VALUES", set()), redirect_stdout(output):
            self.assertEqual(checkin.require_cookie(), secret)
            checkin.log("echo secret-session secret-signature")
        self.assertNotIn("secret-session", output.getvalue())
        self.assertNotIn("secret-signature", output.getvalue())
        self.assertIn("指纹", output.getvalue())


class CheckinTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.logging = patch.object(checkin, "log")
        self.logging.start()
        self.addCleanup(self.logging.stop)

    def test_direct_post_matches_friends_transport(self):
        cookie = "koa:sess=abc+/==; koa:sess.sig=signature; device=original"
        with checkin.build_session(cookie) as session:
            with patch.object(session, "request", return_value=response()) as request:
                self.assertEqual(
                    checkin.do_checkin(session, ["https://glados.cloud"]),
                    "https://glados.cloud",
                )
            self.assertEqual(session.headers["Cookie"], cookie)
            self.assertIn("Chrome/126.0", session.headers["User-Agent"])
        args, kwargs = request.call_args
        self.assertEqual(args, ("post", "https://glados.cloud/api/user/checkin"))
        self.assertEqual(json.loads(kwargs["data"]), {"token": "glados.cloud"})
        self.assertNotIn("json", kwargs)
        self.assertFalse(kwargs["allow_redirects"])

    def test_auth_failure_never_retries_or_switches_domains(self):
        session = Mock()
        session.request.return_value = response({"code": -2, "message": "没有权限"})
        with patch.object(checkin.time, "sleep") as sleep:
            with self.assertRaises(checkin.FatalError):
                checkin.do_checkin(
                    session, ["https://glados.cloud", "https://glados.network"]
                )
        self.assertEqual(session.request.call_count, 1)
        sleep.assert_not_called()

    def test_temporary_error_retries_and_then_succeeds(self):
        session = Mock()
        session.request.side_effect = [requests.Timeout("temporary"), response(status=503),
                                       response()]
        with patch.object(checkin.time, "sleep") as sleep:
            checkin.do_checkin(session, ["https://glados.cloud"])
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual([item.args[0] for item in sleep.call_args_list], [10, 20])

    def test_exhausted_network_requests_have_retryable_exit_reason(self):
        session = Mock()
        session.request.side_effect = requests.Timeout("temporary")
        with patch.object(checkin.time, "sleep"), self.assertRaises(checkin.RetryableError):
            checkin.request_json(session, "post", "https://glados.cloud/api/user/checkin",
                                 "https://glados.cloud", {"token": "glados.cloud"})
        self.assertEqual(session.request.call_count, 3)

    def test_unknown_token_and_device_responses_are_not_success(self):
        examples = [
            ({"code": 1, "message": "please checkin via https://glados.cloud"}, "token_error"),
            ({"code": 1, "message": "oops, token error"}, "token_error"),
            ({"code": 4, "reason": "device-mismatch"}, "device_mismatch"),
            ({"code": -2, "message": "Checkin!"}, "auth_error"),
            ({"code": 1, "message": "unknown"}, "unknown"),
        ]
        for payload, expected in examples:
            with self.subTest(payload=payload):
                self.assertEqual(checkin.classify_checkin(payload), expected)

    def test_friends_observation_response_and_repeat_are_success(self):
        for message in ("Today's observation logged. Return tomorrow for more points.",
                        "Checkin Repeats! Please Try Tomorrow"):
            self.assertEqual(
                checkin.classify_checkin({"code": 1, "message": message}), "success"
            )

    def test_status_failures_do_not_invalidate_checkin(self):
        session = Mock()
        session.request.return_value = response({"code": -2, "message": "没有权限"})
        checkin.report_account(session, "https://glados.cloud")
        self.assertEqual(session.request.call_count, 2)

    def test_main_auth_failure_returns_nonzero(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.request.return_value = response({"code": -2, "message": "没有权限"})
        with patch.dict("os.environ", {"GLADOS_COOKIE": "koa:sess=abc; koa:sess.sig=def"}), \
                patch.object(checkin, "build_session", return_value=session), \
                patch.object(checkin, "send_failure_email") as email:
            self.assertEqual(checkin.main(), checkin.EXIT_FATAL)
        self.assertEqual(session.request.call_count, 1)
        email.assert_called_once()

    def test_untrusted_origin_is_rejected_before_credentials_are_sent(self):
        with patch.dict("os.environ", {"GLADOS_BASE_URL": "https://example.com"}):
            with self.assertRaises(checkin.FatalError):
                checkin.candidate_base_urls()


if __name__ == "__main__":
    unittest.main()
