"""Normalize the read-only account data used in check-in reports."""
import re
from datetime import datetime
from decimal import Decimal

from glados_cookie import BEIJING_TZ, redact

ACCOUNT_ENDPOINTS = (
    ("/api/user/status", "leftDays"),
    ("/api/user/points", "points"),
)


def number(value):
    text = str(value)
    if re.fullmatch(r"-?[0-9]{1,16}(?:\.[0-9]{1,8})?", text):
        return Decimal(text)
    return None


def display(value):
    return format(value.normalize(), "f")


def record_account(account, field, payload):
    if payload.get("code") not in (0, None):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    value = number(data.get(field))
    if value is not None:
        account[field] = value
    if field == "leftDays":
        email = data.get("email")
        if isinstance(email, str) and re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", email):
            name, domain = email.split("@")
            account["account"] = f"{name[:2]}***@{domain}"
    else:
        plans = data.get("plans")
        if isinstance(plans, dict):
            account["plans"] = list(plans.values())
    return value


def report_lines(status, earned_points, account, message):
    ok = status in ("success", "already")
    result = {"success": "本次签到成功", "already": "今日已签到"}.get(status, "签到失败")
    lines = []
    balance = account.get("points")
    if ok:
        lines.append(f"👤 {account.get('account', 'GLaDOS 账号')}")
        points = display(balance) if balance is not None else "未获取"
        if earned_points is not None:
            points += f"（+{earned_points}）"
        lines.append(f"当前积分：{points}")
        days = account.get("leftDays")
        lines.append(f"剩余天数：{display(days)} 天" if days is not None else "剩余天数：未获取")
    if message.strip():
        result = f"{result} · {redact(message).splitlines()[0][:200]}"
    lines.append(f"签到结果：{result}")

    options = []
    for plan in account.get("plans", []):
        if not isinstance(plan, dict):
            continue
        cost, days = number(plan.get("points")), number(plan.get("days"))
        if cost is None or days is None or cost <= 0 or days <= 0:
            continue
        options.append((cost, days))
    if options:
        lines.extend(("", "🎁 兑换选项"))
        for cost, days in sorted(set(options)):
            if balance is None:
                note = "ℹ️", "余额未获取"
            elif balance >= cost:
                note = "✅", "可兑换"
            else:
                note = "❌", f"差 {display(cost - balance)} 积分"
            lines.append(f"{note[0]} {display(cost)} 分 → {display(days)} 天（{note[1]}）")
    lines.append(f"时间（北京时间 UTC+8）：{datetime.now(BEIJING_TZ):%Y-%m-%d %H:%M:%S}")
    return lines
