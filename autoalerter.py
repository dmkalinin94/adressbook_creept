#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autoalerter: create/update incidents based on monitoring events."""

from __future__ import annotations

import argparse
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import datetime

import urllib3

import cnf
from db import get_db_connection
from jira_client import create_jira_incident, get_jira_data, validate_jira_incident_status
from ktalk_client import create_discussion, mark_discussion_resolved, send_to_ktalk_message

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*The 'im' API section is deprecated.*",
)

logger = logging.getLogger("autoalerter")


@dataclass(slots=True)
class EventPayload:
    event: str
    insight_id: str
    groups: str
    trigger_time: str
    trigger_name: str
    message: str


def _sanitize_log_message(message: str) -> str:
    sanitized = re.sub(r"(?i)bearer\s+[a-z0-9._\-=+/]+", "Bearer ***", message)
    sanitized = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    sanitized = re.sub(r"(?i)(password\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    sanitized = re.sub(r"(?i)(token\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    return sanitized


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = _sanitize_log_message(msg)
        record.args = ()
        return True


def configure_logging() -> None:
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        logger.debug("Logger already configured, skip reconfiguration")
        return
    handler = logging.FileHandler(cnf.LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    )
    handler.addFilter(_SecretFilter())
    logger.addHandler(handler)
    logger.debug("Logging configured. file=%s level=DEBUG", cnf.LOG_FILE)


def parse_args() -> EventPayload:
    logger.debug("Parsing CLI arguments")
    parser = argparse.ArgumentParser(description="Auto incident handler")
    parser.add_argument("--event", required=True, choices=["0", "1"])
    parser.add_argument("--insightId", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--triggerTime", required=True)
    parser.add_argument("--trigName", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    payload = EventPayload(
        event=args.event,
        insight_id=args.insightId,
        groups=args.groups,
        trigger_time=args.triggerTime,
        trigger_name=args.trigName,
        message=args.message,
    )
    logger.debug(
        "Args parsed: event=%s insight_id=%s trigger_name=%s",
        payload.event,
        payload.insight_id,
        payload.trigger_name,
    )
    return payload


def validate_insight_id(insight_id: str) -> str:
    logger.debug("Validating insight_id=%s", insight_id)
    if not re.fullmatch(r"^TZ-\d+", insight_id):
        logger.debug("Insight ID validation failed: %s", insight_id)
        raise ValueError(f"Invalid insightId: {insight_id}")
    logger.debug("Insight ID validation passed: %s", insight_id)
    return insight_id


def extract_shortname(groups: str) -> str:
    logger.debug("Extracting shortname from groups=%r", groups)
    match = re.search(r"SG/([^,/]+)", groups)
    if not match:
        logger.debug("Shortname extraction failed")
        raise ValueError("Cannot extract shortname from groups")
    short_name = match.group(1)
    logger.debug("Shortname extracted: %s", short_name)
    return short_name


def convert_trigger_time(trigger_time: str) -> str:
    dt = datetime.strptime(trigger_time, "%Y.%m.%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S.000 +0300")


def get_active_incident_count(insight_id: str) -> int:
    logger.debug("Query active incident count for insight_id=%s", insight_id)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_COUNT_ACTIVE_INCIDENTS, {"insight_id": insight_id})
        row = cur.fetchone()
    count = int(row[0]) if row else 0
    logger.debug("Active incident count result=%s for insight_id=%s", count, insight_id)
    return count


def get_last_room_id(insight_id: str) -> str | None:
    logger.debug("Query last chat room for insight_id=%s", insight_id)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_GET_LAST_ROOM_ID, {"insight_id": insight_id})
        row = cur.fetchone()
    room_id = row[0] if row else None
    logger.debug("Last room id=%r for insight_id=%s", room_id, insight_id)
    return room_id


def get_last_jira_issue_key(insight_id: str) -> str | None:
    logger.debug("Query last Jira issue key for insight_id=%s", insight_id)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_GET_LAST_JIRA_ISSUE_KEY, {"insight_id": insight_id})
        row = cur.fetchone()
    jira_issue_key = row[0] if row else None
    logger.debug("Last Jira issue key=%r for insight_id=%s", jira_issue_key, insight_id)
    return jira_issue_key


def close_open_incidents(insight_id: str) -> str | None:
    logger.debug("Closing open incidents for insight_id=%s", insight_id)
    last_room_id = get_last_room_id(insight_id)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_CLOSE_INCIDENTS, {"insight_id": insight_id})
    logger.debug("Close incidents executed. last_room_id=%r", last_room_id)
    return last_room_id


