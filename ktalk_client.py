#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk messaging helpers for autoalerter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

import cnf

logger = logging.getLogger("autoalerter")


def _load_ad_mapping_helpers() -> tuple[Any, Any] | None:
    try:
        from ad_mapping import get_recipient_profiles_from_ad_mapping, sync_ad_mentions

        return get_recipient_profiles_from_ad_mapping, sync_ad_mentions
    except Exception as exc:  # noqa: BLE001
        logger.exception("Cannot import ad_mapping helpers: %s", exc)
        return None


def _event_message(event: str, text: str) -> str:
    if event == "1":
        return f"🔴🤖{text}"
    return f"🟢🤖{text}"


def _normalize_mentions(users: list[str]) -> list[str]:
    mentions: list[str] = []
    pattern = re.compile(r"^@[A-Za-z0-9._=-]+:[A-Za-z0-9.-]+$")
    domain = str(cnf.KTALK_USER_DOMAIN).strip().lstrip("@")

    for user in users:
        raw = str(user).strip()
        if not raw:
            continue

        if raw.startswith("@"):
            mention = raw if ":" in raw else f"{raw}:{domain}"
        else:
            local_part = raw.lstrip("@")
            mention = f"@{local_part}" if ":" in local_part else f"@{local_part}:{domain}"

        if pattern.fullmatch(mention):
            if mention not in mentions:
                mentions.append(mention)
        else:
            logger.warning("Skip invalid mention value=%r normalized=%r", raw, mention)

    logger.debug("Normalized mentions count=%s", len(mentions))
    return mentions


def _normalize_login(value: str) -> str:
    login = str(value).strip()
    if login.startswith("@"):
        login = login[1:]
    if ":" in login:
        login = login.split(":", 1)[0]
    return login.strip().lower()


def _display_name_from_login(login: str) -> str:
    cleaned = _normalize_login(login)
    parts = [part for part in re.split(r"[._-]+", cleaned) if part]
    if len(parts) >= 2:
        return f"{parts[0].capitalize()} {parts[1].capitalize()}"
    if len(parts) == 1:
        return parts[0].capitalize()
    return cleaned


def _bot_api_url(endpoint: str) -> str:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    endpoint = endpoint.lstrip("/")
    return f"{base}/_matrix/client/strangler/api/v1/bot/{cnf.KTALK_JWT_TOKEN}/{endpoint}"


def _safe_bot_endpoint(endpoint: str) -> str:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    endpoint = endpoint.lstrip("/")
    return f"{base}/_matrix/client/strangler/api/v1/bot/***/{endpoint}"


def _is_mentions_invites_dry_run() -> bool:
    enabled = bool(cnf.KTALK_DRY_RUN_MENTIONS_INVITES)
    if enabled:
        logger.info("KTalk dry-run mode is enabled for invites and mentions")
    return enabled


def _bot_request(method: str, endpoint: str, **kwargs: Any) -> requests.Response:
    url = _bot_api_url(endpoint)
    logger.debug("KTalk Bot API request method=%s endpoint=%s", method, endpoint)
    response = requests.request(method, url, verify=cnf.VERIFY_SSL, timeout=cnf.REQUEST_TIMEOUT, **kwargs)
    return response


