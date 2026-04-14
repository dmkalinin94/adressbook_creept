# -*- coding: utf-8 -*-

DB_HOST = "localhost"
DB_NAME = "trmetrics"
DB_USER = "user"
DB_PASSWORD = "password"
DB_PORT = 5432
DB_OPTIONS = ""

JIRA_TOKEN = "Bearer <token>"
JIRA_SERVICE_URL = "https://hd.samoletgroup.ru/rest/assets/1.0/object/{}/attributes"
JIRA_CREATE_INC_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue"
JIRA_ISSUE_STATUS_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue/{}?fields=status"
JIRA_ISSUE_BROWSE_URL = "https://hd-dev02.samoletgroup.ru/browse/{}"

KTALK_BASE_URL = "https://chat.ktalk.ru"
KTALK_BOT_USER = "zabbix_bot"
KTALK_JWT_TOKEN = "<ktalk_bot_jwt_token>"
KTALK_ROOM_ID = "!SWMeGogRrRLJIxnikt:matrix-9.ktalk.ru"
KTALK_USER_DOMAIN = "matrix-9.ktalk.ru"
KTALK_DRY_RUN_MENTIONS_INVITES = False
KTALK_BEARER_SEARCH_URL = "https://chat.ktalk.ru/api/..."
KTALK_BEARER_TOKEN = "change_me"
KTALK_HOST = "chat.ktalk.ru"
KTALK_TALK_HOST = "https://samoletgroup.ktalk.ru"

AD_HOST = "ad.example.local"
AD_USER = "EXAMPLE\\svc_account"
AD_PASSWORD = "change_me"
AD_BASE_DN = "DC=example,DC=local"

REQUEST_TIMEOUT = 30
VERIFY_SSL = False
LOG_FILE = "/tmp/autoalerter.log"

FLAP_NIGHT_START_HOUR = 23
FLAP_NIGHT_END_HOUR = 7
FLAP_REOPEN_HOURS = 6
TIMEZONE_NAME = "Europe/Moscow"

SQL_GET_LAST_ROOM_ID = """
SELECT r_discussion_id
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

SQL_GET_LAST_JIRA_ISSUE_KEY = """
SELECT jira_issue_key
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

SQL_CLOSE_INCIDENTS = """
UPDATE trmetrics.availconf.conf
SET event_balance = 0
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

SQL_COUNT_ACTIVE_INCIDENTS = """
SELECT COUNT(*)
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

SQL_CREATE_INCIDENT = """
INSERT INTO trmetrics.availconf.conf (
    insight_id,
    short_name,
    full_name,
    trigger_name,
    trigger_time,
    time_start,
    recipients,
    r_discussion_id,
    event_balance,
    jira_issue_key
)
VALUES (
    %(insight_id)s,
    %(short_name)s,
    %(full_name)s,
    %(trigger_name)s,
    %(trigger_time)s,
    CURRENT_TIMESTAMP,
    %(recipients)s,
    %(rdiscussionid)s,
    1,
    %(jira_issue_key)s
);
"""

SQL_UPDATE_EVENT_COUNTER = """
UPDATE trmetrics.availconf.conf
SET event_balance = GREATEST(COALESCE(event_balance, 0) + %(delta)s, 0)
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
RETURNING event_balance;
"""

SQL_GET_MENTION_IDS_BY_AD_LOGINS = """
SELECT
    lower(ad_login) AS ad_login,
    ktalk_mention_id,
    COALESCE(NULLIF(TRIM(ad_first_name || ' ' || ad_last_name), ''), NULLIF(TRIM(ad_display_name), '')) AS full_name
FROM trmetrics.availconf.ad_ktalk_user_map
WHERE lower(ad_login) = ANY(%(logins)s)
  AND ad_active = TRUE
  AND ktalk_matched = TRUE
  AND COALESCE(ktalk_deactivated, FALSE) = FALSE
  AND ktalk_mention_id IS NOT NULL
"""

SQL_UPSERT_AD_KTALK_MAPPING = """
INSERT INTO trmetrics.availconf.ad_ktalk_user_map (
    ad_login,
    ad_first_name,
    ad_last_name,
    ad_display_name,
    ad_title,
    ad_active,
    ktalk_mention_id,
    ktalk_display_name,
    ktalk_post,
    ktalk_matched,
    ktalk_deactivated,
    last_sync_at,
    updated_at
)
VALUES (
    %(ad_login)s,
    %(ad_first_name)s,
    %(ad_last_name)s,
    %(ad_display_name)s,
    %(ad_title)s,
    %(ad_active)s,
    %(ktalk_mention_id)s,
    %(ktalk_display_name)s,
    %(ktalk_post)s,
    %(ktalk_matched)s,
    %(ktalk_deactivated)s,
    %(last_sync_at)s,
    %(updated_at)s
)
ON CONFLICT (ad_login)
DO UPDATE SET
    ad_first_name = EXCLUDED.ad_first_name,
    ad_last_name = EXCLUDED.ad_last_name,
    ad_display_name = EXCLUDED.ad_display_name,
    ad_title = EXCLUDED.ad_title,
    ad_active = EXCLUDED.ad_active,
    ktalk_mention_id = EXCLUDED.ktalk_mention_id,
    ktalk_display_name = EXCLUDED.ktalk_display_name,
    ktalk_post = EXCLUDED.ktalk_post,
    ktalk_matched = EXCLUDED.ktalk_matched,
    ktalk_deactivated = EXCLUDED.ktalk_deactivated,
    last_sync_at = EXCLUDED.last_sync_at,
    updated_at = EXCLUDED.updated_at;
"""
