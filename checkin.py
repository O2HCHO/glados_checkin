"""GLaDOS check-in aligned with pyx13638516490/glados_checkin."""
import hashlib
import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse

import requests


DEFAULT_BASE_URL = "https://glados.cloud"
FALLBACK_BASE_URLS = ("https://glados.rocks", "https://glados.network")
SUPPORTED_BASE_URLS = (
    DEFAULT_BASE_URL, *FALLBACK_BASE_URLS, "https://glados.one",
    "https://glados.space", "https://glados.vip", "https://glados-facility.com",
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = (10, 30)
MAX_ATTEMPTS = 3
EXIT_OK, EXIT_FATAL, EXIT_RETRYABLE = 0, 1, 2
BEIJING_TZ = timezone(timedelta(hours=8))
SECRET_VALUES = set()
COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_\x60|~0-9A-Za-z:]+$")
RAW_VALUE = re.compile(r"^[A-Za-z0-9_+/-]+={0,2}$")
SUCCESS_MARKERS = ("checkin!", "checkin repeats", "observation logged")
AUTH_ERROR_MARKERS = (
    "not login", "not logged", "please login", "unauthorized",
    "invalid cookie", "cookie expired", "session expired",
    "没有权限", "未登录", "请先登录", "登录已过期",
)


class FatalError(RuntimeError):
    """Credentials/configuration/API reply needs user attention."""


class RetryableError(RuntimeError):
    """Temporary network/server failure."""


def get_env(name, default=""):
    return os.getenv(name, "").strip() or default


def redact(text):
    value = str(text)
    for secret in sorted(SECRET_VALUES, key=len, reverse=True):
        if len(secret) >= 4:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"koa:sess(?:\.sig)?=[^;\s\"']+", "koa:sess=[REDACTED]", value)
    value = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", value, flags=re.I)
    return re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", value
    )


def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S UTC+08:00")
    print(f"[{now}] {redact(message)}", flush=True)


def validate_cookie_header(cookie):
    # Validate without parsing/reserializing signed values or dropping fields.
    if any(ord(char) < 32 or ord(char) >= 127 for char in cookie):
        raise FatalError("Cookie 请求头包含换行或非 ASCII 字符，请复制 Network 中的 Cookie 值。")
    for part in cookie.split(";"):
        if not part.strip():
            continue
        name, separator, value = part.strip().partition("=")
        if not separator or not COOKIE_NAME.fullmatch(name) or re.search(r"\s", value):
            raise FatalError("Cookie 格式无效：需要 name=value; name=value，请勿粘贴 Set-Cookie 属性。")
    return cookie


