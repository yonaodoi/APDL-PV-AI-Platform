# Controlled ICSR narratives

## On a case

Go to **Case › ⋯ › Narrative**, or use the pathway's *Write and review the narrative* step.

1. **Generate a new draft.**
   - The draft is built only from the structured ICSR record, using the approved template for the case type:
     - Spontaneous
     - Clinical trial
     - Literature
   - The case type comes from *Case category* in section A of the ICSR form.
   - Generating a draft never changes the case narrative.
2. **Check the draft.**
   - Points to check are listed beside it: contradictions, values that look like placeholders, and AI causality drafts that were left out.
   - *Where each sentence comes from* shows the structured fields behind every sentence.
3. **Use as the case narrative.**
   - You can edit the text first. Your edits are kept exactly as typed, as a new version.
   - Text that still contains `{field}` or `[[ ]]` is refused.
4. **Review.**
   - A second person marks the narrative reviewed. The pathway only moves on to Review once this is done.
   - The person who wrote or adopted that version cannot review it.
   - Reviewers can be: System Administrator, QPPV, Deputy QPPV, Group Head RA & Quality, Medical Reviewer, Quality Reviewer.

Editing the narrative in the ICSR form is also saved as a new version, which then needs review again. Every version records:

- the template and version it was made with
- the facts it used
- the issues found
- who made it, and who reviewed it

## Writing rules the generator follows

- Paragraph order:
  1. Introduction
  2. Patient
  3. Each suspect drug separately
  4. Events, tests and treatment in date order
  5. Closing: action taken, stop dates, dechallenge/rechallenge, outcomes, death
  6. Causality
  7. Associated case references
- Missing facts leave sentences out. "Not reported" is never written as "absent" or "normal".
- Partial dates stay partial ("in Sep 2026").
- Time to onset is calculated only from complete dates.
- Conflicting dates are flagged, not corrected.
- Source and company causality are shown separately. Only assessments a person recorded are used; AI drafts are left out.
- Clinical trial cases state that blinded treatment remains blinded.
- Literature cases name the article and keep the awareness date separate from the publication date.
- The company name comes from Administration › Company profile.

## Templates

Templates are managed under **Report templates › Narrative templates**, by the System Administrator, QPPV, Deputy QPPV or Group Head RA & Quality.

The editing syntax is:

- one sentence per line
- `{field}` inserts a fact from the case
- `[[ … ]]` marks an optional part, left out when its facts are missing
- `A || B` gives alternatives; the first one whose facts are all available is used

A change is saved as a draft. You can preview the draft on any case without saving anything.

A second authorised person must approve the draft before it is used. Existing narratives keep the template version they were made with. Version 1 of each template is the platform default.

## New ICSR fields (migration 057)

Migration 057 adds these fields to the ICSR form:

- Literature: authors, journal, year, title, DOI/PMID
- Study: subject/randomisation ID, blinded (yes/no)
- Patient: allergies, social history
