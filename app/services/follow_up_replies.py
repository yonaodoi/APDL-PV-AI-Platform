"""Reading reporters' replies to follow-up emails.

The inbox of the account the follow-up emails are sent from is checked,
read-only, every few minutes:

* A reply is matched to its case by the case number in the subject or text
  (our subject line always carries it), or by the sender being the reporter
  the request was emailed to.
* The reply text, and any PDF / Word / image the reporter attached, are
  saved on the case as "Follow-up response" attachments. The AI reads them
  and lists suggested updates; nothing in the case changes until someone
  applies them.
* Reminders stop while a reply is waiting to be handled.
* Everyone on the PV team (the roles chosen in Sign-off settings, the case
  owner and any extra addresses) is emailed that a reply arrived.
* A reply that cannot be matched with confidence waits on the follow-up
  page to be linked to the right case; its attachments are re-read from the
  mailbox at that point.

Messages are never marked as read, moved or deleted in the mailbox.
"""

import email
import imaplib
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from uuid import uuid4

from flask import current_app

from app.db import get_db, query_all, query_one, transaction
from app.services.company_profile import case_number_pattern, platform

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
SAVED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
FETCH_BATCH = 200

STATUS_NEW = "New"
STATUS_HANDLED = "Handled"
STATUS_UNMATCHED = "Unmatched"
STATUS_IGNORED = "Ignored"


# --------------------------------------------------------------------------
# Reading a message
# --------------------------------------------------------------------------

def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def _html_to_text(html):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"[ \t]+", " ", text)


def parse_message(raw):
    """Turn raw RFC 822 bytes into the parts we use."""
    message = email.message_from_bytes(raw)
    name, address = parseaddr(_decode(message.get("From")))
    try:
        received = parsedate_to_datetime(message.get("Date"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except Exception:
        received = datetime.now(timezone.utc)

    plain, html, attachments = [], [], []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = _decode(part.get_filename())
        content_type = part.get_content_type()
        if filename or "attachment" in disposition:
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": filename or "attachment",
                "content_type": content_type,
                "data": payload,
            })
            continue
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (plain if content_type == "text/plain" else html).append(text)

    body = "\n".join(plain) if plain else _html_to_text("\n".join(html))
    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = f"<no-id-{address}-{received.isoformat()}>"
    return {
        "message_id": message_id,
        "from_email": (address or "").strip().lower(),
        "from_name": name or None,
        "to": [a.lower() for _, a in getaddresses(message.get_all("To", []))],
        "subject": _decode(message.get("Subject")),
        "received_at": received,
        "text": body.replace("\r\n", "\n"),
        "attachments": attachments,
        "auto_reply": bool(
            message.get("Auto-Submitted", "no").lower() not in ("", "no")
            or message.get("X-Autoreply") or message.get("X-Autorespond")
        ),
    }


QUOTE_HEADER = re.compile(
    r"^\s*(On .{4,200} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+|Le .+ a écrit\s*:)\s*$",
    re.IGNORECASE,
)


def reply_text(text):
    """The reporter's own words: quoted lines and our original email removed.

    Answers typed between quoted lines (inline replies) are kept.
    """
    kept = []
    for line in (text or "").splitlines():
        if QUOTE_HEADER.match(line) and kept and any(l.strip() for l in kept):
            # Everything after "On ... wrote:" is our own email, unless the
            # reporter answered inline (then the quoted lines carry ">").
            remainder = text.splitlines()[len(kept) + 1:]
            inline = [l for l in remainder if l.strip() and not l.lstrip().startswith(">")]
            quoted = [l for l in remainder if l.lstrip().startswith(">")]
            if quoted and inline:
                kept.extend(inline)
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    result = "\n".join(kept).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result or (text or "").strip()


def find_case_numbers(*texts):
    found = []
    for text in texts:
        for match in case_number_pattern().findall(text or ""):
            number = match.upper()
            if number not in found:
                found.append(number)
    return found


# --------------------------------------------------------------------------
# Matching a reply to its case
# --------------------------------------------------------------------------

