"""Medical terminology.

Official dictionaries
  * MedDRA – loaded from the MSSO ASCII release files (llt.asc, pt.asc,
    soc.asc, mdhier.asc), uploaded as a .zip of the release folder or as the
    individual files. Only a licensed subscriber can obtain these files.
  * WHODrug – loaded from a licensed WHODrug extract saved as CSV/TSV/Excel
    with named columns (drug_code or drug_record_number + sequence_1 +
    sequence_2, drug_name, and optionally preferred, ingredients, atc,
    country, mah). The fixed-width B3 distribution files are not read
    directly; export them to a delimited file first.

Local terminology (clearly separate, identifiers start with LOCAL-)
  * Local event terms (pv.event_terms, LOCAL-E-nnnnn) – the platform's own
    controlled event list, also used to group cases for signal detection.
  * Local product terms (pv.local_product_terms, LOCAL-P-nnnnn) – medicines
    that are not in the company catalogue and cannot be coded to WHODrug.
  Local terms record who proposed and approved them and can later be mapped
  to an official term. They are never presented as MedDRA or WHODrug.
"""

import hashlib
import io
import json
import re
import zipfile
from difflib import SequenceMatcher

from app.db import query_all, query_one, transaction

MEDDRA_FILES = ("llt.asc", "pt.asc", "soc.asc", "mdhier.asc")
BATCH = 1000


class DictionaryImportError(ValueError):
    pass


def normalise(text):
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def dictionary_status(dictionary):
    """{'terms': n, 'version': ..., 'imported_at': ...} for the active release."""
    try:
        row = query_one(
            """
            SELECT version, imported_at, term_count AS terms, source_filename
            FROM pv.dictionary_imports
            WHERE dictionary = %s AND status = 'Active'
            ORDER BY imported_at DESC LIMIT 1
            """,
            (dictionary,),
        )
    except Exception:
        _rollback()
        return {}
    return dict(row) if row else {}


def active_version(dictionary):
    return dictionary_status(dictionary).get("version")


def import_history(limit=20):
    try:
        return query_all(
            """
            SELECT d.*, u.full_name AS imported_by_name
            FROM pv.dictionary_imports AS d
            LEFT JOIN pv.users AS u ON u.user_id = d.imported_by
            ORDER BY d.imported_at DESC LIMIT %s
            """,
            (limit,),
        )
    except Exception:
        _rollback()
        return []


def _rollback():
    try:
        from app.db import get_db

        get_db().rollback()
    except Exception:
        pass


# --------------------------------------------------------------------------
# MedDRA import
# --------------------------------------------------------------------------

def _decode(data):
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def collect_meddra_files(uploads):
    """{'llt.asc': text, ...} from uploaded files and/or a zip of the release."""
    found = {}
    for filename, data in uploads:
        name = filename.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if name.endswith(".zip"):
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise DictionaryImportError(f"{filename} is not a valid zip file.")
            for member in archive.namelist():
                base = member.lower().rsplit("/", 1)[-1]
                if base in MEDDRA_FILES or base == "meddra_release.asc":
                    found[base] = _decode(archive.read(member))
        elif name in MEDDRA_FILES or name == "meddra_release.asc":
            found[name] = _decode(data)
    missing = [f for f in MEDDRA_FILES if f not in found]
    if missing:
        raise DictionaryImportError(
            "These MedDRA release files were not found: " + ", ".join(missing)
            + ". Upload the zip of the MedDRA ASCII release (folder 'MedAscii') or the files themselves."
        )
    return found


def _rows(text):
    for line in text.splitlines():
        line = line.strip("\r\n")
        if line:
            yield line.split("$")


