#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jira integration helpers for autoalerter."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import requests

import cnf

logger = logging.getLogger("autoalerter")


@dataclass(slots=True)
class JiraServiceData:
    full_name: str
    is_actual: bool
    function_object_key: str | None
    jira_incident_type_key: str | None
    user_group_id: int | None
    recipients: list[str]


def _decode_actual_state(value: str) -> bool:
    is_actual = value == "Актуально"
    logger.debug("Decoded actual state raw=%r parsed=%s", value, is_actual)
    return is_actual


def _get_json(
    url: str,
    token: str,
    auth: tuple[str, str] | None = None,
    content_type: str = "application/json",
) -> Any:
    headers = {
        "Content-Type": content_type,
        "Authorization": token,
    }
    logger.debug("HTTP GET JSON request: url=%s auth=%s token=%s", url, bool(auth), bool(token))
    response = requests.get(url, headers=headers, auth=auth, verify=False, timeout=30)
    logger.debug("HTTP GET response status=%s url=%s", response.status_code, url)
    response.raise_for_status()
    parsed = response.json()
    logger.debug("HTTP GET JSON parsed: type=%s", type(parsed).__name__)
    return parsed


def _extract_recipients_from_group_attributes(payload: list[dict[str, Any]]) -> list[str]:
    recipients: list[str] = []
    mandatory_recipients = ["dm.kalinin", "d.boyarchuk", "t.sukhorukikh", "dk.korolev"]

    for item in payload:
        attr_id = item.get("objectTypeAttributeId")
        if attr_id not in {2543, 2542}:
            continue
        for value in item.get("objectAttributeValues", []):
            name = value.get("user", {}).get("name")
            if isinstance(name, str) and name and name not in recipients:
                recipients.append(name)

    for user in mandatory_recipients:
        if user not in recipients:
            recipients.append(user)

    logger.debug("Recipients extracted from group attributes count=%s recipients=%s", len(recipients), recipients)
    return recipients


def get_jira_data(insight_id: str) -> JiraServiceData:
    logger.debug("Loading Jira data for insight_id=%s", insight_id)
    service_url = cnf.JiraServURL.format(insight_id)
    payload = _get_json(
        service_url,
        token=cnf.zabbotBAsicToken,
        content_type="application/json;charset=UTF-8",
    )
    logger.debug("Jira payload entries=%s", len(payload) if isinstance(payload, list) else "n/a")

    full_name = ""
    is_actual = False
    function_refs: list[dict[str, Any]] = []
    jira_incident_type_key: str | None = None
    user_group_id: int | None = None

    for item in payload:
        attr_id = item.get("objectTypeAttributeId")
        values = item.get("objectAttributeValues", [])
        if not values:
            continue
        if attr_id == 63:
            full_name = values[0].get("value", "")
            logger.debug("Jira attr 63 found full_name=%r", full_name)
        elif attr_id == 126:
            is_actual = _decode_actual_state(values[0].get("value", ""))
            logger.debug("Jira attr 126 found is_actual=%s", is_actual)
        elif attr_id == 2066:
            function_refs = values
            logger.debug("Jira attr 2066 found refs_count=%s", len(function_refs))
        elif attr_id == 2551:
            for value in values:
                raw_group_id = value.get("referencedObject", {}).get("id")
                if raw_group_id is not None:
                    user_group_id = int(raw_group_id)
                    logger.debug("Jira attr 2551 found user_group_id=%s", user_group_id)
                    break
        elif attr_id == 2413:
            for value in values:
                object_key = value.get("referencedObject", {}).get("objectKey")
                if object_key:
                    jira_incident_type_key = str(object_key)
                    logger.debug("Jira attr 2413 found jira_incident_type_key=%s", jira_incident_type_key)
                    break

    preferred_labels = (
        "Недоступность сервиса",
        "Прочее",
    )
    function_key: str | None = None
    for label in preferred_labels:
        logger.debug("Trying function object label=%r", label)
        for ref in function_refs:
            obj = ref.get("referencedObject", {})
            if obj.get("label") == label:
                function_key = obj.get("objectKey")
                logger.debug("Matched function label=%r key=%r", label, function_key)
                break
        if function_key:
            break

    if not function_key and function_refs:
        function_key = function_refs[0].get("referencedObject", {}).get("objectKey")
        logger.debug("No preferred label found, fallback function key=%r", function_key)

    recipients: list[str] = []
    if user_group_id is not None:
        group_url = f"https://hd.samoletgroup.ru/rest/assets/1.0/object/{user_group_id}/attributes"
        group_payload = _get_json(
            group_url,
            token=cnf.zabbotBAsicToken,
            content_type="application/json",
        )
        recipients = _extract_recipients_from_group_attributes(group_payload)
    else:
        logger.debug("Jira attr 2551 user_group_id not found for insight_id=%s", insight_id)

    result = JiraServiceData(
        full_name=full_name,
        is_actual=is_actual,
        function_object_key=function_key,
        jira_incident_type_key=jira_incident_type_key,
        user_group_id=user_group_id,
        recipients=recipients,
    )
    logger.debug("Jira data prepared: %s", result)
    return result