def normalize_cookie(raw):
    """Preserve a complete header; convert only explicitly different formats."""
    cookie = raw.strip()
    if not cookie:
        raise FatalError("GLADOS_COOKIE 为空，请检查当前仓库的 Repository Secret。")
    if len(cookie) >= 2 and (cookie[0], cookie[-1]) in (
        ('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"),
    ):
        cookie = cookie[1:-1].strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()

    if cookie.startswith(("[", "{")):
        try:
            exported = json.loads(cookie)
        except ValueError as exc:
            raise FatalError("Cookie JSON 无效，请使用 Cookie-Editor 导出的 JSON。") from exc
        if isinstance(exported, dict) and isinstance(exported.get("cookie"), str):
            return normalize_cookie(exported["cookie"])
        if isinstance(exported, dict) and "name" in exported and "value" in exported:
            exported = [exported]
        if not isinstance(exported, list) or not exported:
            raise FatalError("需要单个账号的 Cookie-Editor JSON 数组或完整 Cookie 请求头。")
        pairs, names, domains = [], set(), set()
        for item in exported:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise FatalError("Cookie-Editor 每项必须包含 name 和 value。")
            if not isinstance(item.get("value"), str):
                raise FatalError("Cookie-Editor 的 value 必须是字符串。")
            name = item["name"]
            if name in names:
                raise FatalError("Cookie JSON 有重复名称，请只导出实际登录域名的 Cookie。")
            names.add(name)
            domain = item.get("domain")
            if isinstance(domain, str) and domain:
                domains.add(domain.lstrip(".").lower())
            pairs.append(f"{name}={item['value']}")
        if len(domains) > 1:
            raise FatalError("Cookie JSON 包含多个域名，请只导出一个登录域名。")
        return validate_cookie_header("; ".join(pairs))

    # Only decode HTML spaces in the user's original unnamed two-value format.
    parts = html.unescape(cookie).split()
    if len(parts) == 2 and all(RAW_VALUE.fullmatch(part) for part in parts):
        return validate_cookie_header(f"koa:sess={parts[0]}; koa:sess.sig={parts[1]}")

    # A complete header contains opaque signed data: never HTML/URL/base64
    # decode, re-encode, or reorder its values.
    if re.match(r"^[!#$%&'*+\-.^_\x60|~0-9A-Za-z:]+=", cookie):
        return validate_cookie_header(cookie)
    raise FatalError("Cookie 无法识别：使用完整 name=value 请求头、Cookie-Editor JSON 或两个原始会话值。")


def require_cookie():
    raw = os.getenv("GLADOS_COOKIE", "")
    SECRET_VALUES.add(raw.strip())
    cookie = normalize_cookie(raw)
    SECRET_VALUES.add(cookie)
    for part in cookie.split(";"):
        SECRET_VALUES.add(part.strip().partition("=")[2])
    raw_fingerprint = hashlib.sha256(raw.strip().encode()).hexdigest()[:12]
    fingerprint = hashlib.sha256(cookie.encode()).hexdigest()[:12]
    log(f"Cookie 输入长度={len(raw.strip())}，输入指纹={raw_fingerprint}；"
        f"请求头长度={len(cookie)}，请求头指纹={fingerprint}。")
    return cookie


def candidate_base_urls():
    primary = get_env("GLADOS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if primary not in SUPPORTED_BASE_URLS:
        raise FatalError("GLADOS_BASE_URL 必须是受支持的 GLaDOS 官方 HTTPS 域名。")
    urls = [primary]
    if get_env("GLADOS_DOMAIN_FALLBACK", "1") != "0":
        urls.extend(url for url in FALLBACK_BASE_URLS if url not in urls)
    return urls


def token_for_origin(origin):
    token = get_env("GLADOS_CHECKIN_TOKEN", urlparse(origin).hostname)
    if token == "glados.one" and origin == DEFAULT_BASE_URL:
        log("旧 token glados.one 已改用 glados.cloud。")
        return "glados.cloud"
    return token


def build_session(cookie):
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": get_env("GLADOS_USER_AGENT", DEFAULT_USER_AGENT),
        "Cookie": cookie,
    })
    return session


def classify_checkin(payload):
    code = payload.get("code")
    message = str(payload.get("message", "")).strip().lower()
    if code == -2 or any(marker in message for marker in AUTH_ERROR_MARKERS):
        return "auth_error"
    if code == 4 and payload.get("reason") == "device-mismatch":
        return "device_mismatch"
    if "please checkin via" in message or "token error" in message:
        return "token_error"
    if code == 0 or (code in (None, 1) and any(marker in message for marker in SUCCESS_MARKERS)):
        return "success"
    return "unknown"


def request_json(session, method, url, origin, payload=None):
    last_error = None
    retry_raw = get_env("GLADOS_RETRY_DELAY_SECONDS")
    try:
        fixed_delay = int(retry_raw) if retry_raw else None
        if fixed_delay is not None and not 0 <= fixed_delay <= 60:
            raise ValueError
    except ValueError as exc:
        raise FatalError("GLADOS_RETRY_DELAY_SECONDS 必须是 0 到 60 的整数。") from exc
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            log(f"{method.upper()} {url} (attempt {attempt}/{MAX_ATTEMPTS})")
            kwargs = {
                "timeout": TIMEOUT,
                "headers": {"Origin": origin, "Referer": f"{origin}/console/checkin"},
                "allow_redirects": False,
            }
            if payload is not None:
                # Same serialization and data= transport as the friend's script.
                kwargs["data"] = json.dumps(payload)
            response = session.request(method, url, **kwargs)
            log(f"HTTP {response.status_code}")
            if response.status_code in (401, 403):
                raise FatalError(f"认证失败 HTTP {response.status_code}；Cookie 或会话未被接受。")
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(f"临时服务错误 HTTP {response.status_code}")
            if 300 <= response.status_code < 400:
                raise FatalError("接口发生重定向，请核对 GLADOS_BASE_URL；不会向重定向地址转发 Cookie。")
            if response.status_code >= 400:
                raise FatalError(f"接口拒绝请求 HTTP {response.status_code}")
            try:
                result = response.json()
            except ValueError as exc:
                raise FatalError("接口返回非 JSON 内容，请检查站点是否出现验证页或接口变化。") from exc
            if not isinstance(result, dict):
                raise FatalError("接口响应必须是 JSON 对象。")
            log(f"API code={result.get('code')}，message={result.get('message', '<none>')}，"
                f"reason={result.get('reason', '<none>')}")
            return result
        except (requests.RequestException, RetryableError) as exc:
            last_error = exc
            log(f"临时请求失败：{exc}")
        if attempt < MAX_ATTEMPTS:
            delay = fixed_delay if fixed_delay is not None else min(60, 2 ** attempt * 5)
            log(f"{delay} 秒后重试。")
            time.sleep(delay)
    raise RetryableError(f"{MAX_ATTEMPTS} 次请求后仍失败：{last_error}")


