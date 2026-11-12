"""Send a short check-in summary using configured push channels."""
import re
from html import escape

from checkin_report import report_lines
from glados_cookie import get_env, log


def _send(channel, url, payload, accepted, *, form=False):
    try:
        import requests
    except ImportError:
        log(f"{channel} 推送失败，缺少 requests。")
        return

    try:
        body = {"data": payload} if form else {"json": payload}
        reply = requests.post(url, **body, timeout=(5, 10), allow_redirects=False)
        if 200 <= reply.status_code < 300:
            data = reply.json()
            if isinstance(data, dict) and accepted(data):
                log(f"{channel} 推送成功。")
                return
    except (requests.RequestException, ValueError):
        pass
    log(f"{channel} 推送失败，请检查渠道配置。")


def send_push(status, earned_points=None, *, account=None, message=""):
    """Return whether push is configured, independently of delivery or push level."""
    pushplus_token = get_env("PUSHPLUS_TOKEN")
    serverchan_key = get_env("SERVERCHAN_SENDKEY")
    telegram_token = get_env("TELEGRAM_BOT_TOKEN")
    telegram_chat = get_env("TELEGRAM_CHAT_ID")
    if not any((pushplus_token, serverchan_key, telegram_token and telegram_chat)):
        return False
    if status in ("success", "already") and get_env("PUSH_LEVEL", "fail_only").lower() != "all":
        return True

    title = "GLaDOS 签到：" + {
        "success": "本次运行成功 1/1", "already": "今日已签到 1/1",
    }.get(status, "本次运行失败 0/1")
    lines = report_lines(status, earned_points, account or {}, message)
    repository, run_id = get_env("GITHUB_REPOSITORY"), get_env("GITHUB_RUN_ID")
    if repository and run_id:
        server = get_env("GITHUB_SERVER_URL", "https://github.com")
        lines.extend(("", f"{server}/{repository}/actions/runs/{run_id}"))
    content = "\n".join(lines)

    if pushplus_token:
        _send("PushPlus", "https://www.pushplus.plus/send", {
            "token": pushplus_token, "title": title,
            "content": escape(content).replace("\n", "<br>"), "template": "html",
        }, lambda data: data.get("code") == 200)

    if serverchan_key:
        sc3 = re.fullmatch(r"sctp([0-9]+)t[A-Za-z0-9_-]+", serverchan_key)
        if sc3:
            host = f"{sc3.group(1)}.push.ft07.com"
        elif re.fullmatch(r"SCT[A-Za-z0-9_-]+", serverchan_key):
            host = "sctapi.ftqq.com"
        else:
            host = None
            log("Server 酱推送失败，SendKey 格式无效。")
        if host:
            _send("Server 酱", f"https://{host}/{serverchan_key}.send", {
                "title": title, "desp": "  \n".join(lines),
            }, lambda data: data.get("code") == 0, form=True)

    if telegram_token and telegram_chat:
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", telegram_token):
            _send("Telegram", f"https://api.telegram.org/bot{telegram_token}/sendMessage", {
                "chat_id": telegram_chat, "text": f"{title}\n\n{content}",
            }, lambda data: data.get("ok") is True)
        else:
            log("Telegram 推送失败，Bot Token 格式无效。")
    return True