def match_reply(parsed, cases_by_number, requests_by_email):
    """Decide which case a reply belongs to.

    cases_by_number: {case_number: {"case_id", "reporter_email", "recipients"}}
    requests_by_email: {email: [{"case_id", "sent_at"}, ...] newest first}

    Returns (case_id or None, status, note). Returns (None, None, None) for
    mail that has nothing to do with follow-up requests.
    """
    sender = parsed["from_email"]
    numbers = find_case_numbers(parsed["subject"], parsed["text"][:4000])
    for number in numbers:
        case = cases_by_number.get(number)
        if not case:
            continue
        known = {e for e in [case.get("reporter_email") or ""] + list(case.get("recipients") or ()) if e}
        if sender in {e.strip().lower() for e in known}:
            return case["case_id"], STATUS_NEW, f"Matched by case number {number} and the reporter's address."
        return (
            None,
            STATUS_UNMATCHED,
            f"Mentions {number} but was sent from {sender}, not the reporter's address. Check and link it if it belongs to the case.",
        )

    requests = requests_by_email.get(sender) or []
    case_ids = []
    for item in requests:
        if item["case_id"] not in case_ids:
            case_ids.append(item["case_id"])
    if len(case_ids) == 1:
        return case_ids[0], STATUS_NEW, "Matched by the reporter's address."
    if len(case_ids) > 1:
        return (
            None,
            STATUS_UNMATCHED,
            f"{sender} has follow-up requests on {len(case_ids)} cases and the reply does not say which. Link it to the right case.",
        )
    return None, None, None


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def _rollback():
    try:
        get_db().rollback()
    except Exception:
        pass


def reply_state(case_ids=None):
    """{case_id: {"waiting": n, "last_reply": dt, "last_handled": dt}}.

    Empty if replies cannot be read (for example before the database update).
    """
    try:
        rows = query_all(
            """
            SELECT case_id,
                   COUNT(*) FILTER (WHERE status = 'New') AS waiting,
                   MAX(received_at) AS last_reply,
                   MAX(handled_at) FILTER (WHERE status = 'Handled') AS last_handled
            FROM pv.follow_up_replies
            WHERE case_id IS NOT NULL
              AND status IN ('New', 'Handled')
              AND (%s::bigint[] IS NULL OR case_id = ANY(%s::bigint[]))
            GROUP BY case_id
            """,
            (case_ids, case_ids),
        )
    except Exception:
        _rollback()
        return {}
    return {
        row["case_id"]: {
            "waiting": row["waiting"] or 0,
            "last_reply": row["last_reply"],
            "last_handled": row["last_handled"],
        }
        for row in rows
    }


def replies_for_cases(case_ids):
    """Replies per case, newest first, for the follow-up page."""
    if not case_ids:
        return {}
    try:
        rows = query_all(
            """
            SELECT r.*, u.full_name AS handled_by_name
            FROM pv.follow_up_replies AS r
            LEFT JOIN pv.users AS u ON u.user_id = r.handled_by
            WHERE r.case_id = ANY(%s) AND r.status IN ('New', 'Handled')
            ORDER BY r.received_at DESC
            """,
            (list(case_ids),),
        )
    except Exception:
        _rollback()
        return {}
    grouped = {}
    for row in rows:
        grouped.setdefault(row["case_id"], []).append(dict(row))
    return grouped


def unmatched_replies():
    try:
        return [dict(r) for r in query_all(
            """
            SELECT * FROM pv.follow_up_replies
            WHERE status = 'Unmatched'
            ORDER BY received_at DESC
            """
        )]
    except Exception:
        _rollback()
        return []


def _lookups(since):
    """Cases and request addresses needed to match replies."""
    deliveries = query_all(
        """
        SELECT d.case_id, LOWER(TRIM(d.recipient_email)) AS email, MAX(d.sent_at) AS sent_at
        FROM pv.case_follow_up_email_deliveries AS d
        WHERE d.status = 'Sent' AND d.sent_at >= %s
        GROUP BY d.case_id, LOWER(TRIM(d.recipient_email))
        ORDER BY sent_at DESC
        """,
        (since,),
    )
    requests_by_email = {}
    recipients = {}
    for row in deliveries:
        requests_by_email.setdefault(row["email"], []).append(
            {"case_id": row["case_id"], "sent_at": row["sent_at"]}
        )
        recipients.setdefault(row["case_id"], set()).add(row["email"])
    cases = query_all(
        """
        SELECT case_id, UPPER(case_number) AS case_number, LOWER(TRIM(reporter_email)) AS reporter_email
        FROM pv.safety_cases
        """
    )
    cases_by_number = {
        row["case_number"]: {
            "case_id": row["case_id"],
            "reporter_email": row["reporter_email"],
            "recipients": recipients.get(row["case_id"], set()),
        }
        for row in cases
    }
    return cases_by_number, requests_by_email


