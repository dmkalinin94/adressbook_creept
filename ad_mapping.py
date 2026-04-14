#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AD to KTalk mention-id mapping helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from requests import RequestException

import cnf
from db import get_db_connection

logger = logging.getLogger("autoalerter")

try:
    from ldap3 import ALL, Connection, Server
    from ldap3.utils.conv import escape_filter_chars
except ImportError:  # pragma: no cover
    ALL = None  # type: ignore[assignment]
    Connection = None  # type: ignore[assignment]
    Server = None  # type: ignore[assignment]
    escape_filter_chars = None  # type: ignore[assignment]


@dataclass(slots=True)
class ADUser:
    login: str
    first_name: str
    last_name: str
    display_name: str
    title: str
    active: bool


@dataclass(slots=True)
class KTalkUser:
    mention_id: str
    display_name: str
    post: str
    deactivated: bool


class KTalkUnavailableError(RuntimeError):
    pass


def _normalize_logins(users: list[str]) -> list[str]:
    logins: list[str] = []
    for user in users:
        raw = str(user).strip()
        if not raw:
            continue
        if raw.startswith("@"):
            raw = raw[1:]
        if ":" in raw:
            raw = raw.split(":", 1)[0]
        login = raw.strip().lower()
        if login and login not in logins:
            logins.append(login)
    logger.debug("Normalized AD logins count=%s", len(logins))
    return logins


def _normalize(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("ё", "е")
    return value


def _build_ktalk_bearer() -> str:
    token = str(cnf.KTALK_BEARER_TOKEN).strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _ad_connection_params() -> tuple[str, str, str, str] | None:
    ad_host = str(cnf.AD_HOST).strip()
    ad_user = str(cnf.AD_USER).strip()
    ad_password = str(cnf.AD_PASSWORD).strip()
    ad_base_dn = str(cnf.AD_BASE_DN).strip()
    if not all([ad_host, ad_user, ad_password, ad_base_dn]):
        return None
    return ad_host, ad_user, ad_password, ad_base_dn


def _fetch_ad_user_with_connection(conn: Connection, ad_base_dn: str, login: str) -> ADUser | None:
    safe_login = escape_filter_chars(login) if escape_filter_chars else login
    search_filter = f"(&(objectClass=user)(sAMAccountName={safe_login}))"
    conn.search(
        search_base=ad_base_dn,
        search_filter=search_filter,
        attributes=["givenName", "sn", "displayName", "title", "userAccountControl"],
    )
    if not conn.entries:
        logger.debug("AD user not found login=%s", login)
        return None

    entry = conn.entries[0]
    uac = int(getattr(entry, "userAccountControl", 0).value or 0)
    is_active = not bool(uac & 0x0002)
    return ADUser(
        login=login,
        first_name=str(getattr(entry, "givenName", "").value or ""),
        last_name=str(getattr(entry, "sn", "").value or ""),
        display_name=str(getattr(entry, "displayName", "").value or ""),
        title=str(getattr(entry, "title", "").value or ""),
        active=is_active,
    )


def _fetch_ad_users(logins: list[str]) -> dict[str, ADUser | None]:
    if Server is None or Connection is None:
        logger.error("ldap3 is not installed, AD sync is disabled")
        return {login: None for login in logins}

    params = _ad_connection_params()
    if params is None:
        logger.warning("AD config is incomplete, skip AD lookup")
        return {login: None for login in logins}

    ad_host, ad_user, ad_password, ad_base_dn = params
    server = Server(ad_host, get_info=ALL)
    users: dict[str, ADUser | None] = {}

    with Connection(server, user=ad_user, password=ad_password, auto_bind=True) as conn:
        for login in logins:
            users[login] = _fetch_ad_user_with_connection(conn, ad_base_dn, login)

    return users


def _search_ktalk_users(query: str, limit: int = 15) -> list[KTalkUser]:
    base_url = str(cnf.KTALK_BEARER_SEARCH_URL).strip()
    talk_host = str(cnf.KTALK_TALK_HOST).strip()
    host = str(cnf.KTALK_HOST).strip()
    bearer = _build_ktalk_bearer()
    if not base_url or not talk_host or not bearer:
        logger.warning("KTalk bearer config is incomplete, skip KTalk search")
        return []

    headers = {
        "accept": "application/json",
        "authorization": bearer,
        "talk-host": talk_host,
        "host": host,
        "user-agent": "autoalerter/1.0",
    }
    try:
        response = requests.get(
            base_url,
            headers=headers,
            params={"query": query, "limit": int(limit)},
            verify=cnf.VERIFY_SSL,
            timeout=cnf.REQUEST_TIMEOUT,
        )
    except RequestException as exc:
        logger.exception("Kontur Talk request failed")
        raise KTalkUnavailableError("Kontur Talk lookup failed") from exc

    if not response.ok:
        logger.error("Kontur Talk API non-OK status: %s", response.status_code)
        raise KTalkUnavailableError("Kontur Talk lookup failed")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.exception("Kontur Talk response is not valid JSON")
        raise KTalkUnavailableError("Kontur Talk lookup failed") from exc

    items = payload.get("items", [])
    if not isinstance(items, list):
        return []

    return [
        KTalkUser(
            mention_id=str(item.get("user_id", "") or "").strip(),
            display_name=str(item.get("display_name", "") or "").strip(),
            post=str(item.get("post", "") or "").strip(),
            deactivated=bool(item.get("general_deactivated", False)),
        )
        for item in items
    ]


def _match_ktalk_user(ad_user: ADUser, candidates: list[KTalkUser]) -> KTalkUser | None:
    ad_name = _normalize(f"{ad_user.first_name} {ad_user.last_name}")
    ad_name_rev = _normalize(f"{ad_user.last_name} {ad_user.first_name}")
    ad_title = _normalize(ad_user.title)

    for candidate in candidates:
        if candidate.deactivated:
            continue
        name = _normalize(candidate.display_name)
        post = _normalize(candidate.post)
        if name in {ad_name, ad_name_rev} and ad_title and post == ad_title:
            return candidate

    for candidate in candidates:
        if candidate.deactivated:
            continue
        name = _normalize(candidate.display_name)
        if name in {ad_name, ad_name_rev}:
            return candidate

    return None


def _upsert_mapping_row(
    ad_login: str,
    ad_user: ADUser | None,
    ktalk_user: KTalkUser | None,
    ad_active: bool,
    matched: bool,
) -> None:
    now = datetime.now(timezone.utc)
    values = {
        "ad_login": ad_login,
        "ad_first_name": ad_user.first_name if ad_user else None,
        "ad_last_name": ad_user.last_name if ad_user else None,
        "ad_display_name": ad_user.display_name if ad_user else None,
        "ad_title": ad_user.title if ad_user else None,
        "ad_active": ad_active,
        "ktalk_mention_id": ktalk_user.mention_id if ktalk_user else None,
        "ktalk_display_name": ktalk_user.display_name if ktalk_user else None,
        "ktalk_post": ktalk_user.post if ktalk_user else None,
        "ktalk_matched": matched,
        "ktalk_deactivated": ktalk_user.deactivated if ktalk_user else None,
        "last_sync_at": now,
        "updated_at": now,
    }
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_UPSERT_AD_KTALK_MAPPING, values)


