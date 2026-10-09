"""The company this installation belongs to.

Every page, email, Word report, file name and AI instruction reads the
company's name and details from here, so the platform can be installed for
another marketing authorisation holder by filling in the Company profile page
under Administration.  Nothing is hard-wired: with no saved profile the
defaults below (APDL) are used, which is exactly how the platform behaved
before the profile existed.
"""

import json
import re
from datetime import date
from pathlib import Path

from flask import current_app, g, has_app_context, has_request_context

from app.db import get_db, query_one, transaction

SETTING_KEY = "company_profile"

# A fresh installation starts from neutral values until an administrator
# fills in the Company profile page.
DEFAULTS = {
    "legal_name": "Your Company Limited",
    "letter_name": "Your Company Ltd",
    "short_name": "Company",
    "platform_name": "PV",
    "department": "Pharmacovigilance Department",
    "pv_team_name": "Pharmacovigilance team",
    "address": "",
    "country": "",
    "pv_email": "",
    "pv_phone": "",
    "website": "",
    "case_prefix": "ICSR",
    "signal_prefix": "SIG",
    "complaint_prefix": "PC",
    "psur_prefix": "PSUR",
    "brand_colour": "#145a40",
    "logo_file": "",
    "form_code": "",
    "form_revision": "",
    "form_effective_date": "",
    "sign_prepared_name": "",
    "sign_prepared_title": "",
    "sign_reviewed_name": "",
    "sign_reviewed_title": "",
    "sign_authorised_name": "",
    "sign_authorised_title": "",
    "previous_case_prefixes": [],
}

# APDL's details: saved as its profile by migration 051 on the existing
# APDL database, so nothing changes there.
APDL_PROFILE = {
    "legal_name": "Abacus Parenteral Drugs Limited",
    "letter_name": "Abacus Parenteral Drugs Ltd",
    "short_name": "APDL",
    "platform_name": "APDL PV",
    "department": "Regulatory Affairs Department",
    "pv_team_name": "Pharmacovigilance team",
    "address": "",
    "country": "Uganda",
    "pv_email": "",
    "pv_phone": "+256786557530",
    "website": "",
    "case_prefix": "APDL-ICSR",
    "signal_prefix": "APDL-SIG",
    "complaint_prefix": "APDL-PC",
    "psur_prefix": "APDL-PSUR",
    "brand_colour": "#145a40",
    "logo_file": "",
    # Document control printed on the built-in follow-up form.
    "form_code": "SF/RA/012.2",
    "form_revision": "00",
    "form_effective_date": "31/08/2026",
    # Default signatories printed on forms and reports.
    "sign_prepared_name": "AMEKO CHARLES",
    "sign_prepared_title": "DEPUTY Q.P.P.V.",
    "sign_reviewed_name": "YONA ODOI",
    "sign_reviewed_title": "Q.P.P.V.",
    "sign_authorised_name": "KEITH ARUHO",
    "sign_authorised_title": "GROUP HEAD, RA & QUALITY",
}

# What each field means, in the order the admin page shows them.
FIELDS = [
    ("legal_name", "Registered company name", "Used on Word reports and PSURs, e.g. Example Pharmaceuticals Limited."),
    ("letter_name", "Name on letters and emails", "Shorter form used to sign reporter emails, e.g. Example Pharmaceuticals Ltd."),
    ("short_name", "Short name", "Used in sentences such as “reported to EPL” and on product information, e.g. EPL."),
    ("platform_name", "Platform name", "Shown in the sidebar, page titles and the sign-in page, e.g. EPL PV."),
    ("department", "Department on forms", "Printed under the company name on follow-up forms."),
    ("pv_team_name", "PV team name", "How emails are signed off."),
    ("address", "Address", "Optional. Printed on letters if your templates use it."),
    ("country", "Home country", "Default country for new cases and reference documents."),
    ("pv_email", "PV contact email", "Optional. Given to reporters as the address to reply to."),
    ("pv_phone", "PV contact telephone", "Given to reporters in follow-up emails and forms."),
    ("website", "Website", "Optional."),
]
FORM_FIELDS = [
    ("form_code", "Document number"),
    ("form_revision", "Revision"),
    ("form_effective_date", "Effective date"),
]
SIGNATORY_FIELDS = [
    ("prepared", "Prepared by"),
    ("reviewed", "Reviewed by"),
    ("authorised", "Authorised by"),
]
PREFIX_FIELDS = [
    ("case_prefix", "Safety cases", "ICSR"),
    ("signal_prefix", "Safety signals", "SIG"),
    ("complaint_prefix", "Quality complaints", "PC"),
    ("psur_prefix", "PSURs", "PSUR"),
]

LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp"}
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{0,29}$")


# Fields that may be left blank on purpose.
OPTIONAL = {
    "address", "country", "pv_email", "website", "logo_file", "department",
    "form_code", "form_revision", "form_effective_date",
    "sign_prepared_name", "sign_prepared_title", "sign_reviewed_name",
    "sign_reviewed_title", "sign_authorised_name", "sign_authorised_title",
}


def normalise(values):
    """Fill gaps with defaults and keep each value tidy."""
    values = values or {}
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if key == "previous_case_prefixes":
            continue
        value = values.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, "") or (key in OPTIONAL and key in values):
            result[key] = value if value is not None else ""
    for key in DEFAULTS:
        if key not in ("address", "previous_case_prefixes"):
            result[key] = " ".join(str(result[key]).split())
    result["address"] = "\n".join(line.strip() for line in str(result["address"]).splitlines() if line.strip())
    for key, _, _ in PREFIX_FIELDS:
        prefix = result[key].strip().strip("-").upper()
        result[key] = prefix if PREFIX.match(prefix) else DEFAULTS[key]
    if not COLOUR.match(result["brand_colour"]):
        result["brand_colour"] = DEFAULTS["brand_colour"]
    result["brand_colour"] = result["brand_colour"].lower()
    previous = values.get("previous_case_prefixes") or []
    result["previous_case_prefixes"] = [
        str(p).upper() for p in previous if isinstance(p, str) and PREFIX.match(p) and str(p).upper() != result["case_prefix"]
    ][:10]
    return result


def problems(values):
    """Plain-language problems with what an administrator typed."""
    out = []
    for key in ("legal_name", "letter_name", "short_name", "platform_name", "pv_phone"):
        if not str((values or {}).get(key) or "").strip():
            out.append(f"{dict((k, l) for k, l, _ in FIELDS)[key]} is required.")
    email = str((values or {}).get("pv_email") or "").strip()
    if email:
        from app.services.approval_settings import valid_email

        if not valid_email(email):
            out.append("The PV contact email does not look like an email address.")
    for key, label, _ in PREFIX_FIELDS:
        prefix = str((values or {}).get(key) or "").strip().strip("-")
        if prefix and not PREFIX.match(prefix):
            out.append(f"The {label.lower()} prefix may only use letters, numbers, - / and _.")
    colour = str((values or {}).get("brand_colour") or "").strip()
    if colour and not COLOUR.match(colour):
        out.append("The brand colour must look like #145a40.")
    return out


def _read():
    try:
        row = query_one("SELECT value FROM pv.app_settings WHERE setting_key = %s", (SETTING_KEY,))
    except Exception:
        try:
            get_db().rollback()
        except Exception:
            pass
        return {}
    value = row["value"] if row else {}
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}


def load_profile():
    """The saved profile merged with the defaults (fresh from the database)."""
    return normalise(_read())


def company():
    """The profile for the current request (read once per request)."""
    if has_request_context():
        if "company_profile" not in g:
            g.company_profile = _safe_load()
        return g.company_profile
    return _safe_load()


def _safe_load():
    if not has_app_context():
        return dict(DEFAULTS)
    try:
        return load_profile()
    except Exception:
        return dict(DEFAULTS)


def is_set_up():
    """Has an administrator saved a company profile yet?"""
    return bool(_read())


