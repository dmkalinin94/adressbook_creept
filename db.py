# -*- coding: utf-8 -*-

from __future__ import annotations

import logging

import psycopg2
from psycopg2.extensions import connection as PgConnection

import cnf

logger = logging.getLogger("autoalerter")


def get_db_connection() -> PgConnection:
    logger.debug("Opening PostgreSQL connection: host=%s db=%s", cnf.DB_HOST, cnf.DB_NAME)
    return psycopg2.connect(
        dbname=cnf.DB_NAME,
        user=cnf.DB_USER,
        password=cnf.DB_PASSWORD,
        host=cnf.DB_HOST,
        port=cnf.DB_PORT,
        options=cnf.DB_OPTIONS,
    )