def parse_meddra(files):
    llt = [(r[0], r[1], r[2], (r[9] if len(r) > 9 else "Y") != "N") for r in _rows(files["llt.asc"]) if len(r) >= 3]
    pt = [(r[0], r[1], r[3] if len(r) > 3 else None) for r in _rows(files["pt.asc"]) if len(r) >= 2]
    soc = [(r[0], r[1], r[2] if len(r) > 2 else None) for r in _rows(files["soc.asc"]) if len(r) >= 2]
    hier = [(r[0], r[1], r[5], r[2], r[6], r[3], r[7], (r[11] if len(r) > 11 else "N") == "Y")
            for r in _rows(files["mdhier.asc"]) if len(r) >= 8]
    if not llt or not pt:
        raise DictionaryImportError("The MedDRA files are empty or not in the MedDRA ASCII format ($-separated).")
    if not all(c.isdigit() for c, *_ in llt[:50]):
        raise DictionaryImportError("llt.asc does not look like a MedDRA file (codes must be numbers).")
    version = None
    if files.get("meddra_release.asc"):
        first = next(_rows(files["meddra_release.asc"]), None)
        if first and re.match(r"^\d+(\.\d+)?$", first[0].strip()):
            version = first[0].strip()
    return {"llt": llt, "pt": pt, "soc": soc, "hierarchy": hier, "version": version}


def _insert_many(cur, table, columns, rows):
    rows = list(rows)
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        placeholders = ", ".join("(" + ", ".join(["%s"] * len(columns)) + ")" for _ in chunk)
        params = [value for row in chunk for value in row]
        cur.execute(f"INSERT INTO pv.{table} ({', '.join(columns)}) VALUES {placeholders}", params)


def import_meddra(uploads, version, user_id, filenames="", language="English"):
    parsed = parse_meddra(collect_meddra_files(uploads))
    version = (version or parsed["version"] or "").strip()
    if not re.match(r"^\d{1,2}\.\d$", version):
        raise DictionaryImportError("Enter the MedDRA version of these files, e.g. 29.0.")
    digest = hashlib.sha256(b"".join(d for _, d in uploads)).hexdigest()
    with transaction() as cur:
        for table in ("meddra_llt", "meddra_pt", "meddra_soc", "meddra_hierarchy"):
            cur.execute(f"DELETE FROM pv.{table} WHERE version = %s", (version,))
        _insert_many(cur, "meddra_llt", ["version", "llt_code", "llt_name", "pt_code", "is_current"],
                     [(version, *r) for r in parsed["llt"]])
        _insert_many(cur, "meddra_pt", ["version", "pt_code", "pt_name", "primary_soc_code"],
                     [(version, *r) for r in parsed["pt"]])
        _insert_many(cur, "meddra_soc", ["version", "soc_code", "soc_name", "soc_abbrev"],
                     [(version, *r) for r in parsed["soc"]])
        _insert_many(cur, "meddra_hierarchy",
                     ["version", "pt_code", "hlt_code", "hlt_name", "hlgt_code", "hlgt_name", "soc_code", "soc_name", "is_primary"],
                     [(version, *r) for r in parsed["hierarchy"]])
        cur.execute("UPDATE pv.dictionary_imports SET status = 'Superseded' WHERE dictionary = 'MedDRA' AND status = 'Active'")
        cur.execute(
            "INSERT INTO pv.dictionary_imports (dictionary, version, language, source_filename, file_sha256, term_count, notes, imported_by) "
            "VALUES ('MedDRA', %s, %s, %s, %s, %s, %s, %s)",
            (version, language, filenames[:255], digest, len(parsed["llt"]),
             f"{len(parsed['llt'])} LLTs, {len(parsed['pt'])} PTs, {len(parsed['soc'])} SOCs.", user_id),
        )
    return version, len(parsed["llt"]), len(parsed["pt"])


# --------------------------------------------------------------------------
# WHODrug import (licensed extract with named columns)
# --------------------------------------------------------------------------

