"""
mailer.py - order emails (confirmed, shipped, delivered, cancelled, return updates).

Emails are optional. If SMTP_HOST is empty nothing is sent and nothing breaks.
A failed email NEVER stops an order: the error is only written to the log.

Works with any SMTP service (Gmail app password, Brevo, Resend SMTP, Mailgun ...).
Triggers inside MySQL cannot send email, so the website sends it right after it
has changed an order.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from flask import render_template

from . import config

log = logging.getLogger("greencart.mail")


def send(to, subject, template, **context):
    """Send one email. Returns True when it was handed to the mail server."""
    if not config.EMAILS_READY or not to:
        return False
    try:
        html = render_template("emails/" + template, store=config.STORE_NAME, subject=subject, **context)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "%s <%s>" % (config.STORE_NAME, config.MAIL_FROM)
        message["To"] = to
        message.set_content("Open this email in an HTML-capable mail app to see the details.\n\n" + subject)
        message.add_alternative(html, subtype="html")

        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=8,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=8)
            server.starttls(context=ssl.create_default_context())
        with server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception as e:                      # an email problem must never break an order
        log.warning("Could not send '%s' to %s: %s", subject, to, e)
        return False
