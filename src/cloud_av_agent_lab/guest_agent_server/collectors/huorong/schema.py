from __future__ import annotations

PRODUCT_ID = "huorong"
PRODUCT_LOG_SOURCE = "product_log"

LOG_FILENAMES = ("log.db", "log.db-shm", "log.db-wal")
TABLE_NAME = "HrLogV3_60"
TABLE_NAME_PREFIX = "HrLogV3_"

HASH_FIELDS = ("sha256", "sha1", "md5")
PATH_FIELDS = (
    "procname",
    "cmdline",
    "p_procname",
    "p_cmdline",
    "pathname",
    "res_path",
    "targetname",
    "targetcmdline",
    "res_cmd",
)
PID_FIELDS = ("xpid", "p_xpid")

DETECTION_FIELDS = (
    "recname",
    "description",
    "risk",
    "action",
    "treatment",
    "result",
)
BLOCK_KEYWORDS = (
    "block",
    "blocked",
    "deny",
    "denied",
    "阻止",
    "拦截",
)
QUARANTINE_KEYWORDS = (
    "quarantine",
    "quarantined",
    "隔离",
)
DELETE_KEYWORDS = (
    "delete",
    "deleted",
    "remove",
    "removed",
    "删除",
    "清除",
)