WHODRUG_ALIASES = {
    "drug_code": ["drug_code", "drugcode", "whodrug_code", "code"],
    "drug_record_number": ["drug_record_number", "drecno", "drug_record_no", "record_number"],
    "sequence_1": ["sequence_1", "seq1", "sequence_number_1"],
    "sequence_2": ["sequence_2", "seq2", "sequence_number_2"],
    "drug_name": ["drug_name", "drugname", "trade_name", "name", "medicinal_product"],
    "is_preferred": ["preferred", "is_preferred", "preferred_name", "preferred_flag"],
    "ingredients": ["ingredients", "active_ingredients", "substances"],
    "atc_codes": ["atc", "atc_codes", "atc_code"],
    "country": ["country", "country_code"],
    "marketing_holder": ["mah", "marketing_authorisation_holder", "marketing_authorization_holder", "company"],
}


def import_whodrug(filename, data, version, user_id):
    from app.services.tabular_import import read_table

    version = (version or "").strip()
    if not version:
        raise DictionaryImportError("Enter the WHODrug release, e.g. 2026 Mar 1 B3.")
    headers, rows = read_table(filename, data) if not filename.lower().endswith(".tsv") else read_table(filename[:-4] + ".csv", data)
    mapping = {}
    for field, aliases in WHODRUG_ALIASES.items():
        for alias in aliases:
            if alias in headers:
                mapping[field] = alias
                break
    if "drug_name" not in mapping or not ("drug_code" in mapping or "drug_record_number" in mapping):
        raise DictionaryImportError("The file needs a drug_name column and either drug_code or drug_record_number (+ sequence_1, sequence_2).")
    out, seen = [], set()
    for raw in rows:
        get = lambda f: (raw.get(mapping[f], "") if f in mapping else "").strip()
        code = get("drug_code")
        if not code:
            drn, s1, s2 = get("drug_record_number"), get("sequence_1") or "01", get("sequence_2") or "001"
            if not drn:
                continue
            code = f"{drn.zfill(6)}{s1.zfill(2)}{s2.zfill(3)}"
        name = get("drug_name")
        if not name or code in seen:
            continue
        seen.add(code)
        preferred = get("is_preferred").lower() in ("y", "yes", "true", "1", "preferred")
        out.append((version, code, name[:500], get("drug_record_number") or code[:6] or None,
                    get("sequence_1") or None, get("sequence_2") or None, preferred,
                    get("ingredients") or None, get("atc_codes")[:255] or None, get("country")[:100] or None,
                    get("marketing_holder")[:255] or None))
    if not out:
        raise DictionaryImportError("No WHODrug records were found in the file.")
    digest = hashlib.sha256(data).hexdigest()
    with transaction() as cur:
        cur.execute("DELETE FROM pv.whodrug_products WHERE version = %s", (version,))
        _insert_many(cur, "whodrug_products",
                     ["version", "drug_code", "drug_name", "drug_record_number", "sequence_1", "sequence_2",
                      "is_preferred", "ingredients", "atc_codes", "country", "marketing_holder"], out)
        cur.execute("UPDATE pv.dictionary_imports SET status = 'Superseded' WHERE dictionary = 'WHODrug' AND status = 'Active'")
        cur.execute(
            "INSERT INTO pv.dictionary_imports (dictionary, version, source_filename, file_sha256, term_count, imported_by) "
            "VALUES ('WHODrug', %s, %s, %s, %s, %s)",
            (version, filename[:255], digest, len(out), user_id),
        )
    return version, len(out)


# --------------------------------------------------------------------------
# Searching
# --------------------------------------------------------------------------

def _score(query, name):
    return round(SequenceMatcher(None, normalise(query), normalise(name)).ratio(), 3)


def _like_terms(text):
    words = [w for w in normalise(text).split() if len(w) >= 3][:4]
    return words or [normalise(text)]


