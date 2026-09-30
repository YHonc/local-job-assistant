from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from app.database.models import EmailAccount

# IMAP SINCE 条件固定使用英文月份缩写；strftime(%b) 会跟随本机 locale，
# 在中文系统上生成的日期无法被服务器解析。
_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _imap_date(value: date) -> str:
    return f"{value.day:02d}-{_MONTH_NAMES[value.month - 1]}-{value.year:04d}"


class EmailProviderError(RuntimeError):
    """Base error for a redacted IMAP failure."""


class EmailAuthenticationError(EmailProviderError):
    pass


class EmailNetworkError(EmailProviderError):
    pass


@dataclass(frozen=True)
class EmailEnvelope:
    uid: int
    message_id: str | None
    sender: str
    subject: str
    received_at: datetime | None
    body_text: str | None


@dataclass(frozen=True)
class EmailFetchBatch:
    uid_validity: int | None
    envelopes: list[EmailEnvelope]


class EmailProviderAdapter(Protocol):
    def test_connection(self) -> None: ...

    def fetch_since(self, last_uid: int) -> EmailFetchBatch: ...


class ImapToolsProvider:
    def __init__(
        self,
        account: EmailAccount,
        authorization_code: str,
        mailbox_factory: Any | None = None,
    ) -> None:
        self.account = account
        self.authorization_code = authorization_code
        self.mailbox_factory = mailbox_factory

    def _mailbox(self):
        if self.mailbox_factory is not None:
            return self.mailbox_factory(self.account)
        try:
            from imap_tools import MailBox, MailBoxUnencrypted
        except Exception as exc:
            raise EmailProviderError("IMAP backend unavailable") from exc
        mailbox_type = MailBox if self.account.use_ssl else MailBoxUnencrypted
        return mailbox_type(self.account.imap_host, port=self.account.imap_port)

    def _login(self):
        try:
            return self._mailbox().login(
                self.account.username,
                self.authorization_code,
                initial_folder="INBOX",
            )
        except Exception as exc:
            name = exc.__class__.__name__.casefold()
            if any(token in name for token in ("auth", "login", "credential")):
                raise EmailAuthenticationError("IMAP authentication failed") from exc
            raise EmailNetworkError("IMAP connection failed") from exc

    def test_connection(self) -> None:
        mailbox = self._login()
        try:
            mailbox.folder.get()
        except Exception as exc:
            raise EmailNetworkError("IMAP connection test failed") from exc
        finally:
            try:
                mailbox.logout()
            except Exception:
                pass

    def fetch_since(self, last_uid: int, since_date: date | None = None) -> EmailFetchBatch:
        """Fetch messages after ``last_uid``; ``since_date`` bounds the backfill
        window by IMAP internal date (used for first sync / UIDVALIDITY resets)."""
        mailbox = self._login()
        try:
            folder_name = mailbox.folder.get() or "INBOX"
            folder_status = mailbox.folder.status(folder_name)
            raw_uid_validity = folder_status.get("UIDVALIDITY")
            uid_validity = int(raw_uid_validity) if raw_uid_validity is not None else None
            stored_uid_validity = getattr(self.account, "uid_validity", None)
            cursor_is_valid = (
                uid_validity is not None
                and stored_uid_validity is not None
                and uid_validity == stored_uid_validity
            )
            start = max(1, last_uid + 1) if cursor_is_valid else 1
            criteria = f"UID {start}:*"
            if since_date is not None:
                criteria = f"(SINCE {_imap_date(since_date)} UID {start}:*)"
            uid_values = [
                uid for uid in mailbox.uids(criteria) if int(uid) >= start
            ]
            envelopes: list[EmailEnvelope] = []
            for message in mailbox.fetch(
                uid_list=uid_values,
                mark_seen=False,
                bulk=50,
            ):
                headers = getattr(message, "headers", {}) or {}
                message_id = getattr(message, "message_id", None)
                if not message_id:
                    header_value = headers.get("message-id") or headers.get("Message-ID")
                    if isinstance(header_value, (list, tuple)):
                        header_value = header_value[0] if header_value else None
                    message_id = header_value
                envelopes.append(
                    EmailEnvelope(
                        uid=int(message.uid),
                        message_id=str(message_id)[:998] if message_id else None,
                        sender=str(message.from_ or "")[:500],
                        subject=str(message.subject or "")[:1000],
                        received_at=message.date,
                        body_text=(message.text or "")[:100_000] or None,
                    )
                )
            return EmailFetchBatch(uid_validity=uid_validity, envelopes=envelopes)
        except EmailProviderError:
            raise
        except Exception as exc:
            raise EmailNetworkError("IMAP incremental fetch failed") from exc
        finally:
            try:
                mailbox.logout()
            except Exception:
                pass
