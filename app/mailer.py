"""Kirim email lewat SMTP (opsional). Tanpa TRENDWATCH_SMTP_HOST, email tidak dikirim (``configured()`` False)."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get("TRENDWATCH_SMTP_HOST"))


def send(to: str, subject: str, body: str) -> None:
    host = os.environ["TRENDWATCH_SMTP_HOST"]
    port = int(os.environ.get("TRENDWATCH_SMTP_PORT", "587"))
    user = os.environ.get("TRENDWATCH_SMTP_USER", "")
    sender = os.environ.get("TRENDWATCH_SMTP_FROM") or user or "noreply@localhost"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if os.environ.get("TRENDWATCH_SMTP_SSL", "").lower() in ("1", "true", "yes") or port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
    with server:
        if not isinstance(server, smtplib.SMTP_SSL):
            server.starttls(context=ctx)
        if user:
            server.login(user, os.environ.get("TRENDWATCH_SMTP_PASSWORD", ""))
        server.send_message(msg)
