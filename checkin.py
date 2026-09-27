import html
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests


CONFIGURED_BASE_URL = os.getenv("GLADOS_BASE_URL", "").strip().rstrip("/")
BASE_URL = CONFIGURED_BASE_URL or "https://glados.cloud"
SUPPORTED_BASE_URLS = (
    "https://glados.cloud",
    "https://glados.network",
    "https://glados.rocks",
    "https://glados.one",
    "https://glados.space",
    "https://glados.vip",
    "https://glados-facility.com",
)
CANDIDATE_BASE_URLS = (
    (CONFIGURED_BASE_URL,) if CONFIGURED_BASE_URL else SUPPORTED_BASE_URLS
)
TOKEN_OVERRIDE = os.getenv("GLADOS_CHECKIN_TOKEN", "").strip()
TIMEOUT = 20
RETRY_DELAY_SECONDS = max(
    0, int(os.getenv("GLADOS_RETRY_DELAY_SECONDS", "60").strip() or "60")
)
BEIJING_TZ = timezone(timedelta(hours=8))
NORMAL_CHECKIN_MESSAGES = (
    "checkin! got",
    "checkin repeats! please try tomorrow",
    "today's observation logged",
)


def log(message: str) -> None:
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}] {message}", flush=True)


def normalize_cookie(raw_cookie: str) -> str:
    """Return a valid HTTP Cookie header while preserving extra cookie fields."""
    value = html.unescape(raw_cookie).strip().strip("\"'“”‘’").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()

    # Values copied from rendered HTML or chat may use whitespace instead of a
    # semicolon. Extracting by name avoids forwarding unrelated browser cookies.
    session_match = re.search(r"(?:^|[;\s])koa:sess=([^;\s]+)", value)
    signature_match = re.search(r"(?:^|[;\s])koa:sess\.sig=([^;\s]+)", value)
    if session_match or signature_match:
        if not (session_match and signature_match):
            raise ValueError("Cookie must contain both koa:sess and koa:sess.sig.")
        if ";" in value:
            # A value copied from Request Headers may contain additional
            # session/device cookies required by the current web client.
            return value
        return (
            f"koa:sess={session_match.group(1)}; "
            f"koa:sess.sig={signature_match.group(1)}"
        )

    # Also accept the two raw values copied separately, which is the format
    # some cookie editors and chat applications produce.
    parts = [part for part in re.split(r"[;\s]+", value) if part]
    if len(parts) == 2:
        return f"koa:sess={parts[0]}; koa:sess.sig={parts[1]}"

    raise ValueError(
        "Cookie format is invalid; expected "
        "'koa:sess=...; koa:sess.sig=...' or the two raw values."
    )


def build_headers(cookie: str, base_url: str = BASE_URL) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "Origin": base_url,
        "Referer": f"{base_url}/console/checkin",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
    }


@dataclass
class CheckinResult:
    success: bool
    summary: str
    data: Dict[str, Any]
    http_status: Optional[int]


def parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        body = response.text.strip()
        raise RuntimeError(
            f"Response is not valid JSON. HTTP {response.status_code}. Body: {body or '<empty>'}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Response JSON must be an object. HTTP {response.status_code}."
        )
    return data


def request_checkin(
    session: requests.Session,
    headers: Dict[str, str],
    base_url: str = BASE_URL,
) -> Tuple[Dict[str, Any], int]:
    token = TOKEN_OVERRIDE or (urlparse(base_url).hostname or "glados.cloud")
    checkin_url = f"{base_url}/api/user/checkin"
    payload = {"token": token}
    log(f"Requesting checkin endpoint: {checkin_url}")
    response = session.post(checkin_url, headers=headers, json=payload, timeout=TIMEOUT)
    data = parse_json_response(response)

    log(f"Checkin HTTP status: {response.status_code}")
    log(f"Checkin response: {data}")

    return data, response.status_code


