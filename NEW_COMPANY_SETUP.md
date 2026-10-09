# Setting up the PV platform for another company

The platform is installed **once per company**: each company has its own copy of
the code, its own PostgreSQL database and its own upload folder. No safety data
is ever shared between companies.

Nothing in the code is tied to one company. The company's name, contacts,
reference-number prefixes, signatories, logo and colour all come from
**Administration › Company profile**.

## 1. Install

1. Copy the project folder (or clone the repository) to the new company's server.
2. Create an empty PostgreSQL database for the company.
3. Create a `.env` file for this installation. Use the company's own values:
   - `DATABASE_URL`
   - `APDL_PV_SECRET_KEY` (a new long random value; the name is historical)
   - `UPLOAD_ROOT`
   - the company's email account (`SMTP_*` / `IMAP_*`)
   - `ANTHROPIC_API_KEY` if AI assistance is wanted

   Leave `SMTP_SENDER_NAME` and `PV_CONTACT_PHONE` blank so that the company
   profile is used.
4. Install the requirements: `pip install -r requirements.txt`
5. Create the tables: `python scripts\migrate.py`

   On an empty database, migration 051 does **not** add any company details, so
   the platform starts from neutral values.
6. Create the first administrator: `python -m flask --app run create-admin`

## 2. Fill in the company profile

Sign in as the administrator and open **Administration › Company profile**:

| Section | What to enter |
|---|---|
| Company | Registered name, name on letters, short name, platform name, department, PV team name, address, home country, PV email and telephone |
| Reference number prefixes | For example `XYZ-ICSR`, `XYZ-SIG`, `XYZ-PC`, `XYZ-PSUR`. They appear as examples on forms and are used to recognise case numbers in reporters' replies |
| Signatories | Default "Prepared / Reviewed / Authorised by" names and designations for forms and reports |
| Follow-up form document control | Document number, revision and effective date of the company's follow-up form |
| Look | Logo (sidebar and sign-in page) and brand colour (sidebar, headers, section edges) |

Every change is recorded in the audit trail.

## 3. Company set-up inside the platform

1. **Users:** add users and roles under Administration, and mark the designated QPPV.
2. **Sign-off:** choose who reviews and approves under Administration › Sign-off settings.
3. **Report templates:** upload the company's own Word templates under Report templates (case report, follow-up form, AI assessment, PSUR, summaries). Reports are then printed in the company's layout. Without templates, the built-in layouts are used, carrying the company profile details.
4. **Reference library:** add the company's products and their reference safety documents.

## What changes automatically from the profile

- Page titles, sidebar, sign-in page, dashboard and help text.
- Emails to reporters and to the PV team: sender name, signature, phone and email.
- Word reports (case, signal, complaint, regulatory, AI assessment, follow-up form, PSUR): company name, department, signatories and wording.
- AI instructions, e.g. "comparison with XYZ Product Information".
- Download file names, e.g. `XYZ_safety_case_reporting_summary.docx`.
- Example reference numbers on forms, and recognition of case numbers in reporter replies.

Reference numbers already saved are never changed. If a prefix is changed later,
replies quoting the earlier prefix are still recognised.
