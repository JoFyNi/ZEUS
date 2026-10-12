"""Tests für datei_importieren mit Wissensdokumenten (PDF, TXT) und Ordnern.

Anlass: datei_importieren kannte nur Quellcode/Konfiguration und legte alles
- auch Fließtext - als eingezäunten Codeblock ab. PDFs gingen gar nicht.
Für vorhandenes Wissen (Berichte, Mitschriften, PDFs) ist das die falsche
Form: Diese Tests prüfen, dass PDF/TXT als lesbare, strukturierte Notiz
ankommen (Seiten-/Absatzüberschriften statt Codezaun), dass ein ganzer
Ordner auf einmal importiert werden kann, bereits importierte Dateien beim
zweiten Lauf übersprungen werden (kein Duplikat), und dass Quellcode sich
weiterhin unverändert verhält (Codeblock bleibt Codeblock).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_wissen_"))
VAULT = TMP / "Zeus"
QUELLEN = TMP / "Quellen"
VAULT.mkdir(parents=True)
QUELLEN.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import VaultTools  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 55>>stream
BT /F1 12 Tf 20 100 Td (Seite eins Testinhalt) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
trailer<</Size 6/Root 1 0 R>>
startxref
0
%%EOF"""


def main() -> int:
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    index.sync()
    tools = VaultTools(index)

    # ------------------------------------------------------------- 1. PDF
    pdf_pfad = QUELLEN / "Bericht.pdf"
    pdf_pfad.write_bytes(MINIMAL_PDF)
    r = tools.datei_importieren(pfad=str(pdf_pfad))
    check("PDF-Import gelingt (echte Extraktion via pypdf)", r.ok, r.meldung)
    if r.ok:
        note = tools.resolve_note("Bericht")
        inhalt = note.path.read_text(encoding="utf-8") if note else ""
        check("Seiten-Überschrift gesetzt", "## Seite 1" in inhalt, inhalt[:200])
        check("Extrahierter Text steht in der Notiz", "Testinhalt" in inhalt, inhalt[:400])
        check("Kein Codezaun um PDF-Text", "```pdf" not in inhalt and "```\n" not in inhalt)
        check("Quelldatei im Frontmatter vermerkt",
              note is not None and str(pdf_pfad) in note.frontmatter.get("quelldatei", ""))

    # ------------------------------------------------------------- 2. TXT
    txt_pfad = QUELLEN / "Mitschrift.txt"
    txt_pfad.write_text("Erster Absatz.\n\nZweiter Absatz, getrennt.\n", encoding="utf-8")
    r = tools.datei_importieren(pfad=str(txt_pfad))
    check("TXT-Import gelingt", r.ok, r.meldung)
    if r.ok:
        note = tools.resolve_note("Mitschrift")
        inhalt = note.path.read_text(encoding="utf-8") if note else ""
        check("TXT wird strukturiert, nicht in Codezaun gepackt", "```" not in inhalt, inhalt[:200])
        check("Originaltext vollständig enthalten (nichts erfunden/gekürzt)",
              "Erster Absatz." in inhalt and "Zweiter Absatz, getrennt." in inhalt)

    # -------------------------------------------- 3. Quellcode bleibt Codeblock
    py_pfad = QUELLEN / "skript.py"
    py_pfad.write_text("print('hallo')\n", encoding="utf-8")
    r = tools.datei_importieren(pfad=str(py_pfad))
    check("Python-Import gelingt (bestehendes Verhalten)", r.ok, r.meldung)
    if r.ok:
        note = tools.resolve_note("skript")
        inhalt = note.path.read_text(encoding="utf-8") if note else ""
        check("Quellcode bleibt im Codeblock", "```python" in inhalt, inhalt[:200])

    # -------------------------------------------------- 4. Ordner auf einmal
    ordner = QUELLEN / "Sammlung"
    ordner.mkdir()
    (ordner / "a.txt").write_text("Inhalt A\n", encoding="utf-8")
    (ordner / "b.md").write_text("# B\n\nInhalt B\n", encoding="utf-8")
    (ordner / "ignoriert.exe").write_bytes(b"\x00\x01binary")
    r = tools.datei_importieren(pfad=str(ordner))
    check("Ordner-Import gelingt", r.ok, r.meldung)
    check("Beide unterstützten Dateien importiert", len(r.daten.get("importiert", [])) == 2, r.daten)
    check("Nicht unterstützte Datei (.exe) übersprungen, nicht als Notiz angelegt",
          tools.resolve_note("ignoriert") is None)

    # -- Erneuter Lauf: keine Duplikate --
    r2 = tools.datei_importieren(pfad=str(ordner))
    check("Zweiter Ordner-Import überspringt bereits Importiertes (kein Duplikat)",
          len(r2.daten.get("importiert", [])) == 0 and len(r2.daten.get("uebersprungen", [])) == 2,
          r2.daten)

    # ---------------------------------------------------- 5. Fehlerfälle
    r = tools.datei_importieren(pfad=str(QUELLEN / "gibtsnicht.txt"))
    check("Nicht vorhandener Pfad -> sauberer Fehlschlag", not r.ok, r.meldung)

    leer_ordner = QUELLEN / "Leer"
    leer_ordner.mkdir()
    r = tools.datei_importieren(pfad=str(leer_ordner))
    check("Ordner ohne unterstützte Dateien -> klare Meldung statt stillem Nichtstun",
          not r.ok, r.meldung)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
