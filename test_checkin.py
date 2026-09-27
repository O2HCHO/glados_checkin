import unittest
from unittest.mock import Mock, patch

import checkin


class CookieNormalizationTests(unittest.TestCase):
    def test_keeps_named_cookie_values(self):
        self.assertEqual(
            checkin.normalize_cookie("koa:sess=session; koa:sess.sig=signature"),
            "koa:sess=session; koa:sess.sig=signature",
        )

    def test_preserves_extra_fields_from_full_cookie_header(self):
        cookie = (
            "device=browser-id; koa:sess=session; "
            "koa:sess.sig=signature; clearance=current"
        )
        self.assertEqual(checkin.normalize_cookie(cookie), cookie)

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

    def test_checkin_token_matches_selected_domain(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"code": 0, "message": "Checkin!"}
        session = Mock()
        session.post.return_value = response

        checkin.request_checkin(
            session,
            {"Cookie": "redacted"},
            "https://glados.network",
        )

        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {"token": "glados.network"},
        )

    def test_session_probe_selects_authenticated_domain(self):
        denied = Mock()
        denied.status_code = 200
        denied.json.return_value = {"code": -2, "message": "没有权限"}
        authenticated = Mock()
        authenticated.status_code = 200
        authenticated.json.return_value = {"code": 0, "data": {"leftDays": "10"}}
        session = Mock()
        session.get.side_effect = [denied, authenticated]

        with patch.object(
            checkin,
            "CANDIDATE_BASE_URLS",
            ("https://glados.cloud", "https://glados.network"),
        ):
            base_url, headers = checkin.find_logged_in_session(
                session,
                "koa:sess=session; koa:sess.sig=signature",
            )

        self.assertEqual(base_url, "https://glados.network")
        self.assertEqual(headers["Origin"], "https://glados.network")


if __name__ == "__main__":
    unittest.main()
