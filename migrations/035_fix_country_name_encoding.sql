BEGIN;

-- The original seed was loaded with the wrong client encoding, storing
-- "Côte d'Ivoire" as "CÃ´te d'Ivoire". The Unicode escape below is
-- independent of the client encoding used to run this migration.
UPDATE pv.countries
SET country_name = U&'C\00F4te d''Ivoire'
WHERE iso_alpha2 = 'CI'
  AND country_name <> U&'C\00F4te d''Ivoire';

COMMIT;
