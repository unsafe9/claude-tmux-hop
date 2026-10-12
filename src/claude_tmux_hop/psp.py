"""OSC 7501 Program Status Protocol parser and per-pane record set.

Reports may arrive split across reads. Claude Code's own state uses the root
record (no id). Subagents use child ids. Hop still stores one state per pane.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

PSP_OPTION = "@hop-psp"
APP_NAME = "claude-code"

ESC = 0x1B
BEL = 0x07
ST = bytes((ESC, 0x5C))
MAX_SEQUENCE = 4096
MAX_RECORDS = 256
MAX_ID_LEN = 128
MAX_ID_DEPTH = 8
MAX_KEY_LEN = 16
MAX_MSG_ENCODED = 2732
MAX_MSG_DECODED = 2048
MAX_TITLE_ENCODED = 256
MAX_TITLE_DECODED = 192

STATES = frozenset({"idle", "working", "done", "blocked", "error", "clear"})
KINDS = frozenset({"permission", "question", "auth"})
_STATE_RANK = {"blocked": 0, "working": 1, "done": 2, "error": 2, "idle": 3}
_KIND_RANK = {"auth": 0, "permission": 1, "question": 2, "": 3}
_HOP_STATE = {
    "blocked": "waiting",
    "working": "active",
    "done": "idle",
    "error": "idle",
    "idle": "idle",
}

_KEY_RE = re.compile(r"[a-z]+")
_VALUE_RE = re.compile(r"[A-Za-z0-9_.,+/=-]*")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9_.+-]{1,32}")
_APP_RE = re.compile(r"[A-Za-z0-9_.+-]{1,32}")


@dataclass(frozen=True)
class Query:
    """Bare probe body '?'. A capability reply also starts with '?' and is ignored."""

    terminator: bytes


@dataclass(frozen=True)
class Report:
    id: str | None
    state: str
    kind: str = ""
    app: str = ""
    title: str = ""
    msg: str = ""


@dataclass(frozen=True)
class ShellPrompt:
    """OSC 133 A from the shell. Claude's redraw marker is not one of these."""


@dataclass(frozen=True)
class FullReset:
    """RIS drops every record."""


Event = Query | Report | ShellPrompt | FullReset


@dataclass(frozen=True)
class Snapshot:
    """Empty state means this pane has nothing to show."""

    state: str = ""
    reason: str = ""
    task: str = ""


@dataclass
class _Record:
    state: str
    app: str = ""
    kind: str = ""
    title: str = ""
    msg: str = ""
    updated: int = 0


def capability_reply(terminator: bytes) -> bytes:
    """Claude accepts any OSC 7501 body that starts with '?'."""
    body = (
        b"? : states=idle,working,done,blocked,error"
        b" : kinds=permission,question,auth"
    )
    return bytes((ESC, 0x5D)) + b"7501;" + body + terminator


def _decode_text(value: str, encoded_limit: int, decoded_limit: int) -> str | None:
    """Decoded text, '' when absent, None when this field rejects the report."""
    if value == "":
        return ""
    if len(value) > encoded_limit:
        return None
    try:
        raw = base64.b64decode(value + ("=" * (-len(value) % 4)), validate=True)
    except Exception:
        return None
    if len(raw) > decoded_limit:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    return text


def _valid_id(value: str) -> bool:
    if len(value) > MAX_ID_LEN or value.count("/") + 1 > MAX_ID_DEPTH:
        return False
    return all(_SEGMENT_RE.fullmatch(part) for part in value.split("/"))


def parse_report(body: str) -> Report | None:
    """None means ignore the report and leave stored records untouched."""
    fields: dict[str, str] = {}
    for piece in body.split(":"):
        piece = piece.strip()
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or _KEY_RE.fullmatch(key) is None:
            continue
        if len(key) > MAX_KEY_LEN:
            return None
        if _VALUE_RE.fullmatch(value) is None:
            continue
        fields[key] = value

    state = fields.get("state", "")
    if state not in STATES:
        return None

    record_id: str | None = None
    if "id" in fields:
        record_id = fields["id"]
        if not _valid_id(record_id):
            return None

    if state == "clear":
        return Report(id=record_id, state="clear")

    kind = fields.get("kind", "") if state == "blocked" else ""
    if kind not in KINDS:
        kind = ""

    app = fields.get("app", "")
    if app and _APP_RE.fullmatch(app) is None:
        app = ""

    title = ""
    if "title" in fields:
        decoded = _decode_text(fields["title"], MAX_TITLE_ENCODED, MAX_TITLE_DECODED)
        if decoded is None:
            return None
        title = decoded

    msg = ""
    if "msg" in fields:
        decoded = _decode_text(fields["msg"], MAX_MSG_ENCODED, MAX_MSG_DECODED)
        if decoded is None:
            return None
        msg = decoded

    return Report(id=record_id, state=state, kind=kind, app=app, title=title, msg=msg)


