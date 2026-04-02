#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AD to KTalk mention-id mapping helpers."""

from __future__ import annotations

import logging

import psycopg2
from psycopg2.extensions import connection as PgConnection

import cnf

logger = logging.getLogger("autoalerter")


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