def meddra_exact(text, version=None):
    version = version or active_version("MedDRA")
    if not version or not normalise(text):
        return None
    row = query_one(
        """
        SELECT l.llt_code, l.llt_name, l.pt_code, p.pt_name, p.primary_soc_code, s.soc_name, l.version
        FROM pv.meddra_llt AS l
        JOIN pv.meddra_pt AS p ON p.version = l.version AND p.pt_code = l.pt_code
        LEFT JOIN pv.meddra_soc AS s ON s.version = l.version AND s.soc_code = p.primary_soc_code
        WHERE l.version = %s AND LOWER(l.llt_name) = LOWER(%s) AND l.is_current
        LIMIT 1
        """,
        (version, text.strip()),
    )
    return _meddra_candidate(row, "Exact", 1.0) if row else None


def meddra_by_code(llt_code, version=None):
    version = version or active_version("MedDRA")
    row = query_one(
        """
        SELECT l.llt_code, l.llt_name, l.pt_code, p.pt_name, p.primary_soc_code, s.soc_name, l.version
        FROM pv.meddra_llt AS l
        JOIN pv.meddra_pt AS p ON p.version = l.version AND p.pt_code = l.pt_code
        LEFT JOIN pv.meddra_soc AS s ON s.version = l.version AND s.soc_code = p.primary_soc_code
        WHERE l.version = %s AND l.llt_code = %s
        """,
        (version, str(llt_code)),
    )
    return _meddra_candidate(row, "Selected", 1.0) if row else None


def meddra_search(text, limit=8, version=None):
    version = version or active_version("MedDRA")
    if not version or not normalise(text):
        return []
    words = _like_terms(text)
    conditions = " AND ".join(["LOWER(l.llt_name) LIKE %s"] * len(words))
    rows = query_all(
        f"""
        SELECT l.llt_code, l.llt_name, l.pt_code, p.pt_name, p.primary_soc_code, s.soc_name, l.version
        FROM pv.meddra_llt AS l
        JOIN pv.meddra_pt AS p ON p.version = l.version AND p.pt_code = l.pt_code
        LEFT JOIN pv.meddra_soc AS s ON s.version = l.version AND s.soc_code = p.primary_soc_code
        WHERE l.version = %s AND l.is_current AND {conditions}
        LIMIT 200
        """,
        tuple([version] + [f"%{w}%" for w in words]),
    )
    if not rows and len(words) > 1:
        rows = query_all(
            """
            SELECT l.llt_code, l.llt_name, l.pt_code, p.pt_name, p.primary_soc_code, s.soc_name, l.version
            FROM pv.meddra_llt AS l
            JOIN pv.meddra_pt AS p ON p.version = l.version AND p.pt_code = l.pt_code
            LEFT JOIN pv.meddra_soc AS s ON s.version = l.version AND s.soc_code = p.primary_soc_code
            WHERE l.version = %s AND l.is_current AND LOWER(l.llt_name) LIKE ANY(%s)
            LIMIT 300
            """,
            (version, [f"%{w}%" for w in words]),
        )
    fuzzy = False
    if not rows:
        # Misspelt wording ("uticaria"): look for word fragments, then rank by similarity.
        fuzzy = True
        rows = query_all(
            """
            SELECT l.llt_code, l.llt_name, l.pt_code, p.pt_name, p.primary_soc_code, s.soc_name, l.version
            FROM pv.meddra_llt AS l
            JOIN pv.meddra_pt AS p ON p.version = l.version AND p.pt_code = l.pt_code
            LEFT JOIN pv.meddra_soc AS s ON s.version = l.version AND s.soc_code = p.primary_soc_code
            WHERE l.version = %s AND l.is_current AND LOWER(l.llt_name) LIKE ANY(%s)
            LIMIT 3000
            """,
            (version, _fragments(text)),
        )
    candidates = [_meddra_candidate(r, "Similar spelling" if fuzzy else "Dictionary search", _score(text, r["llt_name"])) for r in rows]
    if fuzzy:
        candidates = [c for c in candidates if c["score"] >= 0.6]
    candidates.sort(key=lambda c: (-c["score"], len(c["term"])))
    return candidates[:limit]


