"""Browser creator selection and saved Creator management."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from ascendant import config
from ascendant.web import state

CREATOR_OVERRIDE = ContextVar('mission_creator', default=None)


def normalize_web_creator(entry: Any) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    uid = str(entry.get("uid") or entry.get("mid") or "").strip()
    space = str(entry.get("space") or "").strip()
    name = str(entry.get("name") or entry.get("uname") or "").strip()
    if not uid and "space.bilibili.com/" in space:
        uid = space.rstrip("/").split("/")[-1]
    if not uid.isdigit():
        return None
    if not space:
        space = f"https://space.bilibili.com/{uid}"
    if not name:
        name = f"Creator-{uid}"
    return {"name": name, "space": space, "uid": uid}


def load_web_creators() -> list[dict[str, str]]:
    try:
        raw = json.loads(config.CREATORS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []

    entries = raw.get("creators", []) if isinstance(raw, dict) else raw
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []

    normalized = []
    seen = set()
    for entry in entries:
        creator = normalize_web_creator(entry)
        if creator is None or creator["uid"] in seen:
            continue
        normalized.append(creator)
        seen.add(creator["uid"])
    return normalized


def save_web_creators(entries: list[dict[str, str]]) -> None:
    seen = set()
    normalized = []
    for entry in entries:
        creator = normalize_web_creator(entry)
        if creator is None or creator["uid"] in seen:
            continue
        normalized.append(creator)
        seen.add(creator["uid"])
    config.CREATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CREATORS_FILE.write_text(json.dumps({"creators": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")


def add_web_creator(name: str, space: str) -> dict[str, str]:
    normalized = normalize_web_creator({"name": name, "space": space})
    if normalized is None:
        raise ValueError("Cannot parse a valid Bilibili UID from that Creator data.")
    entries = [entry for entry in load_web_creators() if entry["uid"] != normalized["uid"]]
    entries.append(normalized)
    save_web_creators(entries)
    select_creator_by_uid(normalized["uid"])
    return normalized


def selected_creator() -> dict[str, str] | None:
    override = CREATOR_OVERRIDE.get()
    if override is not None:
        return override
    creators = load_web_creators()
    if not creators:
        return None
    if state.SELECTED_UID is None:
        return creators[0]
    for entry in creators:
        if entry["uid"] == state.SELECTED_UID:
            return entry
    return creators[0]


def select_creator_by_uid(uid: str) -> dict[str, str] | None:
    for entry in load_web_creators():
        if entry["uid"] == uid:
            state.SELECTED_UID = uid
            return entry
    return None
