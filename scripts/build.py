#!/usr/bin/env python3
"""Build README.md and the CRT slices under assets/ from profile.toml and data/*.json.

Usage: scripts/build.py            (run from anywhere; paths are relative to the repository)

The build is deterministic: the same inputs give the same bytes, and the asset directory is named
after the content so GitHub's image cache fetches a changed profile instead of serving the old one.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

CHAR_W = 9.6
LINE_H = 22
LEFT = 52
FIRST_BASELINE = 64
COLS = 80
WIDTH = 880
BOTTOM_PAD = 45
INDENT = 7
PATCH_INDENT = 14
OPTION_COL = 25
LINK_SLICE_ABOVE = 16
LINK_SLICE_BELOW = 6

TYPE_STEP = 0.177
TYPE_DURATION = 0.14
MAX_STEPS = 41
SEED = 11

VIEWBOX = re.compile(r'viewBox="0 (\d+) 880 (\d+)"')
PAGE_RECT = re.compile(r'<rect width="880" height="(\d+)" rx="22"')
MARKUP = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|\[(.+?)\]\((.+?)\)")

WEATHER_WORDS: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


@dataclass(frozen=True)
class Char:
    ch: str
    cls: str = ""
    url: str | None = None


@dataclass(frozen=True)
class Span:
    text: str
    cls: str = ""
    url: str | None = None


@dataclass(frozen=True)
class Row:
    """One typed line of the man page: its row on the grid, its spans, and the link it belongs to."""
    row: int
    spans: list[Span]
    url: str | None = None
    col: int = 0
    cls: str = ""


@dataclass(frozen=True)
class Patch:
    ref: str
    url: str
    text: str


@dataclass(frozen=True)
class Slice:
    top: int
    bottom: int
    url: str | None

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class Keystroke:
    """A session element that appears at a time: one typed character or one printed line."""
    at: float
    row: int
    col: int
    text: str
    cls: str = ""
    spans: list[Span] = field(default_factory=list)


@dataclass(frozen=True)
class Output:
    readme: str
    svgs: list[tuple[str, str]]
    asset_dir: str


def baseline(row: int) -> int:
    return FIRST_BASELINE + row * LINE_H


def x_of(col: float) -> str:
    return f"{LEFT + col * CHAR_W:.1f}".removesuffix(".0")


def parse_markup(text: str) -> list[Char]:
    chars: list[Char] = []
    pos = 0
    for m in MARKUP.finditer(text):
        chars += [Char(c) for c in text[pos:m.start()]]
        if m.group(1):
            chars += [Char(c, "h") for c in m.group(1)]
        elif m.group(2):
            chars += [Char(c, "u") for c in m.group(2)]
        else:
            chars += [Char(c, "u", m.group(4)) for c in m.group(3)]
        pos = m.end()
    chars += [Char(c) for c in text[pos:]]
    return chars


def to_spans(chars: list[Char]) -> list[Span]:
    spans: list[Span] = []
    for c in chars:
        if spans and (spans[-1].cls, spans[-1].url) == (c.cls, c.url):
            spans[-1] = Span(spans[-1].text + c.ch, c.cls, c.url)
        else:
            spans.append(Span(c.ch, c.cls, c.url))
    return spans


def wrap(text: str, width: int) -> list[list[Span]]:
    """Greedy word wrap of marked-up text. A word wider than the width is an error, not an overflow."""
    chars = parse_markup(text)
    words: list[list[Char]] = [[]]
    for c in chars:
        if c.ch == " ":
            words.append([])
        else:
            words[-1].append(c)
    lines: list[list[Char]] = []
    line: list[Char] = []
    for word in words:
        if len(word) > width:
            raise ValueError(f"word wider than {width} columns: {''.join(c.ch for c in word)}")
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = list(word)
        elif line:
            line = line + [Char(" ")] + word
        else:
            line = list(word)
    lines.append(line)
    return [to_spans(line) for line in lines]


def plain(spans: list[Span]) -> str:
    return "".join(s.text for s in spans)


def url_of(spans: list[Span]) -> str | None:
    urls = {s.url for s in spans if s.url}
    if len(urls) > 1:
        raise ValueError(f"one link per line: {plain(spans)}")
    return urls.pop() if urls else None


def describe_weather(code: int) -> str:
    if code not in WEATHER_WORDS:
        raise ValueError(f"unknown WMO weather code {code}")
    return WEATHER_WORDS[code]


def weather_line(weather: dict[str, Any], place: str) -> str:
    current = weather["current"]
    when = datetime.fromisoformat(current["time"]).strftime("%Y-%m-%d %H:%M")
    return (
        f"{describe_weather(int(current['weather_code']))}, {round(current['temperature_2m'])} °C, "
        f"wind {round(current['wind_speed_10m'])} km/h — {place}, {when}"
    )


def format_uptime(booted_at: datetime, now: datetime) -> str:
    delta = now - booted_at
    minutes = delta.seconds // 60
    days = "1 day" if delta.days == 1 else f"{delta.days} days"
    return f"{days}, {minutes // 60}:{minutes % 60:02d}"


def select_patches(pull_requests: list[dict[str, Any]], max_entries: int, overrides: dict[str, str]) -> list[Patch]:
    shown = sorted(
        (p for p in pull_requests if p["state"] in ("open", "merged")),
        key=lambda p: str(p["created_at"]),
        reverse=True,
    )[:max_entries]
    refs = {f"{p['repository']}#{p['number']}" for p in shown}
    stale = sorted(set(overrides) - refs)
    if stale:
        raise ValueError(f"curated text for pull requests that are not shown: {', '.join(stale)}")
    patches = []
    for p in shown:
        ref = f"{p['repository']}#{p['number']}"
        text = overrides.get(ref) or str(p["title"])
        if p["state"] == "merged":
            text += " (merged)"
        patches.append(Patch(ref, str(p["url"]), text))
    return patches


class Layout:
    """Places man page rows on the grid, top to bottom."""

    def __init__(self) -> None:
        self.rows: list[Row] = []
        self.next = 0

    def line(self, spans: list[Span], col: int = 0, cls: str = "", url: str | None = None) -> None:
        self.rows.append(Row(self.next, spans, url or url_of(spans), col, cls))
        self.next += 1

    def blank(self) -> None:
        self.next += 1

    def paragraph(self, text: str, col: int, url: str | None = None) -> None:
        for piece in text.split("\n"):
            lines = wrap(piece, COLS - col)
            link = url or next((u for line in lines if (u := url_of(line))), None)
            for line in lines:
                self.line(line, col, url=link)


def layout_man_page(profile: dict[str, Any], patches: list[Patch]) -> list[Row]:
    man = profile["man"]
    lay = Layout()
    gap = COLS - 2 * len(man["title"]) - len(man["center"])
    lay.line([Span(f"{man['title']}{' ' * -(-gap // 2)}{man['center']}{' ' * (gap // 2)}{man['title']}")], cls="m")
    lay.blank()
    lay.blank()
    lay.line([Span(man["name"])], cls="h big")
    lay.line([Span(man["tagline"])], cls="m")
    for section in profile["sections"]:
        lay.blank()
        if section.get("patches"):
            lay.line([Span(profile["patches"]["title"])], cls="h")
            for patch in patches:
                lay.line([Span(patch.ref, "u", patch.url)], INDENT)
                lay.paragraph(patch.text, PATCH_INDENT, url=patch.url)
            continue
        lay.line([Span(section["title"])], cls="h")
        for text in section.get("portrait", []):
            if len(text) > COLS - INDENT:
                raise ValueError(f"portrait line wider than {COLS - INDENT} columns: {text!r}")
            lay.line([Span(text.rstrip())], INDENT)
        for i, option in enumerate(section.get("options", [])):
            if i:
                lay.blank()
            flag = option["flag"]
            if len(flag) < OPTION_COL - INDENT:
                lines = wrap(option["text"], COLS - OPTION_COL)
                lay.line([Span(flag, "h"), Span(" " * (OPTION_COL - INDENT - len(flag))), *lines[0]], INDENT)
                for line in lines[1:]:
                    lay.line(line, OPTION_COL)
            else:
                lay.line([Span(flag, "h")], INDENT)
                lay.paragraph(option["text"], OPTION_COL)
        for i, paragraph in enumerate(section.get("paragraphs", [])):
            if i:
                lay.blank()
            lay.paragraph(paragraph, INDENT)
    return lay.rows


def slice_rows(rows: list[Row], page_height: int) -> list[Slice]:
    """Cut the page so every run of rows sharing a link is one image, and the rest fill the gaps."""
    slices: list[Slice] = []
    cursor = 0
    i = 0
    linked = [r for r in rows if r.url]
    while i < len(linked):
        j = i
        while j + 1 < len(linked) and linked[j + 1].url == linked[i].url and linked[j + 1].row == linked[j].row + 1:
            j += 1
        top = baseline(linked[i].row) - LINK_SLICE_ABOVE
        bottom = baseline(linked[j].row) + LINK_SLICE_BELOW
        if top > cursor:
            slices.append(Slice(cursor, top, None))
        slices.append(Slice(top, bottom, linked[i].url))
        cursor = bottom
        i = j + 1
    if cursor < page_height:
        slices.append(Slice(cursor, page_height, None))
    return slices


class Session:
    """The login session typed on the screen before the man page clears it."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.keys: list[Keystroke] = []
        self.cursor: list[tuple[float, int, int]] = []
        self.t = 0.0
        self.row = 0
        self.bell_at = 0.0

    def prompt(self, spans: list[Span], at: float) -> None:
        self.t = at
        text = plain(spans)
        self.keys.append(Keystroke(at, self.row, 0, text, "", spans))
        self.cursor.append((at, self.row, len(text)))
        self.col = len(text)

    def type(self, text: str, pause_before: float) -> None:
        self.t += pause_before
        for ch in text:
            self.t += self.rng.uniform(0.045, 0.16)
            self.keys.append(Keystroke(self.t, self.row, self.col, ch))
            self.col += 1
            self.cursor.append((self.t, self.row, self.col))

    def enter(self) -> None:
        self.t += 0.35
        self.row += 1
        self.col = 0
        self.cursor.append((self.t, self.row, 0))

    def blank(self) -> None:
        self.row += 1

    def print(self, lines: list[tuple[int, str, str]], first_after: float, gap: float) -> None:
        """Print lines; the cursor ends at the start of the row under the last one."""
        self.t += first_after
        for i, (col, text, cls) in enumerate(lines):
            self.t += gap if i else 0
            self.keys.append(Keystroke(self.t, self.row, col, text, cls))
            self.cursor.append((self.t, self.row, col + len(text)))
            self.row += 1
        self.cursor.append((self.t, self.row, 0))

    def print_inline(self, text: str, first_after: float) -> None:
        """Print a line that waits for input: the cursor stays at its end."""
        self.t += first_after
        self.keys.append(Keystroke(self.t, self.row, 0, text))
        self.col = len(text)
        self.cursor.append((self.t, self.row, self.col))


