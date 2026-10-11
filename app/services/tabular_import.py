"""Read an uploaded CSV or Excel (.xlsx) table into rows of {header: text}.

Excel files are read with the standard library (an .xlsx file is a zip of
XML), so no extra package is needed. Only the first worksheet is read; empty
rows are skipped; every value is returned as text exactly as stored.
"""

import csv
import io
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_ROWS = 5000


class ImportFileError(ValueError):
    pass


def normalise_header(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def read_table(filename, data):
    name = (filename or "").lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        rows = _read_csv(data)
    elif name.endswith(".xlsx"):
        rows = _read_xlsx(data)
    elif name.endswith(".xls"):
        raise ImportFileError("Old .xls files cannot be read. Save the sheet as .xlsx or .csv and upload again.")
    else:
        raise ImportFileError("Upload a .csv or .xlsx file.")
    if not rows:
        raise ImportFileError("The file has no rows.")
    headers = [normalise_header(h) for h in rows[0]]
    if not any(headers):
        raise ImportFileError("The first row must contain column headings.")
    out = []
    for line_no, values in enumerate(rows[1:], start=2):
        if not any((v or "").strip() for v in values):
            continue
        record = {}
        for index, header in enumerate(headers):
            if header:
                record[header] = (values[index] if index < len(values) else "").strip()
        record["__line"] = line_no
        out.append(record)
        if len(out) > MAX_ROWS:
            raise ImportFileError(f"The file has more than {MAX_ROWS} rows. Split it into smaller files.")
    return headers, out


def _read_csv(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportFileError("The file is not a valid .xlsx workbook.")
    with archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet_path = _first_sheet(archive)
        root = ElementTree.fromstring(archive.read(sheet_path))
        date_styles = _date_styles(archive)
        rows = []
        for row in root.iter(f"{{{NS['m']}}}row"):
            values = {}
            for cell in row.findall("m:c", NS):
                ref = cell.get("r", "")
                column = _column_index(re.sub(r"\d", "", ref)) if ref else len(values)
                kind = cell.get("t")
                value_el = cell.find("m:v", NS)
                if kind == "s" and value_el is not None:
                    value = shared[int(value_el.text)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
                elif value_el is not None:
                    value = value_el.text or ""
                    if cell.get("s") and int(cell.get("s")) in date_styles and _is_number(value):
                        value = (date(1899, 12, 30) + timedelta(days=int(float(value)))).isoformat()
                    elif _is_number(value) and value.endswith(".0"):
                        value = value[:-2]
                else:
                    value = ""
                values[column] = value
            if values:
                width = max(values) + 1
                rows.append([values.get(i, "") for i in range(width)])
        return rows


def _first_sheet(archive):
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        first = workbook.find("m:sheets/m:sheet", NS)
        rel_id = first.get(REL_NS)
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            if rel.get("Id") == rel_id:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    except (KeyError, AttributeError, ElementTree.ParseError):
        pass
    sheets = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet"))
    if not sheets:
        raise ImportFileError("The workbook has no worksheet.")
    return sheets[0]


def _date_styles(archive):
    """Indexes of cell styles that format numbers as dates."""
    try:
        styles = ElementTree.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return set()
    custom = {}
    for fmt in styles.iter(f"{{{NS['m']}}}numFmt"):
        custom[int(fmt.get("numFmtId"))] = fmt.get("formatCode", "")
    builtin_dates = set(range(14, 23)) | {45, 46, 47}
    out = set()
    cell_xfs = styles.find("m:cellXfs", NS)
    for index, xf in enumerate(cell_xfs if cell_xfs is not None else []):
        fmt_id = int(xf.get("numFmtId", 0))
        code = custom.get(fmt_id, "")
        if fmt_id in builtin_dates or (code and re.search(r"[dy]", code, re.I) and not re.search(r"\[h\]|h:mm", code)):
            out.add(index)
    return out


def _column_index(letters):
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - 64)
    return index - 1


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