def _fragments(text):
    """LIKE patterns from word fragments, for wording with spelling mistakes."""
    out = set()
    for word in normalise(text).split():
        if len(word) >= 5:
            out.update({f"%{word[:3]}%", f"%{word[-4:]}%", f"%{word[len(word) // 2 - 2:len(word) // 2 + 2]}%"})
        elif len(word) >= 3:
            out.add(f"%{word}%")
    return sorted(out) or [f"%{normalise(text)}%"]


def _meddra_candidate(row, method, score):
    return {
        "dictionary": "MedDRA", "code": row["llt_code"], "term": row["llt_name"],
        "pt_code": row["pt_code"], "pt_name": row["pt_name"],
        "soc_code": row.get("primary_soc_code"), "soc_name": row.get("soc_name"),
        "version": row["version"], "method": method, "score": score,
    }


def whodrug_search(text, limit=8, version=None):
    version = version or active_version("WHODrug")
    if not version or not normalise(text):
        return []
    words = _like_terms(text)
    conditions = " AND ".join(["LOWER(drug_name) LIKE %s"] * len(words))
    rows = query_all(
        f"""
        SELECT drug_code, drug_name, is_preferred, ingredients, atc_codes, version
        FROM pv.whodrug_products
        WHERE version = %s AND {conditions}
        ORDER BY is_preferred DESC
        LIMIT 200
        """,
        tuple([version] + [f"%{w}%" for w in words]),
    )
    fuzzy = False
    if not rows:
        fuzzy = True
        rows = query_all(
            """
            SELECT drug_code, drug_name, is_preferred, ingredients, atc_codes, version
            FROM pv.whodrug_products
            WHERE version = %s AND LOWER(drug_name) LIKE ANY(%s)
            LIMIT 3000
            """,
            (version, _fragments(text)),
        )
    candidates = [{
        "dictionary": "WHODrug", "code": r["drug_code"], "term": r["drug_name"],
        "pt_code": None, "pt_name": r.get("ingredients"), "soc_code": r.get("atc_codes"), "soc_name": None,
        "version": r["version"], "method": "Exact" if normalise(r["drug_name"]) == normalise(text) else "Dictionary search",
        "score": 1.0 if normalise(r["drug_name"]) == normalise(text) else _score(text, r["drug_name"]),
        "preferred": r.get("is_preferred"),
    } for r in rows]
    if fuzzy:
        candidates = [dict(c, method="Similar spelling") for c in candidates if c["score"] >= 0.6]
    candidates.sort(key=lambda c: (-c["score"], not c.get("preferred"), len(c["term"])))
    return candidates[:limit]


def whodrug_by_code(code, version=None):
    version = version or active_version("WHODrug")
    row = query_one(
        "SELECT drug_code, drug_name, ingredients, atc_codes, version FROM pv.whodrug_products WHERE version = %s AND drug_code = %s",
        (version, code),
    )
    if not row:
        return None
    return {"dictionary": "WHODrug", "code": row["drug_code"], "term": row["drug_name"], "pt_code": None,
            "pt_name": row.get("ingredients"), "soc_code": row.get("atc_codes"), "soc_name": None,
            "version": row["version"], "method": "Selected", "score": 1.0}


def synonym_lookup(dictionary, wording):
    row = query_one("SELECT * FROM pv.coding_synonyms WHERE dictionary = %s AND wording = %s",
                    (dictionary, normalise(wording)))
    return dict(row) if row else None