def request_status(
    session: requests.Session,
    headers: Dict[str, str],
    base_url: str = BASE_URL,
) -> Tuple[Dict[str, Any], int]:
    status_url = f"{base_url}/api/user/status"
    log(f"Requesting status endpoint: {status_url}")
    response = session.get(status_url, headers=headers, timeout=TIMEOUT)
    data = parse_json_response(response)

    log(f"Status HTTP status: {response.status_code}")
    log(
        "Status response: "
        f"code={data.get('code')}, message={data.get('message', '<none>')}"
    )

    return data, response.status_code


def find_logged_in_session(
    session: requests.Session,
    cookie: str,
) -> Tuple[str, Dict[str, str]]:
    """Find the official origin on which this cookie is authenticated."""
    failures = []
    for base_url in CANDIDATE_BASE_URLS:
        headers = build_headers(cookie, base_url)
        try:
            data, http_status = request_status(session, headers, base_url)
        except (requests.RequestException, RuntimeError) as exc:
            failures.append(f"{base_url}: {exc}")
            continue

        if (
            http_status < 400
            and data.get("code") == 0
            and isinstance(data.get("data"), dict)
        ):
            log(f"Authenticated GLaDOS session found on {base_url}.")
            return base_url, headers

        failures.append(
            f"{base_url}: HTTP {http_status}, code={data.get('code')}, "
            f"message={data.get('message', '<none>')}"
        )

    if CONFIGURED_BASE_URL:
        hint = "Check that GLADOS_BASE_URL matches the domain used to obtain the Cookie."
    else:
        hint = "The Cookie was rejected by every supported GLaDOS domain."
    log("Session probe failures: " + " | ".join(failures))
    raise RuntimeError(hint + " Log in again and copy Cookie from /api/user/status.")


def is_normal_checkin_result(data: Dict[str, Any]) -> bool:
    """Recognize a real check-in or an explicit already-checked-in reply."""
    message = str(data.get("message", "")).strip().lower()
    if any(marker in message for marker in NORMAL_CHECKIN_MESSAGES):
        return True

    # code=0 has historically represented a successful check-in. code=1 is
    # deliberately not enough: GLaDOS also uses it for rejected old tokens.
    return data.get("code") == 0


def perform_checkin(
    session: requests.Session,
    headers: Dict[str, str],
    attempt_number: int,
    base_url: str = BASE_URL,
) -> CheckinResult:
    log(f"Starting checkin attempt {attempt_number}.")

    try:
        checkin_data, checkin_status = request_checkin(session, headers, base_url)
    except requests.RequestException as exc:
        summary = f"Request error: {exc}"
        log(f"Checkin failed: {summary}")
        return CheckinResult(False, summary, {}, None)
    except RuntimeError as exc:
        summary = str(exc)
        log(f"Checkin failed: {summary}")
        return CheckinResult(False, summary, {}, None)

    message = checkin_data.get("message", "No message field returned")
    code = checkin_data.get("code")

    if checkin_status >= 400:
        summary = f"HTTP {checkin_status}; message: {message}"
        success = False
    elif is_normal_checkin_result(checkin_data):
        if "repeats" in str(message).lower():
            summary = f"Already checked in today; message: {message}"
        else:
            summary = f"Succeeded; message: {message}"
        success = True
    else:
        summary = f"Unexpected result; code: {code}, message: {message}"
        success = False

    log(f"Checkin {'succeeded' if success else 'failed'}: {summary}")
    return CheckinResult(success, summary, checkin_data, checkin_status)


