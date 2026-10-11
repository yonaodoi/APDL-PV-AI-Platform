# Creating cases from documents (one or several patients)

To start, go to **Safety cases › Create from a document** and upload a PDF, DOCX or TXT file.

1. **The original file is kept.**
   * It is stored once, identified by its contents (SHA-256).
   * Uploading the same file again, under any name, opens the earlier results. Nothing new is created and no AI call is made.
2. **The file is read page by page.** A progress message shows each step:
   * reading the document
   * finding patients
   * extracting each report

   You can leave the page while this runs.
3. **The AI lists the candidate patient reports.** For each one it gives:
   * the pages it is on
   * a verbatim excerpt
   * whether it is a new patient, follow-up for an existing case, or not a patient report

   Excerpts are checked against the document text, and anything that cannot be found is flagged. A report that cannot be located gets no facts, so another patient's facts can never be copied into it.
4. **Facts are extracted for each report from its own text only.** When several patients share a page, a report runs from its own first words to where the next patient starts. If those boundaries cannot be confirmed, the report is flagged.
5. **Possible duplicates are checked**, both against existing cases and between reports in the same document (the same initials, age or date of birth, and sex).
6. **The reviewer decides on each report.** For each one, the reviewer can:
   * **Create safety case.** This opens the usual case form filled from that report only. It also shows a suggested case number, the pages used, the source text and any flags. Each case then has its own pathway, starting with validity.
   * **Add as follow-up.** The document is added to the existing case, and that report's facts become suggested updates, which are reviewed before they are applied.
   * **Treat as.** Changes the report type.
   * **Split.** Paste the words where the second patient starts.
   * **Merge.** Tick the reports that are the same patient.
   * **Reject**, with a reason. A rejected report can be restored.
7. **Retries and double-clicks cannot create duplicates.** Each report is locked while its case is saved, and a report can become only one case.
   * Once anyone has made a decision, the document is not read again.
   * A document still shown as "in progress" 20 minutes after upload (for example, after a restart) can be started again.

Each created case records the intake document and pages it came from. The original file is attached to the case as its source report.

**Limits:** scanned image-only PDFs need OCR first. Only the first 60,000 characters are screened for patients.