def remember_synonym(dictionary, wording, code, term, version, user_id):
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO pv.coding_synonyms (dictionary, wording, code, term, version, added_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (dictionary, wording) DO UPDATE
            SET code = EXCLUDED.code, term = EXCLUDED.term, version = EXCLUDED.version,
                added_by = EXCLUDED.added_by, added_at = NOW()
            """,
            (dictionary, normalise(wording), code, term, version, user_id),
        )


# --------------------------------------------------------------------------
# Local terminology
# --------------------------------------------------------------------------

def local_event_search(text, limit=6):
    rows = query_all(
        """
        SELECT term_id, local_code, preferred_term, system_organ_class, status, mapped_code, mapped_term
        FROM pv.event_terms WHERE is_active AND status <> 'Rejected'
        """
    )
    synonyms = {normalise(r["synonym"]): r["term_id"] for r in query_all("SELECT synonym, term_id FROM pv.event_term_synonyms")}
    target = normalise(text)
    by_id = {r["term_id"]: r for r in rows}
    out = []
    for r in rows:
        score = 1.0 if normalise(r["preferred_term"]) == target else _score(text, r["preferred_term"])
        out.append(_local_event_candidate(r, "Exact" if score == 1.0 else "Local search", score))
    if target in synonyms and synonyms[target] in by_id:
        out.append(_local_event_candidate(by_id[synonyms[target]], "Synonym", 0.99))
    out.sort(key=lambda c: -c["score"])
    seen, unique = set(), []
    for c in out:
        if c["code"] not in seen:
            seen.add(c["code"])
            unique.append(c)
    return [c for c in unique if c["score"] >= 0.55][:limit]


def _local_event_candidate(row, method, score):
    return {
        "dictionary": "Local", "code": row["local_code"], "term": row["preferred_term"], "local_id": row["term_id"],
        "pt_code": None, "pt_name": None, "soc_code": None, "soc_name": row.get("system_organ_class"),
        "version": "local", "method": method, "score": score, "status": row.get("status"),
        "mapped": row.get("mapped_term"),
    }


def local_product_search(text, limit=6):
    rows = query_all("SELECT * FROM pv.local_product_terms WHERE status <> 'Rejected'")
    out = []
    for r in rows:
        score = 1.0 if normalise(r["name"]) == normalise(text) else _score(text, r["name"])
        if score >= 0.6:
            out.append({"dictionary": "Local", "code": r["local_code"], "term": r["name"], "local_id": r["local_product_id"],
                        "pt_code": None, "pt_name": r.get("ingredients"), "soc_code": None, "soc_name": None,
                        "version": "local", "method": "Exact" if score == 1.0 else "Local search", "score": score,
                        "status": r["status"], "mapped": r.get("mapped_term")})
    out.sort(key=lambda c: -c["score"])
    return out[:limit]


def propose_local_event(term, source_wording, user_id, system_organ_class=None):
    """A new local event term, waiting for approval. Returns its candidate."""
    term = re.sub(r"\s+", " ", (term or "")).strip()[:255]
    if not term:
        raise ValueError("Give the local term a name.")
    existing = query_one("SELECT * FROM pv.event_terms WHERE LOWER(preferred_term) = LOWER(%s)", (term,))
    if existing:
        return _local_event_candidate(existing, "Local search", 1.0)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO pv.event_terms (preferred_term, system_organ_class, status, source_wording, created_by) "
            "VALUES (%s, %s, 'Proposed', %s, %s) RETURNING term_id",
            (term, system_organ_class, source_wording, user_id),
        )
        term_id = cur.fetchone()["term_id"]
        cur.execute("UPDATE pv.event_terms SET local_code = %s WHERE term_id = %s", (f"LOCAL-E-{term_id:05d}", term_id))
        cur.execute("SELECT * FROM pv.event_terms WHERE term_id = %s", (term_id,))
        return _local_event_candidate(cur.fetchone(), "Local proposal", 0.0)


def propose_local_product(name, source_wording, user_id, ingredients=None):
    name = re.sub(r"\s+", " ", (name or "")).strip()[:500]
    if not name:
        raise ValueError("Give the local product a name.")
    existing = query_one("SELECT * FROM pv.local_product_terms WHERE LOWER(name) = LOWER(%s)", (name,))
    if existing:
        row = existing
    else:
        with transaction() as cur:
            cur.execute(
                "INSERT INTO pv.local_product_terms (name, ingredients, source_wording, created_by) VALUES (%s, %s, %s, %s) "
                "RETURNING local_product_id",
                (name, ingredients, source_wording, user_id),
            )
            pid = cur.fetchone()["local_product_id"]
            cur.execute("UPDATE pv.local_product_terms SET local_code = %s WHERE local_product_id = %s", (f"LOCAL-P-{pid:05d}", pid))
            cur.execute("SELECT * FROM pv.local_product_terms WHERE local_product_id = %s", (pid,))
            row = cur.fetchone()
    return {"dictionary": "Local", "code": row["local_code"], "term": row["name"], "local_id": row["local_product_id"],
            "pt_code": None, "pt_name": row.get("ingredients"), "soc_code": None, "soc_name": None,
            "version": "local", "method": "Local proposal", "score": 0.0, "status": row["status"]}


def local_terms(kind, status=None):
    if kind == "event":
        sql = "SELECT e.*, u.full_name AS created_by_name, a.full_name AS approved_by_name FROM pv.event_terms e " \
              "LEFT JOIN pv.users u ON u.user_id = e.created_by LEFT JOIN pv.users a ON a.user_id = e.approved_by"
        order = " ORDER BY (e.status = 'Proposed') DESC, e.preferred_term"
    else:
        sql = "SELECT p.*, u.full_name AS created_by_name, a.full_name AS approved_by_name FROM pv.local_product_terms p " \
              "LEFT JOIN pv.users u ON u.user_id = p.created_by LEFT JOIN pv.users a ON a.user_id = p.approved_by"
        order = " ORDER BY (p.status = 'Proposed') DESC, p.name"
    if status:
        alias = "e" if kind == "event" else "p"
        return query_all(sql + f" WHERE {alias}.status = %s" + order, (status,))
    return query_all(sql + order)


def review_local_term(kind, local_id, decision, user_id, mapping=None):
    """Approve or reject a proposed local term, or map it to an official term."""
    table, key = ("event_terms", "term_id") if kind == "event" else ("local_product_terms", "local_product_id")
    with transaction() as cur:
        if decision in ("Approved", "Rejected"):
            cur.execute(f"UPDATE pv.{table} SET status = %s, approved_by = %s, approved_at = NOW() WHERE {key} = %s",
                        (decision, user_id, local_id))
        if mapping:
            cur.execute(
                f"UPDATE pv.{table} SET mapped_dictionary = %s, mapped_code = %s, mapped_term = %s, mapped_version = %s WHERE {key} = %s",
                (mapping["dictionary"], mapping["code"], mapping["term"], mapping["version"], local_id),
            )
        cur.execute(f"SELECT * FROM pv.{table} WHERE {key} = %s", (local_id,))
        row = cur.fetchone()
        # Case codings that point at this local term follow its decision.
        if row and decision == "Approved":
            cur.execute(
                "UPDATE pv.term_codings SET status = 'Coded', message = NULL "
                "WHERE dictionary = 'Local' AND code = %s AND status = 'Needs review' AND method IN ('Manual', 'Local proposal') "
                "RETURNING case_id, entity, verbatim",
                (row["local_code"],),
            )
            if kind == "event":
                # Signal detection groups cases by the approved local event terms.
                for coded in cur.fetchall():
                    if coded["entity"] == "reaction":
                        cur.execute(
                            """
                            INSERT INTO pv.case_event_terms (case_id, verbatim_term, term_id, coding_method)
                            VALUES (%s, %s, %s, 'Manual')
                            ON CONFLICT (case_id, verbatim_term) DO UPDATE SET term_id = EXCLUDED.term_id, coding_method = 'Manual'
                            """,
                            (coded["case_id"], coded["verbatim"][:500], local_id),
                        )
        elif row and decision == "Rejected":
            cur.execute(
                "UPDATE pv.term_codings SET status = 'Needs review', "
                "message = 'The local term was rejected; choose another term.' "
                "WHERE dictionary = 'Local' AND code = %s",
                (row["local_code"],),
            )
        return row
