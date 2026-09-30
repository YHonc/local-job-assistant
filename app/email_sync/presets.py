from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EmailProviderType(str, Enum):
    QQ = "qq"
    NETEASE_163 = "163"
    CUSTOM = "custom"


@dataclass(frozen=True)
class IMAPSettings:
    host: str
    port: int
    use_ssl: bool
    username: str


PRESETS = {
    EmailProviderType.QQ: ("imap.qq.com", 993, True),
    EmailProviderType.NETEASE_163: ("imap.163.com", 993, True),
}


def resolve_imap_settings(
    provider_type: EmailProviderType,
    email_address: str,
    *,
    imap_host: str | None = None,
    imap_port: int | None = None,
    use_ssl: bool = True,
    username: str | None = None,
) -> IMAPSettings:
    username = (username or email_address).strip()
    if provider_type in PRESETS:
        host, port, preset_ssl = PRESETS[provider_type]
        return IMAPSettings(host, port, preset_ssl, username)

    host = (imap_host or "").strip()
    if not host or imap_port is None:
        raise ValueError("custom IMAP requires host and port")
    return IMAPSettings(host, imap_port, use_ssl, username)
