"""图形验证码：纯 Python 生成 SVG，无外部依赖，离线可用。答案存 session，一次有效。"""

import random
import secrets
import time

SESSION_KEY = "register_captcha"
CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 0/O、1/I
LENGTH = 4
TTL_SECONDS = 300
COLORS = ["#044470", "#0a5a8f", "#1d6fa5", "#33415c", "#5b2a86"]


def new_challenge(request):
    answer = "".join(secrets.choice(CHARSET) for _ in range(LENGTH))
    request.session[SESSION_KEY] = {"answer": answer, "expires": time.time() + TTL_SECONDS}
    return answer


def render_svg(answer, width=120, height=40):
    rng = random.Random(secrets.randbits(32))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#eef4f9"/>',
    ]
    for _ in range(5):
        x1, y1, x2, y2 = rng.randint(0, width), rng.randint(0, height), rng.randint(0, width), rng.randint(0, height)
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{rng.choice(COLORS)}" stroke-opacity="0.35" stroke-width="1"/>'
        )
    for _ in range(24):
        parts.append(
            f'<circle cx="{rng.randint(0, width)}" cy="{rng.randint(0, height)}" r="1" fill="{rng.choice(COLORS)}" fill-opacity="0.5"/>'
        )
    step = width / (LENGTH + 1)
    for index, char in enumerate(answer):
        x = step * (index + 1) + rng.uniform(-4, 4)
        y = height / 2 + 8 + rng.uniform(-3, 3)
        angle = rng.uniform(-22, 22)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" fill="{rng.choice(COLORS)}" font-size="24" font-weight="700" '
            f'font-family="Consolas, monospace" text-anchor="middle" transform="rotate({angle:.1f} {x:.1f} {y:.1f})">{char}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def verify(request, value):
    """校验后立即作废，防止重复使用。"""
    challenge = request.session.pop(SESSION_KEY, None)
    if not challenge or time.time() > challenge.get("expires", 0):
        return False
    return (value or "").strip().upper() == challenge.get("answer")