def search_users_by_bearer(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search users in KTalk telemetry API with Bearer token (for AD mapping use-cases)."""
    base_url = str(cnf.KTALK_BEARER_SEARCH_URL).strip()
    token = str(cnf.KTALK_BEARER_TOKEN).strip()
    talk_host = str(cnf.KTALK_TALK_HOST).strip()
    host = str(cnf.KTALK_HOST).strip()

    if not base_url or not token or not talk_host:
        logger.warning("KTalk bearer search config is incomplete")
        return []

    auth_header = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    headers = {
        "accept": "application/json",
        "authorization": auth_header,
        "talk-host": talk_host,
        "host": host,
        "user-agent": "autoalerter/1.0",
    }
    params = {"query": query, "limit": int(limit)}
    response = requests.get(base_url, headers=headers, params=params, verify=cnf.VERIFY_SSL, timeout=cnf.REQUEST_TIMEOUT)
    if not response.ok:
        logger.error("KTalk bearer search failed status=%s", response.status_code)
        return []
    payload = response.json()
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def send_invite_to_discussion(room_id: str, thread_id: str, user_id: str) -> bool:
    payload = {
        "room_id": room_id,
        "thread_id": thread_id,
        "user_id": user_id,
    }
    logger.info("KTalk invite start room_id=%s thread_id=%s user_id=%s", room_id, thread_id, user_id)
    response = _bot_request("POST", "invite_to_thread", json=payload)
    if not response.ok:
        logger.error("KTalk invite failed room_id=%s thread_id=%s user_id=%s status=%s", room_id, thread_id, user_id, response.status_code)
        return False

    logger.info("KTalk invite success room_id=%s thread_id=%s user_id=%s status=%s", room_id, thread_id, user_id, response.status_code)
    return True


def invite_user_by_bearer(room_id: str, user_id: str) -> bool:
    token = str(cnf.KTALK_BEARER_TOKEN).strip()
    if not token:
        logger.error("KTalk bearer token is empty, cannot invite user")
        return False

    auth_header = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:")
    invite_url = f"{base}/_matrix/client/v3/rooms/{room_path}/invite"
    payload = {
        "user_id": user_id,
    }
    headers = {
        "authorization": auth_header,
        "host": str(cnf.KTALK_HOST).strip(),
        "talk-host": str(cnf.KTALK_TALK_HOST).strip(),
    }

    logger.info("KTalk bearer invite start room_id=%s user_id=%s", room_id, user_id)
    response = requests.post(invite_url, headers=headers, json=payload, verify=cnf.VERIFY_SSL, timeout=cnf.REQUEST_TIMEOUT)
    response.encoding = "utf-8"
    if not response.ok:
        logger.error("KTalk bearer invite failed room_id=%s user_id=%s status=%s", room_id, user_id, response.status_code)
        return False

    logger.info("KTalk bearer invite success room_id=%s user_id=%s", room_id, user_id)
    return True


def fetch_telemetry(auth_token: str, base_url: str) -> str:
    try:
        headers = {
            "accept": "application/json",
            "authorization": auth_token if auth_token.lower().startswith("bearer ") else f"Bearer {auth_token}",
            "host": str(cnf.KTALK_HOST).strip(),
            "talk-host": str(cnf.KTALK_TALK_HOST).strip(),
        }
        response = requests.get(base_url, headers=headers, verify=cnf.VERIFY_SSL, timeout=cnf.REQUEST_TIMEOUT)
        response.encoding = "utf-8"
        if not response.ok:
            logger.error("KTalk telemetry members request failed status=%s", response.status_code)
            return "error"
        return response.text
    except Exception:  # noqa: BLE001
        logger.exception("KTalk telemetry members request failed")
        return "error"


def get_room_members(room_id: str) -> set[str]:
    logger.debug("Loading room members via telemetry room_id=%s", room_id)
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:")
    search_url = f"{base}/_matrix/client/v3/rooms/{room_path}/members"
    auth_token = str(cnf.KTALK_BEARER_TOKEN).strip()
    raw_payload = fetch_telemetry(auth_token, search_url)
    if raw_payload == "error":
        return set()

    try:
        payload = json.loads(raw_payload)
    except ValueError:
        logger.error("Failed to parse room members JSON")
        return set()

    members: set[str] = set()
    chunk = payload.get("chunk", [])
    if isinstance(chunk, list):
        for item in chunk:
            if not isinstance(item, dict):
                continue
            content = item.get("content", {})
            membership = str(content.get("membership", "")).strip().lower()
            if membership != "join":
                continue
            user_id = item.get("state_key") or item.get("user_id") or content.get("user_id")
            if isinstance(user_id, str) and user_id:
                members.add(user_id)

    logger.debug("Room members loaded via telemetry count=%s", len(members))
    return members


def send_to_ktalk_message(
    text: str,
    trigger_time: str,
    discussion_id: str,
    event: str,
    thread_id: str | None = None,
    mentions: list[str] | None = None,
    message_format: str = "plain",
    decorate_event: bool = True,
) -> str | None:
    logger.debug("Sending message to Kontur Talk room=%s thread_id=%r", discussion_id, thread_id)
    if message_format not in {"plain", "html", "markdown"}:
        raise ValueError(f"Unsupported ktalk message format: {message_format}")
    message_text = _event_message(event, text) if decorate_event else text
    final_text = f"{trigger_time} {message_text}".strip()
    if len(final_text) > 4096:
        logger.error("KTalk message exceeds 4096 chars len=%s", len(final_text))
        return None
    payload = {
        "room_id": discussion_id,
        "thread_id": thread_id,
        "format": message_format,
        "message": final_text,
        "mentions": mentions or [],
    }
    logger.debug(
        "Kontur Talk Bot API connection details: base_url=%s room_id=%s bot_user=%s endpoint=%s",
        cnf.KTALK_BASE_URL,
        discussion_id,
        cnf.KTALK_BOT_USER,
        _safe_bot_endpoint("send_message"),
    )
    response = _bot_request("POST", "send_message", json=payload)
    success = response.ok
    if not success:
        logger.error("Kontur Talk send failed status=%s", response.status_code)
        return None

    try:
        event_id = str(response.json().get("event_id", "")).strip()
    except ValueError:
        event_id = ""

    if not event_id:
        logger.error("Kontur Talk send response has no event_id")
        return None

    logger.debug("Kontur Talk send response status=%s event_id=%s", response.status_code, event_id)
    return event_id


def create_discussion(
    users: list[str],
    full_name: str,
    trigger_name: str,
    reply: str,
    jira_key: str,
    trigger_time: str,
) -> str:
    room_id = cnf.KTALK_ROOM_ID
    jira_issue_url = cnf.JIRA_ISSUE_BROWSE_URL.format(jira_key)
    first_message = f"Авария. {full_name}. {trigger_name}. {reply}. {jira_key}"
    logger.debug(
        "Using bot=%s fixed Kontur Talk room id=%s for full_name=%r trigger_name=%r",
        cnf.KTALK_BOT_USER,
        room_id,
        full_name,
        trigger_name,
    )
    event_id = send_to_ktalk_message(first_message, "", room_id, event="1", thread_id=None)
    if not event_id:
        raise RuntimeError("Failed to send first incident message to Kontur Talk")

    thread_message = (
        f"{trigger_time}\n"
        "event=1\n"
        f"{trigger_name}\n"
        f"{reply}\n"
        f"{jira_issue_url}"
    )
    thread_event_id = send_to_ktalk_message(
        thread_message,
        "",
        room_id,
        event="1",
        thread_id=event_id,
    )
    if not thread_event_id:
        logger.warning("Failed to send first thread reply for event=1 thread_id=%s", event_id)

    dry_run = _is_mentions_invites_dry_run()

    if users:
        normalized_logins = [_normalize_login(user) for user in users if str(user).strip()]
        mapping_helpers = _load_ad_mapping_helpers()
        if mapping_helpers is None:
            logger.warning("AD mapping helpers are unavailable, skip mention mapping")
            return event_id

        get_recipient_profiles_from_ad_mapping, sync_ad_mentions = mapping_helpers
        sync_ad_mentions(normalized_logins)
        recipient_profiles = get_recipient_profiles_from_ad_mapping(normalized_logins)
        if not recipient_profiles:
            logger.warning("No mention_id from mapping table for recipients")
            return event_id

        room_members = get_room_members(room_id)

        for login in normalized_logins:
            profile = recipient_profiles.get(login)
            user_id = profile.get("mention_id") if profile else None
            mention_candidates = _normalize_mentions([user_id] if user_id else [])
            if not mention_candidates:
                logger.warning("No valid mention_id for login=%s", login)
                continue

            mention = mention_candidates[0]
            logger.info("KTalk mention target resolved login=%s mention=%s", login, mention)
            full_name = (profile or {}).get("full_name", "").strip() or _display_name_from_login(login)
            mention_text = f"{full_name} {mention}"

            if dry_run:
                if mention not in room_members:
                    debug_invite_text = f"[DEBUG] Нужно пригласить в обсуждение: {full_name} {mention}"
                    logger.info("KTalk dry-run: invite skipped intentionally room_id=%s login=%s", room_id, login)
                    debug_invite_event_id = send_to_ktalk_message(
                        debug_invite_text,
                        "",
                        room_id,
                        event="1",
                        thread_id=None,
                        mentions=[],
                        message_format="plain",
                        decorate_event=False,
                    )
                    if not debug_invite_event_id:
                        logger.warning("KTalk dry-run invite debug message failed room_id=%s login=%s", room_id, login)

                debug_mention_text = f"[DEBUG] Нужно упомянуть в треде: {full_name} {mention}"
                logger.info("KTalk dry-run: mention sent as plain text without mentions API room_id=%s thread_id=%s login=%s", room_id, event_id, login)
                debug_mention_event_id = send_to_ktalk_message(
                    debug_mention_text,
                    "",
                    room_id,
                    event="1",
                    thread_id=event_id,
                    mentions=[],
                    message_format="plain",
                    decorate_event=False,
                )
                if not debug_mention_event_id:
                    logger.warning("KTalk dry-run mention debug message failed room_id=%s thread_id=%s login=%s", room_id, event_id, login)
                continue

            if mention not in room_members:
                logger.info("User is not in room members, invite via bearer API room_id=%s login=%s", room_id, login)
                invited = invite_user_by_bearer(room_id, mention)
                if not invited:
                    logger.warning("KTalk bearer invite failed room_id=%s login=%s", room_id, login)
                else:
                    logger.info("KTalk bearer invite success room_id=%s login=%s", room_id, login)

            logger.info(
                "KTalk mention send start room_id=%s thread_id=%s login=%s full_name=%s mention=%s",
                room_id,
                event_id,
                login,
                full_name,
                mention,
            )
            mention_event_id = send_to_ktalk_message(
                mention_text,
                "",
                room_id,
                event="1",
                thread_id=event_id,
                mentions=[mention],
                message_format="plain",
                decorate_event=False,
            )
            if not mention_event_id:
                logger.warning("KTalk mention failed room_id=%s thread_id=%s login=%s", room_id, event_id, login)
            else:
                logger.info("KTalk mention success room_id=%s thread_id=%s login=%s event_id=%s", room_id, event_id, login, mention_event_id)

    return event_id


def mark_discussion_resolved(discussion_id: str) -> None:
    logger.debug("No room rename operation for Kontur Talk room_id=%s", discussion_id)