def update_event_counter(insight_id: str, delta: int) -> int:
    logger.debug("Updating event counter for insight_id=%s delta=%s", insight_id, delta)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_UPDATE_EVENT_COUNTER, {"insight_id": insight_id, "delta": delta})
        row = cur.fetchone()

    if row is None:
        raise RuntimeError(f"updateEventCounter returned no rows for insight_id={insight_id}")

    counter = int(row[0])
    logger.debug("Event counter updated for insight_id=%s counter=%s", insight_id, counter)
    return counter


def create_internal_incident(
    insight_id: str,
    short_name: str,
    full_name: str,
    trigger_name: str,
    recipients: list[str],
    trigger_start_time: str,
    discussion_id: str,
    jira_issue_key: str,
) -> None:
    logger.debug("Creating internal incident record for insight_id=%s", insight_id)
    values = {
        "insight_id": insight_id,
        "short_name": short_name,
        "full_name": full_name,
        "trigger_name": trigger_name,
        "recipients": recipients,
        "trigger_time": trigger_start_time,
        "rdiscussionid": discussion_id,
        "jira_issue_key": jira_issue_key,
    }
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(cnf.SQL_CREATE_INCIDENT, values)
    logger.debug("Internal incident record created for insight_id=%s", insight_id)


def default_issue_template() -> dict[str, dict[str, object]]:
    logger.debug("Building default Jira issue template")
    return {
        "fields": {
            "project": {"id": "22601"},
            "issuetype": {"id": "10120"},
            "summary": "",
            "description": "",
            "customfield_27602": [{"key": "TZ-24771"}],
            "customfield_35901": [{"key": "TZ-30904"}],
            "customfield_19700": [],
        }
    }


def process_open_event(payload: EventPayload) -> None:
    logger.debug("Start process_open_event for insight_id=%s", payload.insight_id)
    jira_data = get_jira_data(payload.insight_id)
    if not jira_data.is_actual:
        logger.info("Service %s is not actual, skip", payload.insight_id)
        return

    active_count = get_active_incident_count(payload.insight_id)
    if active_count > 0:
        counter = update_event_counter(payload.insight_id, delta=1)
        thread_id = get_last_room_id(payload.insight_id)
        if thread_id:
            logger.debug("Active incident counter incremented to=%s, send message to existing room", counter)
            send_to_ktalk_message(
                payload.message,
                payload.trigger_time,
                cnf.KTALK_ROOM_ID,
                payload.event,
                thread_id=thread_id,
            )
        return

    short_name = extract_shortname(payload.groups)
    recipients = jira_data.recipients
    trigger_start = convert_trigger_time(payload.trigger_time)

    jira_result = create_jira_incident(
        payload.insight_id,
        short_name,
        payload.trigger_name,
        issue_data=default_issue_template(),
        function_object_key=jira_data.function_object_key,
        jira_incident_type_key=jira_data.jira_incident_type_key,
    )
    jira_key = str(jira_result.get("key", "")).strip()

    discussion_id = create_discussion(
        recipients,
        jira_data.full_name,
        payload.trigger_name,
        payload.message,
        jira_key,
        payload.trigger_time,
    )

    create_internal_incident(
        insight_id=payload.insight_id,
        short_name=short_name,
        full_name=jira_data.full_name,
        trigger_name=payload.trigger_name,
        recipients=recipients,
        trigger_start_time=trigger_start,
        discussion_id=discussion_id,
        jira_issue_key=jira_key,
    )


def process_close_event(payload: EventPayload) -> None:
    logger.debug("Start process_close_event for insight_id=%s", payload.insight_id)
    thread_id = get_last_room_id(payload.insight_id)
    jira_issue_key = get_last_jira_issue_key(payload.insight_id)
    counter = update_event_counter(payload.insight_id, delta=-1)

    if thread_id:
        send_to_ktalk_message(
            payload.message,
            convert_trigger_time(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_id,
        )

    if counter > 0:
        return

    if jira_issue_key:
        is_active = validate_jira_incident_status(jira_issue_key)
        if not is_active:
            logger.info("Jira issue %s is already in a closed status", jira_issue_key)
    else:
        logger.warning("No Jira issue key found for insight_id=%s", payload.insight_id)

    if thread_id:
        mark_discussion_resolved(thread_id)
        send_to_ktalk_message(
            "Активные алерты, на которые был создан инцидент, отсутствуют",
            convert_trigger_time(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_id,
        )

    close_open_incidents(payload.insight_id)


def main() -> int:
    configure_logging()
    logger.debug("Script started")
    try:
        payload = parse_args()
        logger.info("received event=%s insightId=%s", payload.event, payload.insight_id)

        validate_insight_id(payload.insight_id)

        if payload.event == "1":
            process_open_event(payload)
        else:
            active_count = get_active_incident_count(payload.insight_id)
            if active_count == 0:
                logger.info(
                    "Ignore close-event for insight_id=%s: no active incident record in DB",
                    payload.insight_id,
                )
                return 0
            process_close_event(payload)

        logger.debug("Script finished successfully")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
