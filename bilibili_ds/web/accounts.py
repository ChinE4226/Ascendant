"""Browser account and QR sign-in workflows."""

from __future__ import annotations

import json
from typing import Any

from bilibili_ds.api.login import QrCodeLogin, QrCodeLoginEvents

from bilibili_ds import accounts as account_service, config, storage
from bilibili_ds.web import state


def account_summary() -> str:
    try:
        raw = json.loads(config.ACTIVE_ACCOUNT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "Guest mode"
    account_id = raw.get("id") if isinstance(raw, dict) else None
    if account_id:
        return f"Account {account_id} (UID {account_id})"
    return "Guest mode"


def account_records_summary() -> list[dict[str, Any]]:
    records = account_service.load_account_records()
    active = account_service.active_account_id()
    return [
        {
            "id": record["id"],
            "name": record.get("name"),
            "uid": record.get("uid"),
            "source": record.get("source"),
            "active": record["id"] == active,
        }
        for record in records
    ]


def select_account(account_id: str) -> dict[str, Any]:
    for record in account_service.load_account_records():
        if record["id"] == account_id:
            storage.write_json(config.ACTIVE_ACCOUNT_FILE, {"id": account_id})
            return {
                "id": record["id"],
                "name": record.get("name"),
                "uid": record.get("uid"),
                "source": record.get("source"),
            }
    raise ValueError("Account was not found.")


async def start_qr_sign_in() -> dict[str, Any]:
    state.QR_LOGIN = QrCodeLogin()
    await state.QR_LOGIN.generate_qrcode()
    config.QRCODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state.QR_LOGIN.save_qrcode(config.QRCODE_FILE)
    return {"qr_code_url": "/runtime/qrcode.png", "status": "waiting"}


async def qr_sign_in_status() -> dict[str, Any]:
    if state.QR_LOGIN is None:
        return {"status": "not_started"}
    event = await state.QR_LOGIN.check_state()
    if event == QrCodeLoginEvents.DONE:
        credential = state.QR_LOGIN.get_credential()
        record = await account_service.build_account_record(credential, source="qr")
        account_service.save_account_record(record)
        state.QR_LOGIN = None
        return {"status": "done", "account": {"id": record["id"], "name": record["name"], "uid": record["uid"]}}
    if event == QrCodeLoginEvents.TIMEOUT:
        state.QR_LOGIN = None
        return {"status": "timeout"}
    return {"status": "waiting"}


async def account_detail() -> dict[str, Any]:
    valid, detail, error = await account_service.load_account_detail(account_service.credential_from_env())
    if error:
        raise ValueError(error)
    if not valid or detail is None:
        raise ValueError("No valid signed-in account is available.")
    return detail


def sign_out_web(mode: str) -> dict[str, Any]:
    removed = 0
    if mode == "keep":
        try:
            config.ACTIVE_ACCOUNT_FILE.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        return {"mode": mode, "removed": removed}
    if mode == "selected":
        removed = account_service.clear_active_account_cache()
        return {"mode": mode, "removed": removed}
    if mode == "all":
        removed = account_service.clear_all_account_caches()
        return {"mode": mode, "removed": removed}
    raise ValueError("Invalid sign-out mode.")
