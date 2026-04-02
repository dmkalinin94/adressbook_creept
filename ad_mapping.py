#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AD to KTalk mention-id mapping helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg2
import requests
from psycopg2.extensions import connection as PgConnection

import cnf

logger = logging.getLogger("autoalerter")

try:
    from ldap3 import ALL, Connection, Server
except ImportError:  # pragma: no cover
    ALL = None  # type: ignore[assignment]
    Connection = None  # type: ignore[assignment]
    Server = None  # type: ignore[assignment]


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


def _db_connect() -> PgConnection:
    logger.debug("Opening PostgreSQL connection for mention mapping: host=%s db=%s", cnf.dbhost, cnf.dbname)
    return psycopg2.connect(
        dbname=cnf.dbname,
        user=cnf.dbuser,
        password=cnf.dbpassword,
        host=cnf.dbhost,
        port=cnf.dbport,
        options=cnf.dboptions,
    )


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
    logger.debug("Normalized AD logins count=%s values=%s", len(logins), logins)
    return logins


def _normalize(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("ё", "е")
    return value


def _build_ktalk_bearer() -> str:
    token = str(cnf.CONFIG.get("ktalk_bearer_token", "")).strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _fetch_ad_user(login: str) -> ADUser | None:
    if Server is None or Connection is None:
        logger.error("ldap3 is not installed, AD sync is disabled")
        return None

    ad_host = str(cnf.CONFIG.get("ad_host", "")).strip()
    ad_user = str(cnf.CONFIG.get("ad_user", "")).strip()
    ad_password = str(cnf.CONFIG.get("ad_password", "")).strip()
    ad_base_dn = str(cnf.CONFIG.get("ad_base_dn", "")).strip()

    if not all([ad_host, ad_user, ad_password, ad_base_dn]):
        logger.warning("AD config is incomplete, skip AD lookup for login=%s", login)
        return None

    server = Server(ad_host, get_info=ALL)
    with Connection(server, user=ad_user, password=ad_password, auto_bind=True) as conn:
        search_filter = f"(&(objectClass=user)(sAMAccountName={login}))"
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


def _search_ktalk_users(query: str, limit: int = 15) -> list[KTalkUser]:
    base_url = str(cnf.CONFIG.get("ktalk_base_url", "")).strip()
    talk_host = str(cnf.CONFIG.get("ktalk_talk_host", "")).strip()
    host = str(cnf.CONFIG.get("ktalk_host", "chat.ktalk.ru")).strip()
    bearer = _build_ktalk_bearer()
    if not base_url or not talk_host or not bearer:
        logger.warning("KTalk bearer config is incomplete, skip KTalk search query=%r", query)
        return []

    headers = {
        "accept": "application/json",
        "authorization": bearer,
        "talk-host": talk_host,
        "host": host,
        "user-agent": "autoalerter/1.0",
    }
    response = requests.get(
        base_url,
        headers=headers,
        params={"query": query, "limit": int(limit)},
        verify=bool(cnf.CONFIG.get("verify_ssl", True)),
        timeout=int(cnf.CONFIG.get("request_timeout", 15)),
    )
    if not response.ok:
        logger.error("KTalk bearer search failed query=%r status=%s body=%s", query, response.status_code, response.text)
        return []

    payload = response.json()
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
    with _db_connect() as conn, conn.cursor() as cur:
        cur.execute(cnf.query.upsertAdKtalkMapping, values)


def sync_ad_mentions(users: list[str]) -> None:
    """Resolve AD users and KTalk mention IDs, then upsert mapping table."""
    logins = _normalize_logins(users)
    if not logins:
        return

    for login in logins:
        try:
            ad_user = _fetch_ad_user(login)
        except Exception as exc:  # noqa: BLE001
            logger.exception("AD lookup failed for login=%s: %s", login, exc)
            _upsert_mapping_row(login, None, None, ad_active=False, matched=False)
            continue

        if ad_user is None:
            _upsert_mapping_row(login, None, None, ad_active=False, matched=False)
            continue

        if not ad_user.active:
            _upsert_mapping_row(login, ad_user, None, ad_active=False, matched=False)
            continue

        try:
            candidates = _search_ktalk_users(query=f"{ad_user.last_name} {ad_user.first_name}")
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

    logger.debug("Resolving mention_id map from DB mapping for logins=%s", logins)
    with _db_connect() as conn, conn.cursor() as cur:
        cur.execute(cnf.query.getMentionIdsByAdLogins, {"logins": logins})
        rows = cur.fetchall()

    mention_by_login = {
        str(row[0]).strip().lower(): str(row[1]).strip()
        for row in rows
        if row and row[1]
    }
    logger.debug(
        "Resolved mention_id map count=%s requested=%s",
        len(mention_by_login),
        len(logins),
    )
    return mention_by_login


def get_mentions_from_ad_mapping(users: list[str]) -> list[str]:
    logins = _normalize_logins(users)
    mention_by_login = get_mentions_map_from_ad_mapping(logins)

    mentions: list[str] = []
    for login in logins:
        mention_id = mention_by_login.get(login)
        if mention_id:
            mentions.append(mention_id)

    logger.debug(
        "Resolved mention_id from DB mapping count=%s requested=%s resolved=%s",
        len(mentions),
        len(logins),
        mentions,
    )
    return mentions
