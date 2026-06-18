import threading
import logging
from typing import Iterable, List, Optional, Tuple, Union

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _send(email_message: EmailMultiAlternatives, fail_silently: bool) -> None:
    try:
        email_message.send(fail_silently=fail_silently)
    except Exception:
        # Always log the exception; caller may set fail_silently to control raising
        logger.exception('Email send failed')


def send_email(*args,
               fail_silently: bool = False,
               async_send: bool = True,
               attachments: Optional[List[Tuple[str, bytes, Optional[str]]]] = None,
               **kwargs) -> None:
    """Send email helper with two supported call styles:

    1) Legacy/full: send_email(subject, body, to, from_email=None, html=None, ...)
    2) Simple (convenience): send_email(to_email, subject, html_message)

    - `to_email` may be a single address or an iterable of addresses.
    - `attachments` is a list of `(filename, content, mimetype)` tuples.
    - By default `fail_silently` is False (errors are logged).
    """
    # Normalize arguments to canonical form: subject, body, to_list, from_email, html
    subject = None
    body = ''
    html = None
    from_email = kwargs.get('from_email') or getattr(settings, 'DEFAULT_FROM_EMAIL', None)

    # Detect simple form: (to, subject, html)
    if len(args) >= 3 and (isinstance(args[0], (list, tuple)) or (isinstance(args[0], str) and '@' in args[0])):
        raw_to = args[0]
        subject = args[1]
        html = args[2]
        if isinstance(raw_to, str):
            to_list = [raw_to]
        else:
            to_list = list(raw_to)
        body = kwargs.get('body') or (strip_tags(html) if html else '')
    else:
        # Legacy pattern: subject, body, to
        if len(args) >= 1:
            subject = args[0]
        if len(args) >= 2:
            body = args[1]
        if len(args) >= 3:
            raw_to = args[2]
        else:
            raw_to = kwargs.get('to') or []
        if isinstance(raw_to, str):
            to_list = [raw_to]
        else:
            to_list = list(raw_to)
        html = kwargs.get('html')

    if not subject or not to_list:
        logger.error('send_email called with insufficient arguments')
        return

    try:
        msg = EmailMultiAlternatives(subject, body, from_email, to_list)
        if html:
            msg.attach_alternative(html, 'text/html')
        if attachments:
            for att in attachments:
                try:
                    if isinstance(att, (list, tuple)) and len(att) >= 2:
                        filename = att[0]
                        content = att[1]
                        mimetype = att[2] if len(att) > 2 else None
                        msg.attach(filename, content, mimetype)
                    else:
                        msg.attach(att)
                except Exception:
                    logger.exception('Failed to attach file to email')

        if async_send:
            t = threading.Thread(target=_send, args=(msg, fail_silently))
            t.daemon = True
            t.start()
        else:
            _send(msg, fail_silently)
    except Exception:
        logger.exception('Failed to prepare/send email')