def create_jira_incident(
    insight_id: str,
    short_name: str,
    trigger_name: str,
    issue_data: dict[str, Any],
    function_object_key: str | None,
    jira_incident_type_key: str | None,
) -> dict[str, Any]:
    logger.debug("Preparing Jira incident payload for insight_id=%s", insight_id)
    issue_data["fields"]["summary"] = f"Автоматический инцидент Zabbix: {short_name} {trigger_name}"
    issue_data["fields"]["description"] = trigger_name
    issue_data["fields"]["customfield_19700"] = [{"key": insight_id}]

    if jira_incident_type_key:
        issue_data["fields"]["customfield_35901"] = [{"key": jira_incident_type_key}]
        logger.debug("Set customfield_35901 from Jira attr 2413 key=%s", jira_incident_type_key)

    if function_object_key:
        issue_data["fields"]["customfield_23400"] = [{"key": function_object_key}]
        logger.debug("Added customfield_23400 from Jira service data key=%s", function_object_key)
    else:
        issue_data["fields"]["customfield_23400"] = []
        logger.debug("No Jira function object key -> set customfield_23400 to empty list")

    logger.debug("Jira payload data=%s", json.dumps(issue_data, ensure_ascii=False))
    logger.debug("Creating Jira incident via POST %s", cnf.jiraCreateINCURL)
    response = requests.post(
        cnf.jiraCreateINCURL,
        json=issue_data,
        verify=False,
        headers={
            "Authorization": cnf.zabbotBAsicToken,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    logger.debug("Jira create response status=%s", response.status_code)
    if response.status_code >= 400:
        logger.error("Jira create failed status=%s body=%s", response.status_code, response.text)
    response.raise_for_status()
    result = response.json()
    logger.debug("Jira incident created response keys=%s", list(result.keys()))
    return result


def validate_jira_incident_status(jira_issue_key: str) -> bool:
    logger.debug("Validating Jira incident status for issue=%s", jira_issue_key)
    status_url = cnf.jiraIssueStatusURL.format(jira_issue_key)
    payload = _get_json(
        status_url,
        token=cnf.zabbotBAsicToken,
        content_type="application/json",
    )

    status_name = str(payload.get("fields", {}).get("status", {}).get("name", "")).strip()
    if not status_name:
        raise ValueError(f"Cannot determine Jira status for issue {jira_issue_key}")

    closed_statuses = {"Решен", "Решён", "Закрыт", "Отменен", "Отменён"}
    is_active = status_name not in closed_statuses
    logger.debug(
        "Validated Jira incident status issue=%s status=%r is_active=%s",
        jira_issue_key,
        status_name,
        is_active,
    )
    return is_active
