"""Credential sources, account caches, and account API operations."""

import hashlib
import os
from pathlib import Path
import re
from typing import Any

from bilibili_ds.api import Credential, user

from bilibili_ds.errors import public_error_message
from bilibili_ds import config
from bilibili_ds.client import (
    close_bilibili_client,
    configure_bilibili_client,
)
from bilibili_ds.storage import (
    read_json,
    write_json,
)


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in cookie_header.split(";"):
        if "=" not in item:
            continue
        key, value = item.strip().split("=", 1)
        if key:
            cookies[key] = value
    return cookies


def credential_from_cookie_header(cookie_header: str) -> Credential:
    return Credential.from_cookies(parse_cookie_header(cookie_header))


def credential_to_dict(credential: Credential) -> dict[str, str | None]:
    return {
        "sessdata": credential.sessdata,
        "bili_jct": credential.bili_jct,
        "dedeuserid": credential.dedeuserid,
        "buvid3": credential.buvid3,
        "buvid4": credential.buvid4,
        "ac_time_value": credential.ac_time_value,
    }


def credential_from_dict(data: dict[str, Any]) -> Credential:
    return Credential(
        sessdata=data.get("sessdata"),
        bili_jct=data.get("bili_jct"),
        dedeuserid=data.get("dedeuserid"),
        buvid3=data.get("buvid3"),
        buvid4=data.get("buvid4"),
        ac_time_value=data.get("ac_time_value"),
    )


def credential_from_environment() -> Credential | None:
    cookie_header = os.getenv("BILI_COOKIE")
    if cookie_header:
        return credential_from_cookie_header(cookie_header)

    if config.COOKIE_FILE.exists():
        cookie_header = config.COOKIE_FILE.read_text(encoding="utf-8").strip()
        if cookie_header:
            return credential_from_cookie_header(cookie_header)

    sessdata = os.getenv("BILI_SESSDATA")
    bili_jct = os.getenv("BILI_JCT")
    dedeuserid = os.getenv("BILI_DEDEUSERID")
    buvid3 = os.getenv("BILI_BUVID3")
    buvid4 = os.getenv("BILI_BUVID4")
    ac_time_value = os.getenv("BILI_AC_TIME_VALUE")
    if not any([sessdata, bili_jct, dedeuserid, buvid3, buvid4, ac_time_value]):
        return None

    return Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        dedeuserid=dedeuserid,
        buvid3=buvid3,
        buvid4=buvid4,
        ac_time_value=ac_time_value,
    )


