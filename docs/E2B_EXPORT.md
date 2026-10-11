# E2B(R3) / E2B(R2) XML export

Open the case, then **⋯ › E2B XML export**, or follow the pathway's *Reporting* step.

1. Choose the format, E2B(R3) or E2B(R2), and the recipient.
2. The page checks the case and lists every problem. Each one links to the field to correct: the ICSR form field, the coding step, validity, the narrative or sign-off.
   - **Correct**: must be fixed before the file can be used.
   - **Check**: a warning only.
3. Make the file. Every file is kept as a numbered version for the case. Each version records:
   - its check results
   - the MedDRA and WHODrug versions it used
   - a SHA-256 fingerprint of the file
   - who made it and when

   The case audit trail records each generation and each download.

## Status of a file

| Status | Meaning |
|---|---|
| Draft – not for submission | Problems remained when it was made (or the case is not approved in sign-off). The file name says `DRAFT-not-for-submission`. |
| Checked | Passed the platform's built-in rules: required elements, code lists, date formats, recipient rules and well-formed XML. |
| Schema-validated | Also passed the official ICH schema installed on the server (see below). |

None of these means "submitted" or "accepted". **The platform never sends files.**

- Submit through your usual gateway, such as the EudraVigilance gateway or the national authority's portal.
- Then record the submission date on the case.
- Send the first file to each recipient's test environment before going live.

## Set-up (System Administrator): Administration › E2B settings

- **Sender identifier, type, organisation and country.** These are as registered with each recipient.
- **Recipients.** For each recipient, set:
  - its name
  - its receiver identifier
  - the formats it accepts
  - its rules: all reactions coded to MedDRA, most-recent-information date required, batch number required for suspect drugs, study registration reminder
- **Official schemas (recommended).** Copy them into the folders shown on the settings page, under the instance folder:
  - the E2B(R3) XSD set, i.e. the folder containing `MCCI_IN200100UV01.xsd` → `instance\e2b\r3\`
  - the E2B(R2) DTD → `instance\e2b\r2\`

  Get the schema files from ICH, or from the recipient's implementation package.

## Dictionaries

- **Reactions** go out with their MedDRA LLT code and the MedDRA version they were coded with. A reaction coded only to a LOCAL term cannot be exported for a recipient that requires MedDRA.
- **WHODrug coding** supplies the active ingredient names. The reported drug name is always sent as reported.
- **Dates:** partial dates stay partial (R3: `CCYY`, `CCYYMM`; R2 date formats `602` and `610`).
- **AI-assisted causality drafts** are never exported.

## Known limits

- The XML follows the ICH E2B(R3) implementation guide and the E2B(M) DTD 2.1 element names. It has only been tested with the built-in checks and a sample schema. **It has not been tested against the official ICH schema or a regulator's test gateway.** Do both before the first live submission.
- Code lists (outcome, action taken, route of administration, observation codes and the R3 object identifiers) are kept in one table in `app/services/e2b.py`. Check them against the current ICH code lists when ICH publishes an update.
- Regional extensions are not produced. Examples: EU-specific fields, FDA regional data elements, IDMP product identifiers (MPID/PhPID).
- Not exported yet:
  - parent data (parent–child reports, D.10)
  - drug–reaction intervals (G.k.9.i.3)
  - study registration (C.5.1)
  - attached documents
