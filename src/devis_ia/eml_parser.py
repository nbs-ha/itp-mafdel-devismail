"""Lecture d'un .eml : en-têtes, corps nettoyé, pièces jointes exploitables."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email import message_from_binary_file
from pathlib import Path

# Pièces jointes envoyées à Claude (les images de signature sont ignorées)
ATTACHMENT_TYPES = {"application/pdf"}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

_URL_ONLY = re.compile(r"^\s*<?https?://\S+>?\s*$")
_BLANKS = re.compile(r"\n{3,}")


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes

    def as_content_block(self) -> dict:
        return {
            "type": "document",
            "title": self.filename,
            "source": {
                "type": "base64",
                "media_type": self.content_type,
                "data": base64.standard_b64encode(self.data).decode("ascii"),
            },
        }


@dataclass
class ParsedMail:
    subject: str
    sender: str
    to: str
    date: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    ignored_attachments: list[str] = field(default_factory=list)


def clean_body(text: str) -> str:
    """Retire les lignes qui ne sont qu'une URL (logos, liens de tracking) ; ne tronque rien d'autre."""
    lines = [ln.rstrip() for ln in text.splitlines() if not _URL_ONLY.match(ln)]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def parse_eml(path: str | Path) -> ParsedMail:
    with open(path, "rb") as fh:
        msg: EmailMessage = message_from_binary_file(fh, policy=policy.default)  # type: ignore[assignment]
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    mail = ParsedMail(
        subject=str(msg["subject"] or ""),
        sender=str(msg["from"] or ""),
        to=str(msg["to"] or ""),
        date=str(msg["date"] or ""),
        body=clean_body(body),
    )
    for att in msg.iter_attachments():
        name = att.get_filename() or "sans_nom"
        payload = att.get_payload(decode=True) or b""
        if att.get_content_type() in ATTACHMENT_TYPES and 0 < len(payload) <= MAX_ATTACHMENT_BYTES:
            mail.attachments.append(Attachment(name, att.get_content_type(), payload))
        else:
            mail.ignored_attachments.append(name)
    return mail


def to_user_content(mail: ParsedMail) -> list[dict]:
    """Blocs de contenu du message utilisateur : PDF d'abord, puis le texte du mail."""
    blocks: list[dict] = [a.as_content_block() for a in mail.attachments]
    header = (
        f"Objet : {mail.subject}\nDe : {mail.sender}\nÀ : {mail.to}\nDate : {mail.date}\n"
        f"Pièces jointes lues : {[a.filename for a in mail.attachments] or 'aucune'}\n"
        f"Pièces jointes ignorées : {mail.ignored_attachments or 'aucune'}\n\n"
    )
    blocks.append({"type": "text", "text": header + mail.body})
    return blocks
