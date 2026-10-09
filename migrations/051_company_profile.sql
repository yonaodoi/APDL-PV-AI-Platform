-- Company profile (makes the platform reusable for another company).
-- On the existing APDL database (it already holds safety cases) save APDL's
-- details as the company profile, so every page, report and email stays
-- exactly as before. A fresh installation for another company gets no row
-- and starts from neutral values until its administrator fills in
-- Administration > Company profile.
BEGIN;

INSERT INTO pv.app_settings (setting_key, value, updated_at)
SELECT 'company_profile', $json${
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
  "form_code": "SF/RA/012.2",
  "form_revision": "00",
  "form_effective_date": "31/08/2026",
  "sign_prepared_name": "AMEKO CHARLES",
  "sign_prepared_title": "DEPUTY Q.P.P.V.",
  "sign_reviewed_name": "YONA ODOI",
  "sign_reviewed_title": "Q.P.P.V.",
  "sign_authorised_name": "KEITH ARUHO",
  "sign_authorised_title": "GROUP HEAD, RA & QUALITY"
}$json$::jsonb, CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM pv.safety_cases)
ON CONFLICT (setting_key) DO NOTHING;

COMMIT;
