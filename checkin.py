import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple

import requests


BASE_URL = os.getenv("GLADOS_BASE_URL", "https://glados.cloud").rstrip("/")
CHECKIN_URL = f"{BASE_URL}/api/user/checkin"
STATUS_URL = f"{BASE_URL}/api/user/status"
CHECKIN_PAGE_URL = f"{BASE_URL}/console/checkin"
DEFAULT_TOKEN = os.getenv("GLADOS_CHECKIN_TOKEN", "glados.one")
TIMEOUT = 20
RETRY_DELAY_SECONDS = 10 * 60
BEIJING_TZ = timezone(timedelta(hours=8))


def log(message: str) -> None:
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}] {message}")


def build_headers(cookie: str) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "Origin": BASE_URL,
        "Referer": CHECKIN_PAGE_URL,
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


def request_checkin(session: requests.Session, headers: Dict[str, str]) -> Tuple[Dict[str, Any], int]:
    payload = {"token": DEFAULT_TOKEN}
    log(f"Requesting checkin endpoint: {CHECKIN_URL}")
    response = session.post(CHECKIN_URL, headers=headers, json=payload, timeout=TIMEOUT)
    data = parse_json_response(response)

    log(f"Checkin HTTP status: {response.status_code}")
    log(f"Checkin response: {data}")

    return data, response.status_code


def request_status(session: requests.Session, headers: Dict[str, str]) -> Tuple[Dict[str, Any], int]:
    log(f"Requesting status endpoint: {STATUS_URL}")
    response = session.get(STATUS_URL, headers=headers, timeout=TIMEOUT)
    data = parse_json_response(response)

    log(f"Status HTTP status: {response.status_code}")
    log(f"Status response: {data}")

    return data, response.status_code


def perform_checkin(
    session: requests.Session,
    headers: Dict[str, str],
    attempt_number: int,
) -> CheckinResult:
    log(f"Starting checkin attempt {attempt_number}.")

    try:
        checkin_data, checkin_status = request_checkin(session, headers)
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
    elif code == 0:
        summary = f"Succeeded; message: {message}"
        success = True
    elif code == 1:
        summary = f"Already checked in today; message: {message}"
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
    cookie = os.getenv("GLADOS_COOKIE", "").strip()
    if not cookie:
        log("Missing environment variable GLADOS_COOKIE.")
        return 1

    headers = build_headers(cookie)

    with requests.Session() as session:
        first_attempt = perform_checkin(session, headers, 1)
        final_attempt = first_attempt

        if not first_attempt.success:
            delay_minutes = RETRY_DELAY_SECONDS // 60
            log(
                f"First checkin attempt failed. Retrying in {delay_minutes} minutes."
            )
            time.sleep(RETRY_DELAY_SECONDS)
            final_attempt = perform_checkin(session, headers, 2)

        status_summary = "Status endpoint was not requested."
        status_ok = True
        try:
            status_data, status_code = request_status(session, headers)
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
