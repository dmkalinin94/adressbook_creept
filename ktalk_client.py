#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk messaging helpers for autoalerter."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

import cnf
from ad_mapping import get_mentions_map_from_ad_mapping

logger = logging.getLogger("autoalerter")


def _event_message(event: str, text: str) -> str:
    if event == "1":
        return f"🔴🤖{text}"
    return f"🟢🤖{text}"


def _normalize_mentions(users: list[str]) -> list[str]:
    mentions: list[str] = []
    pattern = re.compile(r"^@[A-Za-z0-9._=-]+:[A-Za-z0-9.-]+$")
    domain = str(cnf.ktalkUserDomain).strip().lstrip("@")

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

    logger.debug("Normalized mentions count=%s values=%s", len(mentions), mentions)
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
    base = str(cnf.ktalkBaseURL).rstrip("/")
    endpoint = endpoint.lstrip("/")
    return f"{base}/_matrix/client/strangler/api/v1/bot/{cnf.ktalkJwtToken}/{endpoint}"


def _bot_request(method: str, endpoint: str, **kwargs: Any) -> requests.Response:
    url = _bot_api_url(endpoint)
    logger.debug("KTalk Bot API request method=%s endpoint=%s", method, endpoint)
    response = requests.request(method, url, verify=False, timeout=30, **kwargs)
    return response


def search_users_by_bearer(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search users in KTalk telemetry API with Bearer token (for AD mapping use-cases)."""
    config = cnf.CONFIG
    base_url = str(config.get("ktalk_base_url", "")).strip()
    token = str(config.get("ktalk_bearer_token", "")).strip()
    talk_host = str(config.get("ktalk_talk_host", "")).strip()
    host = str(config.get("ktalk_host", "chat.ktalk.ru")).strip()

    if not base_url or not token or not talk_host:
        logger.warning("KTalk bearer search config is incomplete, skip query=%r", query)
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
    response = requests.get(base_url, headers=headers, params=params, verify=False, timeout=30)
    if not response.ok:
        logger.error("KTalk bearer search failed status=%s body=%s", response.status_code, response.text)
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
    logger.debug("Sending discussion invite room_id=%s thread_id=%s user_id=%s", room_id, thread_id, user_id)
    response = _bot_request("POST", "invite_to_thread", json=payload)
    if not response.ok:
        logger.error(
            "Kontur Talk invite failed status=%s user_id=%s body=%s",
            response.status_code,
            user_id,
            response.text,
        )
        return False
    return True


def get_room_members(room_id: str) -> set[str]:
    logger.debug("Loading room members room_id=%s", room_id)
    response = _bot_request("GET", "get_room_members", params={"room_id": room_id})
    if not response.ok:
        logger.error("Failed to load room members status=%s body=%s", response.status_code, response.text)
        return set()

    payload = response.json()
    members: set[str] = set()

    raw_members = payload.get("members", [])
    if isinstance(raw_members, list):
        for item in raw_members:
            if isinstance(item, str):
                members.add(item)
            elif isinstance(item, dict):
                user_id = item.get("user_id")
                if isinstance(user_id, str):
                    members.add(user_id)

    joined = payload.get("joined")
    if isinstance(joined, dict):
        members.update(str(user_id) for user_id in joined.keys())
    elif isinstance(joined, list):
        members.update(str(user_id) for user_id in joined)

    logger.debug("Room members loaded count=%s", len(members))
    return members


def send_to_ktalk_message(
    text: str,
    trigger_time: str,
    discussion_id: str,
    event: str,
    thread_id: str | None = None,
    mentions: list[str] | None = None,
    message_format: str = "plain",
) -> str | None:
    logger.debug("Sending message to Kontur Talk room=%s thread_id=%r", discussion_id, thread_id)
    if message_format not in {"plain", "html", "markdown"}:
        raise ValueError(f"Unsupported ktalk message format: {message_format}")
    message_text = _event_message(event, text)
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
        cnf.ktalkBaseURL,
        discussion_id,
        cnf.ktalkBotUser,
        _bot_api_url("send_message"),
    )
    response = _bot_request("POST", "send_message", json=payload)
    success = response.ok
    if not success:
        logger.error("Kontur Talk send failed status=%s body=%s", response.status_code, response.text)
        return None

    try:
        event_id = str(response.json().get("event_id", "")).strip()
    except ValueError:
        event_id = ""

    if not event_id:
        logger.error("Kontur Talk send response has no event_id. body=%s", response.text)
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
    room_id = cnf.ktalkRoomId
    jira_issue_url = cnf.jiraIssueBrowseURL.format(jira_key)
    first_message = f"Авария. {full_name}. {trigger_name}. {reply}. {jira_key}"
    logger.debug(
        "Using bot=%s fixed Kontur Talk room id=%s for full_name=%r trigger_name=%r",
        cnf.ktalkBotUser,
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

    if users:
        mention_by_login = get_mentions_map_from_ad_mapping(users)
        normalized_logins = [_normalize_login(user) for user in users if str(user).strip()]
        if not mention_by_login:
            logger.warning("No mention_id from mapping table for recipients=%s", users)
            return event_id

        room_members = get_room_members(room_id)
        failed_invites: list[str] = []

        for login in normalized_logins:
            user_id = mention_by_login.get(login)
            mention_candidates = _normalize_mentions([user_id] if user_id else [])
            if not mention_candidates:
                logger.warning("No valid mention_id for login=%s", login)
                continue

            mention = mention_candidates[0]
            if mention not in room_members:
                if not send_invite_to_discussion(room_id, event_id, mention):
                    failed_invites.append(mention)

            mention_text = f"{_display_name_from_login(login)} {mention}"
            mention_event_id = send_to_ktalk_message(
                mention_text,
                "",
                room_id,
                event="1",
                thread_id=event_id,
                mentions=[mention],
                message_format="plain",
            )
            if not mention_event_id:
                logger.warning("Failed to mention user=%s in thread_id=%s", mention, event_id)

        if failed_invites:
            logger.warning("Failed to invite users to discussion: %s", failed_invites)
    return event_id


def mark_discussion_resolved(discussion_id: str) -> None:
    logger.debug("No room rename operation for Kontur Talk room_id=%s", discussion_id)