def build_session(profile: dict[str, Any], weather: dict[str, Any], now: datetime) -> Session:
    s = profile["session"]
    user, host = s["user"], s["host"]
    greeting = [((COLS - len(g)) // 2, g, "b") for g in s["greeting"]]
    status = [
        (2, f"Uptime:      {format_uptime(s['booted_at'], now)}", ""),
        (2, f"Weather:     {weather_line(weather, profile['weather']['place'])}", ""),
        (2, f"Last login:  {s['last_login']}", ""),
    ]
    shell = [Span(f"{user}@{host.split('.')[0]}", "b"), Span(":~$ ", "m")]
    session = Session(SEED)
    session.prompt([Span("$ ", "m")], 0.8)
    session.type(f"ssh {user}@{host}", 0.0)
    session.enter()
    session.print([
        (0, f"The authenticity of host '{host} ({s['ip']})' can't be established.", ""),
        (0, f"ED25519 key fingerprint is {s['fingerprint']}.", ""),
        (0, "This key is not known by any other names.", ""),
    ], 0.45, 0.09)
    session.print_inline("Are you sure you want to continue connecting (yes/no/[fingerprint])? ", 0.09)
    session.type("yes", 1.0)
    session.enter()
    session.print([(0, f"Warning: Permanently added '{host}' (ED25519) to the list of known hosts.", "")], 0.0, 0.09)
    session.blank()
    session.t += 1.52
    session.bell_at = session.t
    session.print(greeting, 0.0, 0.06)
    session.blank()
    session.print(status, 0.06, 0.06)
    session.blank()
    session.prompt(shell, session.t + 0.16)
    session.type("whoami", 3.59)
    session.enter()
    session.print([(0, user, "")], 0.0, 0.0)
    session.prompt(shell, session.t + 0.15)
    session.type("man kris", 0.36)
    session.enter()
    return session


def fmt(t: float) -> str:
    return f"{t:.3f}"


def span_markup(spans: list[Span]) -> str:
    return "".join(escape(s.text) if not s.cls else f'<tspan class="{s.cls}">{escape(s.text)}</tspan>' for s in spans)


def render_session(session: Session) -> str:
    parts = []
    for k in session.keys:
        cls = f"ap {k.cls}".strip()
        body = span_markup(k.spans) if k.spans else escape(k.text)
        parts.append(f'<text class="{cls}" style="animation-delay:{fmt(k.at)}s" x="{x_of(k.col)}" y="{baseline(k.row)}">{body}</text>')
    return "\n".join(parts)


def render_row(row: Row, at: float) -> str:
    steps = min(len(plain(row.spans)) + 1, MAX_STEPS)
    cls = f"l {row.cls}".strip()
    return (
        f'<text class="{cls}" style="animation-delay:{at:.2f}s;animation-timing-function:steps({steps},jump-none)" '
        f'x="{x_of(row.col)}" y="{baseline(row.row)}">{span_markup(row.spans)}</text>'
    )


def keyframes(cursor: list[tuple[float, int, int]], total: float) -> str:
    frames = [f"{t / total * 100:.3f}%{{transform:translate({col * CHAR_W:g}px,{row * LINE_H}px)}}" for t, row, col in cursor]
    last = cursor[-1]
    frames.append(f"100%{{transform:translate({last[2] * CHAR_W:g}px,{last[1] * LINE_H}px)}}")
    return "".join(frames)


def svg_head(label: str, top: int, height: int, page_height: int, timing: dict[str, Any]) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {top} {WIDTH} {height}" preserveAspectRatio="none" width="{WIDTH}" height="{height}" role="img" aria-label="{escape(label, quote=True)}">
<defs>
<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="#000000" fill-opacity=".24"/></pattern>
<radialGradient id="vig" cx="50%" cy="50%" r="75%"><stop offset="60%" stop-color="#000000" stop-opacity="0"/><stop offset="100%" stop-color="#000a03" stop-opacity=".6"/></radialGradient>
<radialGradient id="bg" cx="50%" cy="40%" r="80%"><stop offset="0%" stop-color="#06160a"/><stop offset="100%" stop-color="#020803"/></radialGradient>
<filter id="glow" filterUnits="userSpaceOnUse" x="0" y="{top - 20}" width="{WIDTH}" height="{height + 40}"><feGaussianBlur stdDeviation="1.6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<style>
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:16px;fill:#41ff6e;white-space:pre}}
.h{{fill:#b6ffc8;font-weight:700}} .m{{fill:#1f9e46}} .u{{fill:#7dffa0;text-decoration:underline}}
.b{{fill:#b6ffc8}} .big{{font-size:34px}}
.l{{opacity:0;animation-name:type;animation-duration:{TYPE_DURATION}s;animation-fill-mode:forwards}}
@keyframes type{{0%{{opacity:1;clip-path:inset(0 100% 0 0)}}100%{{opacity:1;clip-path:inset(0 0 0 0)}}}}
.ap,.pg{{opacity:0;animation:on .25s linear forwards}} .rv{{fill:#03140a}}
@keyframes on{{0%,100%{{opacity:1}}}}
.sess{{animation:clr .25s linear {fmt(timing["clear_at"])}s forwards}}
@keyframes clr{{0%,100%{{opacity:0}}}}
.cur{{fill:#41ff6e;animation:blink 1s steps(1) infinite,follow {fmt(timing["total"])}s step-end forwards}}
@keyframes blink{{50%{{opacity:0}}}}
@keyframes follow{{{timing["keyframes"]}}}
.bell{{opacity:0;animation:bell .05s linear {fmt(timing["bell_at"])}s forwards,unbell .05s linear {fmt(timing["bell_at"] + 0.14)}s forwards}}
@keyframes bell{{0%,100%{{opacity:.35}}}} @keyframes unbell{{0%,100%{{opacity:0}}}}
.flk{{animation:flicker 4s steps(1) infinite}}
@keyframes flicker{{0%{{opacity:.035}}33%{{opacity:0}}61%{{opacity:.05}}79%{{opacity:.01}}}}
@media (prefers-reduced-motion:reduce){{
.l,.pg{{opacity:1;animation:none}} .flk{{animation:none;opacity:0}} .sess{{display:none}} .bell{{display:none}}
.cur{{animation:none;transform:translate({timing["end_col"] * CHAR_W:g}px,{timing["end_row"] * LINE_H}px)}}
}}
</style>
<rect width="{WIDTH}" height="{page_height}" rx="22" fill="#121a14"/>
<rect x="12" y="12" width="{WIDTH - 24}" height="{page_height - 24}" rx="16" fill="url(#bg)" stroke="#1c2e21" stroke-width="2"/>
<g filter="url(#glow)">
'''


def svg_tail(page_height: int) -> str:
    inner = f'x="12" y="12" width="{WIDTH - 24}" height="{page_height - 24}" rx="16"'
    return f'''<rect class="cur" x="{LEFT}" y="{FIRST_BASELINE - 14}" width="{CHAR_W:g}" height="18"/>
</g>
<rect class="bell" {inner} fill="#b6ffc8"/>
<rect {inner} fill="url(#scan)"/>
<rect class="flk" {inner} fill="#41ff6e" opacity="0"/>
<rect {inner} fill="url(#vig)"/>
</svg>
'''


def narration(profile: dict[str, Any], session: Session) -> str:
    s = profile["session"]
    printed = [k.text for k in session.keys if not k.spans and len(k.text) > 1]
    status = [t.strip() for t in printed if t.startswith("  ") or t.strip() in s["greeting"]]
    return (
        f"A terminal runs ssh {s['user']}@{s['host']}, is asked to accept the unknown ED25519 host key, answers yes, "
        f"connects and shows a welcome message: {', '.join(status)}. whoami answers {s['user']}, then man kris opens the manual page."
    )


def render(profile: dict[str, Any], patches_data: dict[str, Any], weather: dict[str, Any]) -> Output:
    tz = ZoneInfo(profile["weather"]["timezone"])
    now = datetime.fromisoformat(weather["current"]["time"]).replace(tzinfo=tz)
    patches = select_patches(patches_data["pull_requests"], profile["patches"]["max_entries"], profile["patches"]["text"])
    rows = layout_man_page(profile, patches)
    pager_row = rows[-1].row + 2
    page_height = baseline(pager_row) + BOTTOM_PAD
    session = build_session(profile, weather, now)

    clear_at = session.t
    man_start = clear_at + 0.25
    cursor = list(session.cursor) + [(clear_at, 0, 0)]
    typed_at: dict[int, float] = {}
    for i, row in enumerate(rows):
        typed_at[row.row] = man_start + i * TYPE_STEP
        cursor.append((typed_at[row.row] + TYPE_DURATION, row.row + 1, 0))
    pager_text = profile["man"]["pager"]
    pager_at = typed_at[rows[-1].row] + TYPE_STEP
    cursor.append((pager_at, pager_row, len(pager_text) + 1))
    total = pager_at + 0.01
    timing = {
        "clear_at": clear_at, "bell_at": session.bell_at, "total": total,
        "keyframes": keyframes(cursor, total), "end_row": pager_row, "end_col": len(pager_text) + 1,
    }
    session_markup = render_session(session)
    session_bottom = baseline(session.row) + 4
    pager_markup = (
        f'<g class="pg" style="animation-delay:{fmt(pager_at)}s"><rect x="{LEFT}" y="{baseline(pager_row) - 14}" '
        f'width="{len(pager_text) * CHAR_W:g}" height="18" fill="#41ff6e"/>'
        f'<text class="rv" x="{LEFT}" y="{baseline(pager_row)}">{escape(pager_text)}</text></g>'
    )

    slices = slice_rows(rows, page_height)
    svgs: list[tuple[str, str]] = []
    alts: list[str] = []
    for n, sl in enumerate(slices, 1):
        shown = [r for r in rows if sl.top - LINE_H <= baseline(r.row) <= sl.bottom + LINE_H]
        visible = [plain(r.spans).strip() for r in rows if sl.top <= baseline(r.row) - 12 and baseline(r.row) <= sl.bottom]
        if sl.top <= baseline(pager_row) <= sl.bottom:
            visible.append(f"Pager status: {pager_text.strip()}")
        label = " ".join(([narration(profile, session)] if n == 1 else []) + visible)
        body = []
        if sl.top < session_bottom:
            body.append(f'<g class="sess">{session_markup}</g>')
        body += [render_row(r, typed_at[r.row]) for r in shown]
        if sl.top - LINE_H <= baseline(pager_row) <= sl.bottom + LINE_H:
            body.append(pager_markup)
        svg = svg_head(label, sl.top, sl.height, page_height, timing) + "\n".join(body) + "\n" + svg_tail(page_height)
        svgs.append((f"{n:02d}.svg", svg))
        alts.append(label)

    digest = hashlib.sha256("".join(svg for _, svg in svgs).encode()).hexdigest()[:8]
    asset_dir = f"v{digest}"
    images = []
    for (name, _), sl, alt in zip(svgs, slices, alts):
        img = f'<img src="assets/{asset_dir}/{name}" width="100%" align="left" alt="{escape(alt, quote=True)}">'
        images.append(f'<a href="{escape(sl.url, quote=True)}">{img}</a>' if sl.url else img)
    readme = "<p>" + "".join(images) + "</p>\n"
    return Output(readme, svgs, asset_dir)


def load_profile() -> dict[str, Any]:
    """profile.toml, with every section's portrait file read into its lines."""
    profile = tomllib.loads((ROOT / "profile.toml").read_text())
    for section in profile["sections"]:
        if "portrait" in section:
            section["portrait"] = (ROOT / section["portrait"]).read_text().splitlines()
    return profile


def main() -> int:
    profile = load_profile()
    patches = json.loads((ROOT / "data" / "patches.json").read_text())
    weather = json.loads((ROOT / "data" / "weather.json").read_text())
    out = render(profile, patches, weather)
    assets = ROOT / "assets"
    for old in assets.iterdir() if assets.exists() else []:
        if old.is_dir() and old.name != out.asset_dir:
            shutil.rmtree(old)
    target = assets / out.asset_dir
    target.mkdir(parents=True, exist_ok=True)
    for name, svg in out.svgs:
        (target / name).write_text(svg)
    (ROOT / "README.md").write_text(out.readme)
    shown = select_patches(patches["pull_requests"], profile["patches"]["max_entries"], profile["patches"]["text"])
    print(f"=== Built {len(out.svgs)} slices into assets/{out.asset_dir} ===")
    print("=== Patches shown ===")
    for p in shown:
        curated = "curated" if p.ref in profile["patches"]["text"] else "TITLE, not curated"
        print(f"{p.ref}\t{curated}\t{p.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
