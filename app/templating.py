"""Jinja environment and the formatting filters the templates rely on."""
import json
import datetime as dt
from fastapi.templating import Jinja2Templates
from .config import BASE_DIR, ORG_NAME, PORTAL_NAME
from .auth import csrf_token

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def inr(v, dp=2):
    """Indian digit grouping: 1,23,45,678.90"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    neg, v = v < 0, abs(v)
    whole = int(v)
    frac = f"{v - whole:.{dp}f}"[2:] if dp else ""
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    out = ("-" if neg else "") + s + (("." + frac) if dp else "")
    return out


def lakh(v):
    """Compact Indian scale, for headline tiles."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    a = abs(v)
    if a >= 1e7:
        return f"{'-' if v < 0 else ''}{a / 1e7:,.2f} cr"
    if a >= 1e5:
        return f"{'-' if v < 0 else ''}{a / 1e5:,.2f} L"
    return inr(v, 0)


def pct(v, dp=2):
    try:
        return f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return "—"


def dmy(d):
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return f"{d.day:02d}-{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.month-1]}-{str(d.year)[2:]}"


def stamp(d):
    if not d:
        return ""
    return d.strftime("%d-%b-%Y %H:%M")


templates.env.filters.update(inr=inr, lakh=lakh, pct=pct, dmy=dmy, stamp=stamp,
                             fromjson=lambda s: json.loads(s or "{}"))
templates.env.globals.update(ORG_NAME=ORG_NAME, PORTAL_NAME=PORTAL_NAME,
                             now=dt.datetime.now, csrf_token=csrf_token)
