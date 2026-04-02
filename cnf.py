# -*- coding: utf-8 -*-


dbhost = "localhost"
dbname = "trmetrics"
dbuser = "user"
dbpassword = "password"
dbport = 5432
dboptions = ""

zabbotBAsicToken = "Bearer <token>"
JiraServURL = "https://hd.samoletgroup.ru/rest/assets/1.0/object/{}/attributes"
jiraCreateINCURL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue"
jiraIssueStatusURL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue/{}?fields=status"
jiraIssueBrowseURL = "https://hd-dev02.samoletgroup.ru/browse/{}"

ktalkBaseURL = "https://chat.ktalk.ru"
ktalkBotUser = "zabbix_bot"
ktalkJwtToken = "<ktalk_bot_jwt_token>"
ktalkRoomId = "!SWMeGogRrRLJIxnikt:matrix-9.ktalk.ru"
ktalkUserDomain = "matrix-9.ktalk.ru"
ktalkRoomMembersURL = "https://chat.ktalk.ru/_matrix/client/strangler/api/v1/bot/{}/get_room_members"


class query:
    selectActivInc = """
SELECT json_build_object(
    'id', id,
    'insight_id', TRIM(insight_id),
    'short_name', TRIM(short_name),
    'full_name', TRIM(full_name),
    'trigger_name', TRIM(trigger_name),
    'trigger_time', trigger_time,
    'time_start', time_start,
    'recipients', recipients,
    'rdiscussionid', TRIM(r_discussion_id),
    'event_balance', event_balance,
    'jira_issue_key', TRIM(jira_issue_key)
)
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

    getLastRoomID = """
SELECT r_discussion_id
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

    getLastJiraIssueKey = """
SELECT jira_issue_key
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

    closeInc = """
UPDATE trmetrics.availconf.conf
SET event_balance = 0
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

    countTZactiv = """
SELECT COUNT(*)
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

    createInc = """
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

    updateEventCounter = """
UPDATE trmetrics.availconf.conf
SET event_balance = GREATEST(COALESCE(event_balance, 0) + %(delta)s, 0)
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
RETURNING event_balance;
"""

    getMentionIdsByAdLogins = """
SELECT lower(ad_login) AS ad_login, ktalk_mention_id
FROM trmetrics.availconf.ad_ktalk_user_map
WHERE lower(ad_login) = ANY(%(logins)s)
  AND ad_active = TRUE
  AND ktalk_matched = TRUE
  AND COALESCE(ktalk_deactivated, FALSE) = FALSE
  AND ktalk_mention_id IS NOT NULL
"""


    upsertAdKtalkMapping = """
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


# Конфигурация для idcheker/ad mapping синхронизации
CONFIG = {
    # PostgreSQL
    "pg_dsn": "postgresql://user:password@localhost:5432/trmetrics",
    # Полное имя таблицы для чтения/записи соответствий
    "table_name": "trmetrics.availconf.ad_ktalk_user_map",
    # AD / LDAP
    "ad_host": "ad.example.local",
    "ad_user": "EXAMPLE\\svc_account",
    "ad_password": "change_me",
    "ad_base_dn": "DC=example,DC=local",
    # KTalk
    "ktalk_base_url": "https://chat.ktalk.ru/api/...",
    # Можно указывать как полный заголовок ("Bearer ..."),
    # так и только значение токена — префикс будет добавлен автоматически.
    "ktalk_bearer_token": "change_me",
    "ktalk_host": "chat.ktalk.ru",
    "ktalk_talk_host": "https://samoletgroup.ktalk.ru",
    # Runtime
    "verify_ssl": True,
    "request_timeout": 15,
    "log_file": "/tmp/idcheker.log",
}