def send_failure_email(
    first_attempt: CheckinResult,
    second_attempt: CheckinResult,
    status_summary: str,
) -> None:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    mail_to = os.getenv("MAIL_TO", "").strip()
    mail_from = os.getenv("MAIL_FROM", "").strip() or smtp_username
    required = {
        "SMTP_HOST": smtp_host,
        "SMTP_USERNAME": smtp_username,
        "SMTP_PASSWORD": smtp_password,
        "MAIL_TO": mail_to,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        log(
            "Failure email skipped; missing SMTP configuration: "
            + ", ".join(missing)
        )
        return

    try:
        smtp_port = int(os.getenv("SMTP_PORT", "").strip() or "587")
    except ValueError:
        log("Failure email skipped; SMTP_PORT must be an integer.")
        return

    use_ssl = os.getenv("SMTP_USE_SSL", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    run_url = ""
    github_server = os.getenv("GITHUB_SERVER_URL", "").strip()
    github_repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    github_run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if github_server and github_repository and github_run_id:
        run_url = f"{github_server}/{github_repository}/actions/runs/{github_run_id}"

    message = EmailMessage()
    message["Subject"] = "[GLaDOS] Automatic checkin failed twice"
    message["From"] = mail_from
    message["To"] = mail_to
    message.set_content(
        "GLaDOS automatic checkin failed twice.\n\n"
        f"First attempt: {first_attempt.summary}\n"
        f"Second attempt: {second_attempt.summary}\n"
        f"Status request: {status_summary}\n"
        f"Time (Beijing): {datetime.now(BEIJING_TZ).isoformat()}\n"
        + (f"GitHub Actions run: {run_url}\n" if run_url else "")
    )

    try:
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(smtp_host, smtp_port, timeout=TIMEOUT) as server:
            if not use_ssl:
                server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        log(f"Failure email could not be sent: {exc}")
        return

    log(f"Failure email sent to {mail_to}.")


def extract_left_days(status_data: Dict[str, Any]) -> Optional[str]:
    data = status_data.get("data")
    if not isinstance(data, dict):
        return None

    left_days = data.get("leftDays")
    if left_days is None:
        return None

    if isinstance(left_days, (int, float)):
        return str(left_days)

    if isinstance(left_days, str):
        value = left_days.strip()
        if not value:
            return None
        try:
            decimal_value = Decimal(value)
            normalized = decimal_value.quantize(Decimal("0.01"))
            return format(normalized.normalize(), "f")
        except (InvalidOperation, ValueError):
            return value

    return str(left_days)


def main() -> int:
    raw_cookie = os.getenv("GLADOS_COOKIE", "").strip()
    if not raw_cookie:
        log("Missing environment variable GLADOS_COOKIE.")
        return 1

    try:
        cookie = normalize_cookie(raw_cookie)
    except ValueError as exc:
        log(str(exc))
        return 1

    with requests.Session() as session:
        try:
            active_base_url, headers = find_logged_in_session(session, cookie)
        except RuntimeError as exc:
            log(f"Authentication failed: {exc}")
            return 1

        first_attempt = perform_checkin(session, headers, 1, active_base_url)
        final_attempt = first_attempt

        if not first_attempt.success:
            log(
                "First checkin attempt failed. "
                f"Retrying in {RETRY_DELAY_SECONDS} seconds."
            )
            time.sleep(RETRY_DELAY_SECONDS)
            final_attempt = perform_checkin(session, headers, 2, active_base_url)

        status_summary = "Status endpoint was not requested."
        status_ok = True
        try:
            status_data, status_code = request_status(
                session, headers, active_base_url
            )
        except requests.RequestException as exc:
            status_summary = f"Request error: {exc}"
            log(f"Failed to request status endpoint: {exc}")
            status_ok = False
        except RuntimeError as exc:
            status_summary = str(exc)
            log(str(exc))
            status_ok = False
        else:
            left_days = extract_left_days(status_data)
            if status_code >= 400:
                status_summary = f"HTTP {status_code}"
                log(f"Failed to fetch status. HTTP {status_code}")
                status_ok = False
            elif left_days is not None:
                status_summary = f"Remaining days: {left_days}"
                log(f"Remaining days: {left_days}")
            else:
                status_summary = "Field data.leftDays was not found."
                log("Field data.leftDays was not found in the status response.")
                status_ok = False

        if not final_attempt.success:
            log("Checkin failed twice; sending failure email notification.")
            send_failure_email(first_attempt, final_attempt, status_summary)
            return 1

    return 0 if status_ok else 1


if __name__ == "__main__":
    sys.exit(main())