def _seen_message_ids(message_ids):
    if not message_ids:
        return set()
    rows = query_all(
        "SELECT message_id FROM pv.follow_up_replies WHERE message_id = ANY(%s)",
        (list(message_ids),),
    )
    return {row["message_id"] for row in rows}


def _case_owner(case_id):
    row = query_one(
        "SELECT case_number, created_by FROM pv.safety_cases WHERE case_id = %s",
        (case_id,),
    )
    if row and row.get("created_by"):
        return row
    admin = query_one(
        """
        SELECT u.user_id FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE r.role_name = 'System Administrator' ORDER BY u.user_id LIMIT 1
        """
    )
    return {"case_number": (row or {}).get("case_number"), "created_by": admin["user_id"] if admin else None}


def _safe_filename(name, fallback):
    from werkzeug.utils import secure_filename

    cleaned = secure_filename(name or "") or fallback
    return cleaned[:180]


def _save_attachment(app, case_id, owner_id, filename, content_type, data, document_type):
    """Store a file on the case like an upload. Returns the attachment row."""
    extension = Path(filename).suffix.lower()
    stored = f"{uuid4().hex}{extension}"
    folder = Path(app.config["UPLOAD_ROOT"]) / "attachments" / "case" / str(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / stored
    path.write_bytes(data)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.record_attachments (
                record_type, record_id, original_filename, stored_filename,
                content_type, file_size_bytes, uploaded_by, document_type
            )
            VALUES ('case', %s, %s, %s, %s, %s, %s, %s)
            RETURNING attachment_id
            """,
            (case_id, filename, stored, content_type, len(data), owner_id, document_type),
        )
        attachment_id = cursor.fetchone()["attachment_id"]
    return {
        "attachment_id": attachment_id,
        "original_filename": filename,
        "content_type": content_type,
        "document_type": document_type,
        "path": path,
    }


def file_reply_on_case(app, reply_id, case_id, parsed=None, actor_user_id=None):
    """Save a matched reply's text and attachments on the case, start the AI
    reading, and tell the case owner. Returns the attachment ids."""
    from app.services.case_documents import process_case_attachment

    reply = query_one("SELECT * FROM pv.follow_up_replies WHERE reply_id = %s", (reply_id,))
    owner = _case_owner(case_id)
    owner_id = actor_user_id or owner.get("created_by")
    stamp = reply["received_at"].astimezone().strftime("%Y-%m-%d_%H%M")
    text = reply.get("reply_text") or ""
    header = (
        f"Reporter's email reply to the follow-up request for {owner.get('case_number')}\n"
        f"From: {reply.get('from_name') or ''} <{reply['from_email']}>\n"
        f"Received: {reply['received_at'].astimezone():%d %b %Y %H:%M}\n"
        f"Subject: {reply.get('subject') or ''}\n\n"
    )
    saved = [
        _save_attachment(
            app, case_id, owner_id, f"reporter_reply_{stamp}.txt", "text/plain",
            (header + text).encode("utf-8"), "follow_up",
        )
    ]
    skipped = []
    for index, item in enumerate((parsed or {}).get("attachments") or [], start=1):
        name = _safe_filename(item["filename"], f"reply_attachment_{index}")
        if Path(name).suffix.lower() not in SAVED_EXTENSIONS:
            skipped.append(f"{item['filename']} (file type not accepted)")
            continue
        if len(item["data"]) > MAX_ATTACHMENT_BYTES or not item["data"]:
            skipped.append(f"{item['filename']} (empty or larger than 15 MB)")
            continue
        saved.append(_save_attachment(
            app, case_id, owner_id, name, item["content_type"], item["data"], "follow_up",
        ))

    ids = [a["attachment_id"] for a in saved]
    with transaction() as cursor:
        cursor.execute(
            "UPDATE pv.follow_up_replies SET case_id = %s, status = 'New', attachment_ids = %s WHERE reply_id = %s",
            (case_id, ids, reply_id),
        )
        details = (
            f"Reporter replied by email from {reply['from_email']} "
            f"({len(saved) - 1} attachment(s) saved). The AI is reading it for suggested updates."
        )
        if skipped:
            details += " Not saved: " + "; ".join(skipped) + "."
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (case_id, "Follow-up reply received", details, owner_id),
        )

    for attachment in saved:
        try:
            process_case_attachment(attachment, case_id, attachment["path"], owner_id)
        except Exception:
            app.logger.exception("Could not start reading reply attachment %s", attachment["attachment_id"])
            _rollback()
    _notify_owner(app, case_id, reply, len(saved) - 1)
    return ids


def team_recipients(case_id, settings=None):
    """Everyone on the PV team who should hear about a reply, as
    [(name, email)]: the case owner, every active user holding one of the
    PV team roles chosen in Sign-off settings, and the extra addresses."""
    from app.services.approval_settings import load_settings

    settings = settings or load_settings()
    people = []

    def add(name, address):
        address = (address or "").strip().lower()
        if address and "@" in address and address not in [p[1] for p in people]:
            people.append((name or "colleague", address))

    owner = query_one(
        """
        SELECT u.full_name, u.email
        FROM pv.safety_cases AS c
        JOIN pv.users AS u ON u.user_id = c.created_by
        WHERE c.case_id = %s AND u.is_active
        """,
        (case_id,),
    )
    if owner:
        add(owner.get("full_name"), owner.get("email"))
    for row in query_all(
        """
        SELECT u.full_name, u.email
        FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE u.is_active AND r.role_name = ANY(%s)
        ORDER BY u.full_name
        """,
        (list(settings.get("officer_roles") or []),),
    ):
        add(row.get("full_name"), row.get("email"))
    for address in settings.get("officer_extra_emails") or []:
        add("colleague", address)
    return people


def _notify_owner(app, case_id, reply, attachment_count):
    """Email the PV team (each person at their own address) that a reply came."""
    from app.services.follow_up_automation import email_configured

    try:
        if not email_configured(app):
            return
        from app.services.follow_up_email import send_notification_email

        row = query_one("SELECT case_number FROM pv.safety_cases WHERE case_id = %s", (case_id,))
        if not row:
            return
        base = app.config.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")
        snippet = (reply.get("reply_text") or "").strip()
        if len(snippet) > 600:
            snippet = snippet[:600].rstrip() + " …"
        for name, address in team_recipients(case_id):
            body = (
                f"Dear {name},\n\n"
                f"The reporter replied to the follow-up request for {row['case_number']} "
                f"({reply['received_at'].astimezone():%d %b %Y at %H:%M}"
                f"{', with ' + str(attachment_count) + ' attachment(s)' if attachment_count else ''}).\n\n"
                f"{snippet}\n\n"
                "The reply is saved on the case and the AI is preparing suggested updates. "
                "Nothing changes on the case until someone applies them. Reminders to the "
                "reporter are paused.\n\n"
                f"Open the follow-up page: {base}/cases/follow-up-tasks#case-{case_id}\n\n"
                f"{platform()} (automatic message)"
            )
            try:
                send_notification_email(app, address, f"Reply received for {row['case_number']}", body)
            except Exception:
                app.logger.exception("Reply notification to %s failed", address)
    except Exception:
        app.logger.exception("Could not send reply notifications for case %s", case_id)


def _store(parsed, case_id, status, note):
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.follow_up_replies (
                message_id, case_id, from_email, from_name, subject,
                received_at, reply_text, match_note, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO NOTHING
            RETURNING reply_id
            """,
            (
                parsed["message_id"], case_id, parsed["from_email"], parsed["from_name"],
                parsed["subject"][:1000], parsed["received_at"], reply_text(parsed["text"]),
                note, status,
            ),
        )
        row = cursor.fetchone()
    return row["reply_id"] if row else None