def sync_ad_mentions(users: list[str]) -> None:
    """Resolve AD users and KTalk mention IDs, then upsert mapping table."""
    logins = _normalize_logins(users)
    if not logins:
        return

    try:
        ad_users = _fetch_ad_users(logins)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AD batch lookup failed for count=%s: %s", len(logins), exc)
        ad_users = {login: None for login in logins}

    for login in logins:
        ad_user = ad_users.get(login)

        if ad_user is None:
            _upsert_mapping_row(login, None, None, ad_active=False, matched=False)
            continue

        if not ad_user.active:
            _upsert_mapping_row(login, ad_user, None, ad_active=False, matched=False)
            continue

        try:
            candidates = _search_ktalk_users(query=ad_user.login)
        except KTalkUnavailableError as exc:
            logger.warning("KTalk is temporarily unavailable for login=%s: %s", login, exc)
            _upsert_mapping_row(login, ad_user, None, ad_active=True, matched=False)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("KTalk search failed for login=%s: %s", login, exc)
            _upsert_mapping_row(login, ad_user, None, ad_active=True, matched=False)
            continue

        matched = _match_ktalk_user(ad_user, candidates)
        _upsert_mapping_row(login, ad_user, matched, ad_active=True, matched=matched is not None)


def get_mentions_map_from_ad_mapping(users: list[str]) -> dict[str, str]:
    logins = _normalize_logins(users)
    if not logins:
        return {}

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_GET_MENTION_IDS_BY_AD_LOGINS, {"logins": logins})
        rows = cur.fetchall()

    mention_by_login = {
        str(row[0]).strip().lower(): str(row[1]).strip()
        for row in rows
        if row and row[1]
    }
    logger.debug("Resolved mention_id map count=%s requested=%s", len(mention_by_login), len(logins))
    return mention_by_login


def get_recipient_profiles_from_ad_mapping(users: list[str]) -> dict[str, dict[str, str]]:
    logins = _normalize_logins(users)
    if not logins:
        return {}

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_GET_MENTION_IDS_BY_AD_LOGINS, {"logins": logins})
        rows = cur.fetchall()

    profiles: dict[str, dict[str, str]] = {}
    for row in rows:
        if not row or not row[1]:
            continue
        login = str(row[0]).strip().lower()
        mention_id = str(row[1]).strip()
        full_name = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        profiles[login] = {
            "mention_id": mention_id,
            "full_name": full_name,
        }

    logger.debug("Resolved recipient profiles count=%s", len(profiles))
    return profiles


def get_mentions_from_ad_mapping(users: list[str]) -> list[str]:
    logins = _normalize_logins(users)
    mention_by_login = get_mentions_map_from_ad_mapping(logins)

    mentions: list[str] = []
    for login in logins:
        mention_id = mention_by_login.get(login)
        if mention_id:
            mentions.append(mention_id)

    logger.debug("Resolved mention_id from DB mapping count=%s requested=%s", len(mentions), len(logins))
    return mentions
