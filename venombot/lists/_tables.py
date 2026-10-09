"""Stdlib readers for spreadsheet-style list files (xlsx, ods) and zips.

Many official lists are published only as Excel/ODS. Rather than add a
dependency, these read the zipped XML directly. Legacy binary ``.xls`` is not
supported; use the OpenSanctions mirror of that list instead.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_COL = re.compile(r"([A-Z]+)")


def _col_index(ref: str) -> int:
    letters = _COL.match(ref).group(1)  # type: ignore[union-attr]
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheet_names(path: Path) -> List[str]:
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name", "") for s in wb.iter(f"{_NS_MAIN}sheet")]


def read_xlsx(path: Path, sheet: Optional[str] = None) -> Iterator[List[str]]:
    """Yield rows of one sheet as lists of strings (empty cells -> "").

    @param sheet sheet name; default is the first sheet
    """
    with zipfile.ZipFile(path) as z:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{_NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS_MAIN}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels}
        chosen = None
        for s in wb.iter(f"{_NS_MAIN}sheet"):
            if sheet is None or s.get("name") == sheet:
                chosen = targets.get(s.get(f"{_NS_REL}id"))
                break
        if not chosen:
            raise KeyError(f"sheet {sheet!r} not found in {path}")
        name = chosen.lstrip("/")
        if not name.startswith("xl/"):
            name = "xl/" + name
        with z.open(name) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != f"{_NS_MAIN}row":
                    continue
                cells: Dict[int, str] = {}
                for c in el.findall(f"{_NS_MAIN}c"):
                    t = c.get("t")
                    v = c.find(f"{_NS_MAIN}v")
                    if t == "s" and v is not None and v.text is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{_NS_MAIN}t"))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    cells[_col_index(c.get("r", "A1"))] = val.strip()
                el.clear()
                if cells:
                    width = max(cells) + 1
                    yield [cells.get(i, "") for i in range(width)]


_NS_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_NS_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def read_ods(path: Path, sheet: Optional[str] = None) -> Iterator[List[str]]:
    """Yield rows of an OpenDocument spreadsheet sheet as lists of strings."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    for tbl in root.iter(f"{_NS_TABLE}table"):
        if sheet is not None and tbl.get(f"{_NS_TABLE}name") != sheet:
            continue
        for row in tbl.iter(f"{_NS_TABLE}table-row"):
            out: List[str] = []
            for cell in row:
                if cell.tag not in (f"{_NS_TABLE}table-cell", f"{_NS_TABLE}covered-table-cell"):
                    continue
                repeat = min(int(cell.get(f"{_NS_TABLE}number-columns-repeated", "1")), 50)
                text = " ".join("".join(p.itertext()) for p in cell.findall(f"{_NS_TEXT}p")).strip()
                out.extend([text] * repeat)
            while out and not out[-1]:
                out.pop()
            if out:
                yield out
        if sheet is None:
            return


def rows_as_dicts(rows: Iterator[List[str]], header_row: int = 0) -> Iterator[Dict[str, str]]:
    """Turn row lists into dicts keyed by the header row (blank rows skipped).

    @param header_row zero-based index of the header among the yielded rows
    """
    header: Optional[List[str]] = None
    for i, row in enumerate(rows):
        if i < header_row:
            continue
        if header is None:
            header = [h.strip() for h in row]
            continue
        if not any(c for c in row):
            continue
        yield {h: (row[j] if j < len(row) else "") for j, h in enumerate(header) if h}


def unzip_first(path: Path, suffixes: tuple, dest: Path) -> Path:
    """Extract the first member ending in one of ``suffixes`` next to ``dest``."""
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.lower().endswith(suffixes):
                target = dest / Path(name).name
                with z.open(name) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                return target
    raise FileNotFoundError(f"no {suffixes} member in {path}")
