"""Current progress vocabulary and lossless compatibility with older records."""

CURRENT_STATUSES = ("待投递", "已投递", "待面试", "1面", "2面", "3面", "HR面", "拒绝")
STATUS_ALIASES = {"收藏": "待投递", "一面": "1面", "二面": "2面"}
LEGACY_REVIEW_STATUSES = ("测评", "笔试", "Offer", "放弃")


def current_status(value: str | None) -> str:
    return STATUS_ALIASES.get(value, value) if value else "待投递"
