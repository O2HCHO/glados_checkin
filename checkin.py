import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import requests


BASE_URL = os.getenv("GLADOS_BASE_URL", "https://glados.cloud").rstrip("/")
CHECKIN_URL = f"{BASE_URL}/api/user/checkin"
STATUS_URL = f"{BASE_URL}/api/user/status"
CHECKIN_PAGE_URL = f"{BASE_URL}/console/checkin"
DEFAULT_TOKEN = os.getenv("GLADOS_CHECKIN_TOKEN", "glados.one")
TIMEOUT = 20
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


def parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        body = response.text.strip()
        raise RuntimeError(
            f"Response is not valid JSON. HTTP {response.status_code}. Body: {body or '<empty>'}"
        )


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
    exit_code = 0

    with requests.Session() as session:
        try:
            checkin_data, checkin_status = request_checkin(session, headers)
        except requests.RequestException as exc:
            log(f"Failed to request checkin endpoint: {exc}")
            return 1
        except RuntimeError as exc:
            log(str(exc))
            return 1

        message = checkin_data.get("message", "No message field returned")
        code = checkin_data.get("code")

        if checkin_status >= 400:
            log(f"Checkin failed. HTTP {checkin_status}. message: {message}")
            exit_code = 1
        elif code == 0:
            log(f"Checkin succeeded. message: {message}")
        elif code == 1:
            log(f"Already checked in today. message: {message}")
        else:
            log(f"Unexpected checkin result. code: {code}, message: {message}")
            exit_code = 1

        try:
            status_data, status_code = request_status(session, headers)
        except requests.RequestException as exc:
            log(f"Failed to request status endpoint: {exc}")
            return 1
        except RuntimeError as exc:
            log(str(exc))
            return 1

        left_days = extract_left_days(status_data)
        if status_code >= 400:
            log(f"Failed to fetch status. HTTP {status_code}")
            return 1

        if left_days is not None:
            log(f"Remaining days: {left_days}")
        else:
            log("Field data.leftDays was not found in the status response.")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