# --------------------------------------------------------------------------
# Checking the inbox
# --------------------------------------------------------------------------

def inbox_configured(app=None):
    app = app or current_app
    return bool(
        app.config.get("FOLLOW_UP_READ_REPLIES")
        and app.config.get("IMAP_HOST")
        and app.config.get("IMAP_USERNAME")
        and app.config.get("IMAP_PASSWORD")
    )


def _header_candidates(connection, uids, own_addresses, reporter_emails):
    """UIDs whose From/Subject suggest a follow-up reply."""
    wanted = []
    for start in range(0, len(uids), FETCH_BATCH):
        chunk = b",".join(uids[start:start + FETCH_BATCH])
        status, data = connection.uid(
            "fetch", chunk, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])"
        )
        if status != "OK":
            continue
        for item in data:
            if not isinstance(item, tuple):
                continue
            meta, header_bytes = item
            uid_match = re.search(rb"UID (\d+)", meta)
            if not uid_match:
                continue
            headers = email.message_from_bytes(header_bytes)
            sender = parseaddr(_decode(headers.get("From")))[1].strip().lower()
            if not sender or sender in own_addresses:
                continue
            subject = _decode(headers.get("Subject"))
            if find_case_numbers(subject) or sender in reporter_emails:
                wanted.append((uid_match.group(1), (headers.get("Message-ID") or "").strip()))
    return wanted


