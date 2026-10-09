from datetime import date, datetime, timezone
from email.message import EmailMessage

import app.services.follow_up_automation as automation
import app.services.follow_up_replies as replies

CASE = {"case_id": 7, "reporter_email": "nurse@clinic.org", "recipients": {"nurse@clinic.org"}}
CASES = {"APDL-ICSR-26-TEST1": CASE}
REQUESTS = {"nurse@clinic.org": [{"case_id": 7, "sent_at": datetime(2026, 10, 8, tzinfo=timezone.utc)}]}


def raw_reply(sender="Nurse Amina <nurse@clinic.org>",
              subject="Re: Follow-up request for adverse reaction report APDL-ICSR-26-TEST1",
              body=None, attachment=None, headers=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "odoiwilber2@gmail.com"
    message["Subject"] = subject
    message["Date"] = "Fri, 09 Oct 2026 10:15:00 +0300"
    message["Message-ID"] = "<abc123@clinic.org>"
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(body or (
        "The patient recovered on 12 Oct 2026. Weight 64 kg.\n\n"
        "On Thu, 8 Oct 2026 at 22:51, APDL Pharmacovigilance <odoiwilber2@gmail.com> wrote:\n"
        "> Dear reporter,\n"
        "> Please tell us the outcome.\n"
    ))
    if attachment:
        message.add_attachment(attachment[1], maintype="application", subtype="pdf", filename=attachment[0])
    return message.as_bytes()


def test_reply_is_read_and_our_quoted_email_removed():
    parsed = replies.parse_message(raw_reply(attachment=("lab.pdf", b"%PDF-1.4 test")))

    assert parsed["from_email"] == "nurse@clinic.org"
    assert parsed["from_name"] == "Nurse Amina"
    assert parsed["message_id"] == "<abc123@clinic.org>"
    assert parsed["received_at"].utcoffset().total_seconds() == 3 * 3600
    assert parsed["attachments"][0]["filename"] == "lab.pdf"
    text = replies.reply_text(parsed["text"])
    assert "recovered on 12 Oct 2026" in text
    assert "Please tell us the outcome" not in text
    assert "wrote:" not in text


def test_inline_answers_between_quoted_lines_are_kept():
    body = (
        "Answers below.\n\n"
        "On Thu, 8 Oct 2026, APDL wrote:\n"
        "> 1. Outcome of the reaction?\n"
        "Recovered\n"
        "> 2. Patient weight?\n"
        "64 kg\n"
    )
    text = replies.reply_text(body)
    assert "Recovered" in text and "64 kg" in text
    assert "Outcome of the reaction" not in text


def test_reply_matched_by_case_number_and_reporter_address():
    parsed = replies.parse_message(raw_reply())
    case_id, status, note = replies.match_reply(parsed, CASES, REQUESTS)
    assert (case_id, status) == (7, "New")
    assert "case number" in note


def test_case_number_from_a_different_address_waits_to_be_linked():
    parsed = replies.parse_message(raw_reply(sender="someone@else.com"))
    case_id, status, note = replies.match_reply(parsed, CASES, REQUESTS)
    assert case_id is None and status == "Unmatched"
    assert "someone@else.com" in note


def test_reply_without_case_number_matched_by_sender():
    parsed = replies.parse_message(raw_reply(subject="Information you asked for"))
    assert replies.match_reply(parsed, CASES, REQUESTS)[:2] == (7, "New")

    two_cases = {"nurse@clinic.org": REQUESTS["nurse@clinic.org"] + [{"case_id": 9, "sent_at": None}]}
    assert replies.match_reply(parsed, CASES, two_cases)[:2] == (None, "Unmatched")


def test_unrelated_mail_is_ignored():
    parsed = replies.parse_message(raw_reply(sender="news@shop.com", subject="Weekly offers"))
    assert replies.match_reply(parsed, CASES, REQUESTS) == (None, None, None)


def test_out_of_office_replies_are_flagged():
    parsed = replies.parse_message(raw_reply(headers={"Auto-Submitted": "auto-replied"}))
    assert parsed["auto_reply"] is True


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL for check_replies."""

    def __init__(self, messages):
        self.messages = messages  # {uid: raw}
        self.readonly = None
        self.logged_in = False

    def login(self, user, password):
        self.logged_in = True

    def select(self, mailbox, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(self.messages)]
        uids, what = args
        wanted = uids.split(b",") if isinstance(uids, bytes) else [uids]
        data = []
        for uid in wanted:
            raw = self.messages[uid]
            if "HEADER.FIELDS" in what:
                header = raw.split(b"\n\n", 1)[0] + b"\n\n"
                data.append((b"%s (UID %s BODY[HEADER] {1}" % (uid, uid), header))
            else:
                data.append((b"%s (UID %s BODY[] {1}" % (uid, uid), raw))
            data.append(b")")
        return "OK", data

    def logout(self):
        pass


def test_inbox_check_files_matched_reply_and_reads_inbox_read_only(monkeypatch):
    from app import create_app
    from config import TestingConfig

    app = create_app(TestingConfig)
    app.config.update(FOLLOW_UP_READ_REPLIES=True, IMAP_HOST="imap.test", IMAP_USERNAME="odoiwilber2@gmail.com",
                      IMAP_PASSWORD="x", SMTP_SENDER_EMAIL="odoiwilber2@gmail.com")
    fake = FakeIMAP({
        b"11": raw_reply(),
        b"12": raw_reply(sender="news@shop.com", subject="Weekly offers").replace(b"abc123", b"zzz"),
        b"13": raw_reply(sender="APDL <odoiwilber2@gmail.com>").replace(b"abc123", b"own"),
    })
    monkeypatch.setattr(replies.imaplib, "IMAP4_SSL", lambda host, port, timeout=None: fake)
    monkeypatch.setattr(replies, "_lookups", lambda since: (CASES, REQUESTS))
    monkeypatch.setattr(replies, "_seen_message_ids", lambda ids: set())
    stored, filed = [], []
    monkeypatch.setattr(replies, "_store", lambda parsed, case_id, status, note: stored.append((parsed["from_email"], case_id, status)) or 1)
    monkeypatch.setattr(replies, "file_reply_on_case", lambda app, reply_id, case_id, parsed: filed.append((case_id, len(parsed["attachments"]))))

    with app.app_context():
        summary = replies.check_replies(app)

    assert fake.readonly is True
    assert stored == [("nurse@clinic.org", 7, "New")]
    assert filed == [(7, 0)]
    assert summary["matched"] == 1 and summary["unmatched"] == 0


def test_reminders_pause_while_a_reply_waits(monkeypatch):
    rows = [{"case_id": 7, "requested": datetime(2026, 9, 1, tzinfo=timezone.utc), "sends": 1,
             "last_sent": datetime(2026, 9, 1, tzinfo=timezone.utc), "due": date(2026, 9, 8)}]
    monkeypatch.setattr(automation, "query_all", lambda sql, parameters=(): rows)
    monkeypatch.setattr(replies, "reply_state", lambda case_ids=None: {
        7: {"waiting": 1, "last_reply": datetime(2026, 9, 3, tzinfo=timezone.utc), "last_handled": None}})

    status = automation.reminder_status(date(2026, 10, 1), reminder_days=7, max_reminders=2)

    assert status[7]["reply_waiting"] is True
    assert status[7]["next_reminder"] is None
    assert automation.cases_needing_reminder(date(2026, 10, 1)) == []


def test_reminders_restart_from_when_the_reply_was_handled(monkeypatch):
    rows = [{"case_id": 7, "requested": datetime(2026, 9, 1, tzinfo=timezone.utc), "sends": 1,
             "last_sent": datetime(2026, 9, 1, tzinfo=timezone.utc), "due": date(2026, 9, 8)}]
    monkeypatch.setattr(automation, "query_all", lambda sql, parameters=(): rows)
    monkeypatch.setattr(replies, "reply_state", lambda case_ids=None: {
        7: {"waiting": 0, "last_reply": None, "last_handled": datetime(2026, 9, 20, 12, tzinfo=timezone.utc)}})

    status = automation.reminder_status(date(2026, 9, 21), reminder_days=7, max_reminders=2)

    assert status[7]["next_reminder"] == date(2026, 9, 27)


def test_whole_pv_team_is_notified_at_their_own_addresses(monkeypatch):
    monkeypatch.setattr(replies, "query_one", lambda sql, parameters=(): {"full_name": "Agnes", "email": "agnes@apdl.org"})
    monkeypatch.setattr(replies, "query_all", lambda sql, parameters=(): [
        {"full_name": "Agnes", "email": "Agnes@apdl.org"},
        {"full_name": "Brian", "email": "brian@apdl.org"},
        {"full_name": "No Mail", "email": ""},
    ])
    people = replies.team_recipients(7, {"officer_roles": ["PV Officer", "QPPV"], "officer_extra_emails": ["pv.box@apdl.org"]})
    assert [p[1] for p in people] == ["agnes@apdl.org", "brian@apdl.org", "pv.box@apdl.org"]
