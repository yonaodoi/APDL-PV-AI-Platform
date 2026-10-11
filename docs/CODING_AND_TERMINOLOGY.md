# Coding and terminology (MedDRA, WHODrug, local terms)

## What you need (licences)

| Dictionary | Licence | What to import | Where |
|---|---|---|---|
| MedDRA | MSSO subscription | The **MedAscii** folder of the release (zip, or `llt.asc`, `pt.asc`, `soc.asc`, `mdhier.asc`; add `meddra_release.asc` to fill the version automatically) | Administration › Terminology |
| WHODrug | Uppsala Monitoring Centre licence | A WHODrug extract saved as CSV/TSV/Excel with named columns: `drug_name` and `drug_code` (or `drug_record_number` + `sequence_1` + `sequence_2`); optional `preferred`, `ingredients`, `atc`, `country`, `mah`. The fixed-width B3 files must be exported to CSV first. | Administration › Terminology |

The upload limit is `MAX_UPLOAD_MB` in `.env` (default 20). The English MedAscii
files are normally within that; raise it if your release is larger.

Recording a licence under **Licences & integrations** does not load any terms.
Coding uses a dictionary only after its files are imported. **Nothing in the
platform contains or invents MedDRA or WHODrug codes.** Without the files, the
coding step says so on every case:

* "MedDRA coding unavailable: dictionary access is not configured."
* "WHODrug coding unavailable: check the licence or connection."

Live dictionary APIs are not connected. The connection test for a MedDRA or
WHODrug entry only checks whether files have been imported.

## How a case is coded

Coding runs automatically each time the ICSR form is saved. You can also open
the coding step from the pathway or from the case's ⋯ menu ("Coding"). Each
reported item is coded beside its reported wording; the wording itself is never
changed. The items are:

* reactions (required)
* drugs (required)
* indications
* medical history
* tests
* past drugs

For each item, the platform tries these steps in order:

1. **Exact match** in the imported dictionary (MedDRA LLT name or WHODrug drug name).
2. **Wording confirmed before.** A reviewer ticked "remember wording" when choosing a term.
3. **Dictionary search**, including similar spellings (for example "uticaria" finds "Urticaria").
4. **Optional AI suggestions.** Only names that exist in the imported dictionary are shown, and they are never accepted automatically.
5. **Local terminology.**
   * If no suitable official term is found, a local term is proposed: "No suitable official match found. Local terminology proposed for review."

Which of these may be accepted without a person is set under **Administration ›
Terminology › Automatic coding rules**. Everything else shows **Needs review**
with its candidates. The person coding can:

* choose a candidate
* search
* propose a local term
* for optional items, record why the item is not coded

Reactions and drugs must be coded before the pathway moves on to Assessment.

Every coding stores the following, and its history is kept on the coding page:

* dictionary
* code
* term
* PT/SOC (MedDRA) or ingredients/ATC (WHODrug)
* version
* method
* who coded it, and when

If the reported wording changes later, the item goes back to Needs review.

## Local terminology

* Local event terms are `LOCAL-E-nnnnn` and local products are `LOCAL-P-nnnnn`. They are always labelled as local, never as MedDRA or WHODrug.
* A proposed local term counts on a case only after approval. Approval is in Administration › Terminology › Local terms, and needs one of these roles: System Administrator, QPPV, Deputy QPPV or Group Head RA & Quality.
* After approval, case codings that use the term count as coded. Reactions coded to approved local event terms are also used to group cases for signal detection.
* A local term can be mapped to an official code once the dictionary is imported. The mapping is checked against the imported files.

## Not yet done / limits

* There is no automatic upgrade of existing codings to a new MedDRA version. New coding uses the newest import, and each existing coding keeps the version it was coded with.
* SMQs and secondary SOC paths are not used yet.
* WHODrug B3/C3 fixed-width files are not read directly.