def check_replies(app=None, now=None):
    """Read new replies from the inbox. Safe to call repeatedly.

    Returns {"matched": n, "unmatched": n, "checked": n, "skipped": reason|None}.
    """
    app = app or current_app._get_current_object()
    summary = {"matched": 0, "unmatched": 0, "checked": 0, "skipped": None,
               "checked_at": datetime.now(timezone.utc)}
    if not inbox_configured(app):
        summary["skipped"] = "Reading replies is not set up."
        return summary

    now = now or datetime.now(timezone.utc)
    lookback = app.config.get("FOLLOW_UP_REPLY_LOOKBACK_DAYS", 30)
    since = now - timedelta(days=lookback)
    cases_by_number, requests_by_email = _lookups(since)
    if not requests_by_email:
        summary["skipped"] = "No follow-up emails have been sent recently."
        return summary
    own = {
        (app.config.get("SMTP_SENDER_EMAIL") or "").lower(),
        (app.config.get("IMAP_USERNAME") or "").lower(),
    }

    connection = imaplib.IMAP4_SSL(app.config["IMAP_HOST"], app.config.get("IMAP_PORT", 993), timeout=60)
    try:
        connection.login(app.config["IMAP_USERNAME"], app.config["IMAP_PASSWORD"])
        # Read-only: nothing is marked as read, moved or deleted.
        connection.select("INBOX", readonly=True)
        status, data = connection.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
        uids = data[0].split() if status == "OK" and data and data[0] else []
        candidates = _header_candidates(connection, uids, own, set(requests_by_email))
        seen = _seen_message_ids([mid for _, mid in candidates if mid])
        for uid, message_id in candidates:
            if message_id and message_id in seen:
                continue
            status, data = connection.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((part[1] for part in data or [] if isinstance(part, tuple)), None)
            if status != "OK" or not raw:
                continue
            parsed = parse_message(raw)
            summary["checked"] += 1
            if parsed["auto_reply"] or parsed["message_id"] in seen:
                continue
            case_id, reply_status, note = match_reply(parsed, cases_by_number, requests_by_email)
            if not reply_status:
                continue
            try:
                reply_id = _store(parsed, case_id, reply_status, note)
                seen.add(parsed["message_id"])
                if not reply_id:
                    continue
                if case_id:
                    file_reply_on_case(app, reply_id, case_id, parsed)
                    summary["matched"] += 1
                else:
                    summary["unmatched"] += 1
            except Exception:
                app.logger.exception("Could not store follow-up reply %s", parsed["message_id"])
                _rollback()
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    return summary


def fetch_message(app, message_id):
    """Re-read one message from the inbox (used when linking a reply)."""
    if not inbox_configured(app) or not message_id or message_id.startswith("<no-id-"):
        return None
    connection = imaplib.IMAP4_SSL(app.config["IMAP_HOST"], app.config.get("IMAP_PORT", 993), timeout=60)
    try:
        connection.login(app.config["IMAP_USERNAME"], app.config["IMAP_PASSWORD"])
        connection.select("INBOX", readonly=True)
        status, data = connection.uid("search", None, "HEADER", "Message-ID", message_id)
        uids = data[0].split() if status == "OK" and data and data[0] else []
        if not uids:
            return None
        status, data = connection.uid("fetch", uids[-1], "(BODY.PEEK[])")
        raw = next((part[1] for part in data or [] if isinstance(part, tuple)), None)
        return parse_message(raw) if raw else None
    finally:
        try:
            connection.logout()
        except Exception:
            pass


def link_reply(app, reply_id, case_id, actor_user_id):
    """Attach an unmatched reply to a case chosen by the team."""
    reply = query_one(
        "SELECT * FROM pv.follow_up_replies WHERE reply_id = %s AND status = 'Unmatched'",
        (reply_id,),
    )
    if not reply:
        return False
    parsed = None
    try:
        parsed = fetch_message(app, reply["message_id"])
    except Exception:
        app.logger.warning("Could not re-read reply %s for its attachments", reply_id, exc_info=True)
    file_reply_on_case(app, reply_id, case_id, parsed, actor_user_id)
    return True


def set_reply_status(reply_id, status, actor_user_id):
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.follow_up_replies
            SET status = %s, handled_by = %s, handled_at = CURRENT_TIMESTAMP
            WHERE reply_id = %s
            RETURNING case_id
            """,
            (status, actor_user_id, reply_id),
        )
        row = cursor.fetchone()
    return row["case_id"] if row else None