def save_profile(values, user_id):
    before = load_profile()
    values = dict(values)
    previous = list(before.get("previous_case_prefixes") or [])
    if before["case_prefix"] != str(values.get("case_prefix") or before["case_prefix"]).strip().strip("-").upper() and is_set_up():
        previous.insert(0, before["case_prefix"])
    values["previous_case_prefixes"] = previous
    profile = normalise(values)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.app_settings (setting_key, value, updated_by, updated_at)
            VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO UPDATE
            SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, json.dumps(profile), user_id),
        )
    if has_request_context():
        g.company_profile = profile
    return profile


# --------------------------------------------------------------------------
# Short helpers used across the code
# --------------------------------------------------------------------------

def short_name():
    return company()["short_name"]


def legal_name():
    return company()["legal_name"]


def platform():
    """'APDL PV platform' – as used in report sentences."""
    return f"{company()['platform_name']} platform"


def file_prefix():
    """Safe start of downloaded file names, e.g. 'APDL'."""
    name = re.sub(r"[^A-Za-z0-9]+", "_", company()["short_name"]).strip("_")
    return name or "PV"


def example_number(kind, today=None):
    """An example reference number for a form placeholder."""
    profile = company()
    year = (today or date.today()).year
    if kind == "case":
        return f"{profile['case_prefix']}-{year % 100:02d}-001"
    if kind == "signal":
        return f"{profile['signal_prefix']}-001"
    if kind == "complaint":
        return f"{profile['complaint_prefix']}-{year}-0001"
    return f"{profile['psur_prefix']}-{year}-0001"


def case_number_pattern():
    """Regex matching case numbers in reporter replies.

    Accepts the current prefix and any earlier ones, so replies about cases
    entered before a prefix change are still recognised.
    """
    profile = company()
    prefixes = {profile["case_prefix"], *profile.get("previous_case_prefixes", [])}
    alternatives = "|".join(sorted((re.escape(p) for p in prefixes), key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9/-])(?:{alternatives})-\d{{2}}-[A-Z0-9]+\b", re.IGNORECASE)


def docx_brand_colour():
    """Brand colour as 'RRGGBB' for Word headings."""
    return company()["brand_colour"].lstrip("#").upper()


# --------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------

def logo_folder():
    return Path(current_app.config["UPLOAD_ROOT"]) / "branding"


def logo_path():
    name = company().get("logo_file")
    if not name:
        return None
    path = logo_folder() / name
    return path if path.exists() else None


def store_logo(file_storage):
    """Save an uploaded logo and return its stored file name."""
    suffix = Path(file_storage.filename or "").suffix.lower()
    if suffix not in LOGO_TYPES:
        raise ValueError("The logo must be a PNG, JPG, SVG or WEBP image.")
    data = file_storage.read()
    if not data:
        raise ValueError("The logo file is empty.")
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("The logo must be smaller than 2 MB.")
    folder = logo_folder()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"logo-{date.today():%Y%m%d}-{abs(hash(data)) % 10**8:08d}{suffix}"
    (folder / name).write_bytes(data)
    return name


def shade(hex_colour, factor):
    """Darken (factor < 1) or lighten (factor > 1) a #rrggbb colour."""
    value = hex_colour.lstrip("#")
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    if factor <= 1:
        rgb = [int(c * factor) for c in rgb]
    else:
        rgb = [int(c + (255 - c) * (factor - 1)) for c in rgb]
    return "#" + "".join(f"{min(max(c, 0), 255):02x}" for c in rgb)


def platform_name():
    """'APDL PV' – the platform's display name."""
    return company()["platform_name"]


def signatory(kind):
    """(NAME, DESIGNATION) of the default 'prepared', 'reviewed' or 'authorised' signatory."""
    profile = company()
    return profile[f"sign_{kind}_name"], profile[f"sign_{kind}_title"]


def signatory_rows(extra_columns=0):
    """Rows for a signature table: (role, name, designation, '' * extra_columns)."""
    return tuple(
        (label,) + signatory(kind) + ("",) * extra_columns
        for kind, label in SIGNATORY_FIELDS
    )