def account_id_from_credential(credential: Credential) -> str:
    if credential.dedeuserid:
        return str(credential.dedeuserid)
    digest_source = "|".join(
        value or ""
        for value in [
            credential.sessdata,
            credential.bili_jct,
            credential.buvid3,
            credential.buvid4,
        ]
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"account-{digest}"


def account_path(account_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", account_id)
    return config.ACCOUNTS_DIR / f"{safe_id}.json"


def account_record_from_credential(
    credential: Credential,
    *,
    name: str | None = None,
    uid: str | None = None,
    source: str = "cache",
) -> dict[str, Any]:
    account_id = str(uid or credential.dedeuserid or account_id_from_credential(credential))
    return {
        "id": account_id,
        "name": name or f"Account {account_id}",
        "uid": str(uid or credential.dedeuserid or ""),
        "source": source,
        "credential": credential_to_dict(credential),
    }


async def build_account_record(credential: Credential, source: str) -> dict[str, Any]:
    account_id = account_id_from_credential(credential)
    name = f"Account {account_id}"
    uid = str(credential.dedeuserid or "")

    configure_bilibili_client()
    try:
        if await credential.check_valid():
            info = await user.get_self_info(credential)
            uid = str(info.get("mid") or uid)
            name = str(info.get("name") or info.get("uname") or name)
    except Exception:
        pass
    finally:
        await close_bilibili_client()

    return account_record_from_credential(credential, name=name, uid=uid, source=source)


def load_account_record(path: Path) -> dict[str, Any] | None:
    data = read_json(path, {})
    if not isinstance(data, dict):
        return None
    credential_data = data.get("credential")
    if not isinstance(credential_data, dict):
        return None
    credential = credential_from_dict(credential_data)
    account_id = str(data.get("id") or account_id_from_credential(credential))
    return {
        "id": account_id,
        "name": str(data.get("name") or f"Account {account_id}"),
        "uid": str(data.get("uid") or credential.dedeuserid or ""),
        "source": str(data.get("source") or "cache"),
        "credential": credential_to_dict(credential),
        "path": path,
    }


def legacy_account_record() -> dict[str, Any] | None:
    data = read_json(config.LEGACY_CREDENTIAL_FILE, {})
    if not isinstance(data, dict) or not data:
        return None
    try:
        credential = credential_from_dict(data)
    except Exception:
        return None
    return account_record_from_credential(credential, source="legacy")


def load_account_records() -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if config.ACCOUNTS_DIR.exists():
        for path in sorted(config.ACCOUNTS_DIR.glob("*.json")):
            record = load_account_record(path)
            if record is not None:
                records[record["id"]] = record

    legacy_record = legacy_account_record()
    if legacy_record is not None and legacy_record["id"] not in records:
        legacy_record["path"] = config.LEGACY_CREDENTIAL_FILE
        records[legacy_record["id"]] = legacy_record

    env_credential = credential_from_environment()
    if env_credential is not None:
        env_record = account_record_from_credential(env_credential, name="Environment account", source="environment")
        records.setdefault(env_record["id"], env_record)

    return sorted(records.values(), key=lambda record: record["name"].lower())


def active_account_id() -> str | None:
    data = read_json(config.ACTIVE_ACCOUNT_FILE, {})
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return None


def active_account_record() -> dict[str, Any] | None:
    records = load_account_records()
    if not records:
        return None

    active_id = active_account_id()
    if active_id:
        for record in records:
            if record["id"] == active_id:
                return record

    cached_records = [record for record in records if record.get("source") != "environment"]
    if len(cached_records) == 1:
        return cached_records[0]
    return None


def save_account_record(record: dict[str, Any], *, make_active: bool = True) -> None:
    account_id = str(record["id"])
    write_json(account_path(account_id), {key: value for key, value in record.items() if key != "path"})
    write_json(config.LEGACY_CREDENTIAL_FILE, record["credential"])
    if make_active:
        write_json(config.ACTIVE_ACCOUNT_FILE, {"id": account_id})


def credential_from_cache() -> Credential | None:
    record = active_account_record()
    if record is None:
        return None
    return credential_from_dict(record["credential"])


def credential_from_env() -> Credential | None:
    return credential_from_environment() or credential_from_cache()


def unlink_if_exists(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"Could not remove {path}: {exc}")
        return False


def clear_active_account_cache() -> int:
    removed = 0
    record = active_account_record()
    if record is not None:
        path = record.get("path")
        if isinstance(path, Path) and path != config.LEGACY_CREDENTIAL_FILE:
            removed += int(unlink_if_exists(path))
    removed += int(unlink_if_exists(config.ACTIVE_ACCOUNT_FILE))
    return removed


def clear_all_account_caches() -> int:
    removed = 0
    if config.ACCOUNTS_DIR.exists():
        for path in config.ACCOUNTS_DIR.glob("*.json"):
            removed += int(unlink_if_exists(path))
    for path in (config.ACTIVE_ACCOUNT_FILE, config.LEGACY_CREDENTIAL_FILE, config.COOKIE_FILE, config.QRCODE_FILE):
        removed += int(unlink_if_exists(path))
    return removed


async def load_account_detail(
    credential: Credential | None,
) -> tuple[bool | None, dict[str, Any] | None, str | None]:
    if credential is None or not credential.has_sessdata():
        return False, None, "No sign-in credential is available."

    configure_bilibili_client()
    try:
        if not await credential.check_valid():
            return False, None, None
        return True, await user.get_self_info(credential), None
    except Exception as exc:
        return None, None, public_error_message(exc)
    finally:
        await close_bilibili_client()