class Parser:
    def __init__(self) -> None:
        self._mode = "ground"
        self._osc = bytearray()

    def feed(self, data: bytes) -> list[Event]:
        events: list[Event] = []
        for byte in data:
            self._step(byte, events)
        return events

    def _step(self, byte: int, events: list[Event]) -> None:
        if self._mode == "ground":
            if byte == ESC:
                self._mode = "esc"
            return
        if self._mode == "esc":
            if byte == 0x5D:
                self._osc.clear()
                self._mode = "osc"
            elif byte == ord("c"):
                events.append(FullReset())
                self._mode = "ground"
            else:
                self._mode = "ground"
            return
        if self._mode == "osc":
            if byte == BEL:
                self._finish(bytes((BEL,)), events)
                return
            if byte == ESC:
                self._mode = "osc_esc"
                return
            if byte in (0x18, 0x1A):
                self._mode = "ground"
                return
            self._osc.append(byte)
            if 2 + len(self._osc) + 1 > MAX_SEQUENCE:
                self._mode = "ground"
                self._osc.clear()
            return
        if byte == 0x5C:
            self._finish(ST, events)
            return
        self._mode = "ground"

    def _finish(self, terminator: bytes, events: list[Event]) -> None:
        raw = bytes(self._osc)
        self._osc.clear()
        self._mode = "ground"
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return
        if text.startswith("7501;"):
            body = text[5:]
            stripped = body.strip()
            if stripped == "?":
                events.append(Query(terminator))
                return
            if stripped.startswith("?"):
                return
            report = parse_report(body)
            if report is not None:
                events.append(report)
            return
        if text.startswith("133;"):
            params = text[4:]
            # Claude marks its own turns with A;redraw=0. A shell prompt does not.
            if params.split(";", 1)[0] == "A" and "redraw=0" not in params:
                events.append(ShellPrompt())


class Ledger:
    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}
        self._clock = 0

    def apply(self, event: Event) -> None:
        if isinstance(event, Query):
            return
        if isinstance(event, FullReset):
            self._records.clear()
            return
        if isinstance(event, ShellPrompt):
            self._drop_ephemeral()
            return
        self._apply_report(event)

    def snapshot(self) -> Snapshot:
        if not self._records:
            return Snapshot()
        _record_id, record = min(
            self._records.items(),
            key=lambda item: (
                _STATE_RANK.get(item[1].state, 9),
                _KIND_RANK.get(item[1].kind, 9),
                0 if item[0] == "" else 1,
                item[0],
            ),
        )
        return Snapshot(
            state=_HOP_STATE.get(record.state, ""),
            reason=record.kind if record.state == "blocked" else "",
            task=record.msg or record.title,
        )

    def _apply_report(self, report: Report) -> None:
        key = report.id or ""
        if report.state == "clear":
            if report.id is None:
                self._records.clear()
                return
            prefix = key + "/"
            for existing in list(self._records):
                if existing == key or existing.startswith(prefix):
                    del self._records[existing]
            return

        app = report.app or self._inherited_app(key)
        if app != APP_NAME:
            return
        self._clock += 1
        if key not in self._records and len(self._records) >= MAX_RECORDS:
            oldest = min(self._records, key=lambda item: self._records[item].updated)
            del self._records[oldest]
        self._records[key] = _Record(
            state=report.state,
            app=app,
            kind=report.kind,
            title=report.title,
            msg=report.msg,
            updated=self._clock,
        )

    def _inherited_app(self, record_id: str) -> str:
        parent = record_id.rpartition("/")[0] if record_id else ""
        while True:
            found = self._records.get(parent)
            if found and found.app:
                return found.app
            if not parent:
                return ""
            parent = parent.rpartition("/")[0]

    def _drop_ephemeral(self) -> None:
        for key, record in list(self._records.items()):
            if record.state in ("working", "blocked"):
                del self._records[key]