def do_checkin(session, base_urls):
    last_error = None
    for origin in base_urls:
        token = token_for_origin(origin)
        try:
            payload = request_json(session, "post", f"{origin}/api/user/checkin",
                                   origin, {"token": token})
        except RetryableError as exc:
            last_error = exc
            log(f"{origin} 暂时不可用，尝试备用域名。")
            continue
        result = classify_checkin(payload)
        if result == "auth_error":
            raise FatalError(
                "签到认证失败：Cookie 或登录会话未被接受。请核对当前仓库 "
                "GLADOS_COOKIE 的指纹与浏览器请求头；不会重复请求失效凭据。"
            )
        if result == "device_mismatch":
            raise FatalError("GLaDOS 返回 device-mismatch：登录设备与签到设备不匹配，请在登录设备上签到。")
        if result == "token_error":
            raise FatalError(f"签到 token 不匹配：域名={origin}，token={token}。")
        if result != "success":
            raise FatalError(f"无法确认签到成功：code={payload.get('code')}，"
                             f"message={payload.get('message', '<none>')}。")
        log(f"签到成功或今日已签到：{payload.get('message', '<none>')}")
        return origin
    raise RetryableError(f"所有候选域名暂时不可用：{last_error}")


def report_account(session, origin):
    # Display failures must not invalidate an already successful check-in.
    for endpoint, field in (("status", "leftDays"), ("points", "points")):
        try:
            result = request_json(session, "get", f"{origin}/api/user/{endpoint}", origin)
            if result.get("code") != 0:
                log(f"WARNING: {endpoint} 查询失败；签到结果保持成功。")
                continue
            data = result.get("data") if endpoint == "status" else result
            value = data.get(field) if isinstance(data, dict) else None
            log(f"{field}={value if value is not None else '<unavailable>'}")
        except (FatalError, RetryableError) as exc:
            log(f"WARNING: {endpoint} 查询失败：{exc}；签到结果保持成功。")


def send_failure_email(summary):
    host, username = get_env("SMTP_HOST"), get_env("SMTP_USERNAME")
    password, recipient = os.getenv("SMTP_PASSWORD", ""), get_env("MAIL_TO")
    if not all((host, username, password, recipient)):
        log("未配置完整 SMTP，跳过失败邮件。")
        return
    try:
        port = int(get_env("SMTP_PORT", "587"))
        use_ssl = get_env("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes", "on")
        message = EmailMessage()
        message["Subject"] = "[GLaDOS] Automatic checkin failed"
        message["From"], message["To"] = get_env("MAIL_FROM", username), recipient
        body = f"GLaDOS checkin failed.\n\n{redact(summary)}\n"
        server_url = get_env("GITHUB_SERVER_URL", "https://github.com")
        repository, run_id = get_env("GITHUB_REPOSITORY"), get_env("GITHUB_RUN_ID")
        if repository and run_id:
            body += f"\nActions: {server_url}/{repository}/actions/runs/{run_id}\n"
        message.set_content(body)
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=30) as server:
            if not use_ssl:
                server.starttls()
            server.login(username, password)
            server.send_message(message)
        log("失败邮件已发送。")
    except (OSError, ValueError, smtplib.SMTPException):
        log("失败邮件发送失败，请检查 SMTP 配置。")


def main():
    try:
        cookie = require_cookie()
        origins = candidate_base_urls()
        log(f"签到域名候选：{', '.join(origins)}")
        with build_session(cookie) as session:
            origin = do_checkin(session, origins)
            report_account(session, origin)
        return EXIT_OK
    except (FatalError, RetryableError) as exc:
        log(f"ERROR: {exc}")
        send_failure_email(str(exc))
        return EXIT_RETRYABLE if isinstance(exc, RetryableError) else EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
