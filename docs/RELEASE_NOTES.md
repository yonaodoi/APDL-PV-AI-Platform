# Release notes: ICSR pathway programme (versions 68–77)

## 1. Install and restart (Windows)

1. **Back up first** (section 2).
2. Stop the app (press Ctrl+C in its window).
3. In the project folder, with the virtual environment active:
   ```
   python scripts\migrate.py
   ```
   This applies migrations 051–059, in order:
   - company profile
   - deleted records
   - ICSR structure
   - product catalogue and integrations
   - terminology and coding
   - document intake
   - narratives
   - notifications
   - E2B exports

   Each migration is safe to re-run.
4. Optional: add `PV_SECRETS_KEY=<a long random text>` to `.env` to encrypt integration credentials with your own key. Set it once and never change it; if you change it, saved credentials can no longer be read and must be entered again.
5. Start the app again, then press **Ctrl+F5** in the browser.
6. Commit and push to your repository as usual.

## 2. Backup and rollback

**Before migrating**, take a database backup:
```
pg_dump -h localhost -U <user> -d <database> -F c -f pv_backup_YYYYMMDD.dump
```
Also copy the project folder, including `instance\` (uploads and logos).

**To roll back:**
1. Stop the app.
2. Restore the previous code: `git checkout <previous commit>`, or put back your folder copy.
3. Restore the database:
   ```
   pg_restore -h localhost -U <user> -d <database> --clean pv_backup_YYYYMMDD.dump
   ```

The migrations only add tables and columns, so older code also runs on a migrated database. You only need to restore the database if you want to remove the new data as well.

## 3. What changed

| Area | Summary |
|---|---|
| Company profile (v69) | One installation per company: name, logo, signatories and numbering under Administration › Company profile. |
| Deletion (v70) | Only the System Administrator can delete cases, signals, complaints and PSURs. A full copy is kept under Deleted records. |
| ICSR pathway (v71) | ICSR sections A–I, plus the pathway: Intake → Validity → ICSR → Coding → Assessment → Narrative → Review → Reporting. Validity is separate from completeness. Cases that fail validity wait in Awaiting information. |
| Catalogue and integrations (v72) | Product catalogue with CSV/Excel import, preview, duplicate checks and record snapshots. Licences & integrations admin: credentials are encrypted, with connection tests, retries and AI spending limits. |
| Coding (v73) | MedDRA and WHODrug coding against imported licensed files, plus a separate local terminology with approval. Completeness items are clickable and open the exact field. |
| Document intake (v74) | One upload can produce several patient reports. A reviewer confirms, splits, merges or rejects each one before any case is created. The same file is never processed twice. |
| Narratives (v75) | Narratives are built from the recorded facts using approved, versioned templates for spontaneous, clinical trial and literature cases. A second person must review them. |
| Cards (v76) | One PSUR Workspace card with clickable counts, more compact cards, and links without underlines. |
| v77 | E2B(R3)/(R2) export with checks and versioned files; completion alerts (sound, pop-up, desktop); plain-language error pages; the QPPV can process a case alone (setting); sign-in attempts are shown. |

Topic guides are in `docs\`:
- `ICSR_FORM_CROSSCHECK.md`
- `CODING_AND_TERMINOLOGY.md`
- `DOCUMENT_INTAKE.md`
- `NARRATIVES.md`
- `E2B_EXPORT.md`
- `NEW_COMPANY_SETUP.md`

## 4. Licence and external dependencies

| Item | Needed for | Status |
|---|---|---|
| MedDRA (MSSO subscription) | Official event coding; E2B reaction codes | Import the MedAscii release under Administration › Terminology. Without it, local terms are used and E2B files remain drafts. |
| WHODrug (UMC licence) | Drug coding and active ingredient names | Import a CSV/Excel extract. B3/C3 fixed-width files must be converted first. |
| AI service (Claude API key or local Ollama) | Extraction, assessment drafts, AI coding suggestions | Configure under Licences & integrations or in `.env`. |
| ICH E2B schemas (XSD for R3, DTD for R2) | "Schema-validated" E2B files | Copy them into `instance\e2b\r3` and `instance\e2b\r2`. |
| Recipient registration (EMA, national authority, partners) | Sender and receiver identifiers; test submissions | Arrange with each recipient. The platform does not transmit files. |

## 5. Not tested / unresolved

**Never tested with the real thing:**
- Real MedDRA or WHODrug releases. Only synthetic sample files were used.
- The official ICH schemas.
- Any regulator's test gateway.
- Live MedDRA or WHODrug API connections. None are connected; the connection tests only confirm that files have been imported.

**E2B export gaps:**
- No regional extensions (EU/FDA-specific fields, IDMP identifiers).
- Not exported: parent data, drug–reaction intervals, study registration, attached documents.
- The code lists are held in one table in `app/services/e2b.py`; check them against current ICH code lists.

**Alerts:**
- Browsers only play the completion sound after you have clicked somewhere on the page; the pop-up always shows.
- Background jobs stop if the server restarts. They are marked as failed, and you start them again.

**Not built:**
- Mobile/PWA work (left out as requested).
- Showing users' current passwords (not possible by design; use Reset password).

**Earlier pending items:**
- Suggested next workflow status.
- Readiness check before submission.
- Automatic follow-up status moves.
- A full tidy-up of buttons in older modules.
