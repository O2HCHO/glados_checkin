import unittest
from unittest.mock import Mock

import checkin


class CookieNormalizationTests(unittest.TestCase):
    def test_keeps_named_cookie_values(self):
        self.assertEqual(
            checkin.normalize_cookie("koa:sess=session; koa:sess.sig=signature"),
            "koa:sess=session; koa:sess.sig=signature",
        )

    def test_accepts_html_space_between_raw_values(self):
        self.assertEqual(
            checkin.normalize_cookie(
                "“session-value &#x20;\n signature-value  ”"
            ),
            "koa:sess=session-value; koa:sess.sig=signature-value",
        )

    def test_rejects_incomplete_cookie(self):
        with self.assertRaisesRegex(ValueError, "both koa:sess and koa:sess.sig"):
            checkin.normalize_cookie("koa:sess=session-only")


class CheckinResponseTests(unittest.TestCase):
    def test_code_one_old_token_rejection_is_not_success(self):
        self.assertFalse(
            checkin.is_normal_checkin_result(
                {"code": 1, "message": "please checkin via https://glados.cloud"}
            )
        )

    def test_explicit_repeat_is_success(self):
        self.assertTrue(
            checkin.is_normal_checkin_result(
                {"code": 1, "message": "Checkin Repeats! Please Try Tomorrow"}
            )
        )

    def test_checkin_payload_uses_current_domain(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"code": 0, "message": "Checkin! Got 10 Points"}
        session = Mock()
        session.post.return_value = response

        checkin.request_checkin(session, {"Cookie": "redacted"})

        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {"token": "glados.cloud"},
        )


if __name__ == "__main__":
    unittest.main()
