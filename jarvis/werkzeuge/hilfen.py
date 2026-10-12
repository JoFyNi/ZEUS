"""Textarbeit ohne Zustand: Abschnitte finden, Frontmatter setzen, PDF lesen.

Reine Funktionen. Keine davon kennt die Vault, keine schreibt etwas -
sie bekommen Text und geben Text zurueck. Genau deshalb sind sie einzeln
pruefbar, und genau deshalb stehen sie nicht als Methoden in der Klasse.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..vault import (
    fold,
    safe_filename,
    similarity,
)


# Welche Dateien importiert werden dürfen und wie sie im Codeblock ausgezeichnet werden
IMPORT_ENDUNGEN = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql", ".r", ".m",
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".log", ".xml", ".html", ".css", ".env", ".st", ".scl",
    ".pdf",
}
# Dateien, die FLIESSTEXT enthalten und deshalb nicht in einen Codeblock
# gehören. Der Unterschied ist nicht kosmetisch: Was im Codezaun steht, wird
# vom Index als ein einziger Klotz behandelt und findet sich später nicht
# wieder - eine importierte Vorlesungsmitschrift war damit unauffindbar.
PROSA_ENDUNGEN = {".txt", ".log"}
SPRACH_ENDUNGEN = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".sh": "bash", ".ps1": "powershell", ".bat": "batch",
    ".sql": "sql", ".r": "r", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".csv": "csv", ".tsv": "csv",
    ".xml": "xml", ".html": "html", ".css": "css", ".log": "text",
    ".st": "iecst", ".scl": "iecst",
}


_LISTENZEILE_RE = re.compile(r"^(\s*[-*+]\s+)(?:\[( |x|X)\]\s*)?(.*)$")
_CHECKBOX_RE = re.compile(r"^(\s*[-*+]\s+)\[( |x|X)\]\s*")


def _punkttext(zeile: str) -> str:
    """Der reine Text eines Listenpunkts - ohne Marke, ohne Checkbox."""
    treffer = _LISTENZEILE_RE.match(zeile or "")
    return treffer.group(3).strip() if treffer else (zeile or "").strip()


def _abschnittsnamen(zeilen: Sequence[str]) -> List[str]:
    return [z.strip().lstrip("#").strip() for z in zeilen
            if re.match(r"^#{1,6}\s+\S", z.strip())]


def _abschnittsgrenzen(zeilen: Sequence[str], abschnitt: str):
    """Von welcher bis zu welcher Zeile reicht der Abschnitt? None = gibt es nicht."""
    gesucht = fold(str(abschnitt).strip())
    anfang = None
    ebene = 0
    for i, zeile in enumerate(zeilen):
        treffer = re.match(r"^(#{1,6})\s+(.*)$", zeile.strip())
        if not treffer:
            continue
        if anfang is None:
            if fold(treffer.group(2).strip()) == gesucht:
                anfang, ebene = i + 1, len(treffer.group(1))
        elif len(treffer.group(1)) <= ebene:
            return (anfang, i)
    return (anfang, len(zeilen)) if anfang is not None else None


def _listenbereich(zeilen: Sequence[str], abschnitt: str = ""):
    """Erste und letzte Zeile der Liste - im Abschnitt, sonst der ersten der Notiz.

    Gesucht wird ein zusammenhängender Block von Listenzeilen. Eine Leerzeile
    MITTEN in einer Liste beendet sie nicht (Obsidian rendert das weiterhin als
    eine Liste), zwei aufeinanderfolgende schon.
    """
    if abschnitt:
        grenzen = _abschnittsgrenzen(zeilen, abschnitt)
        if grenzen is None:
            return None
        von, bis = grenzen
    else:
        von, bis = 0, len(zeilen)

    erste = None
    letzte = None
    leer = 0
    for i in range(von, min(bis, len(zeilen))):
        if _LISTENZEILE_RE.match(zeilen[i]):
            if erste is None:
                erste = i
            letzte = i
            leer = 0
        elif erste is not None:
            if not zeilen[i].strip():
                leer += 1
                if leer >= 2:
                    break
                continue
            break
    return (erste, letzte) if erste is not None else None


# ------------------------------------------------------------------- PDF
def _pypdf_version() -> str:
    try:
        import pypdf
        return str(getattr(pypdf, "__version__", "unbekannt"))
    except ImportError:
        return "nicht installiert"


def _pdf_als_text(quelle: Path):
    """Text aus einem PDF, Seite für Seite. Rückgabe: (inhalt, fehler).

    Seitenweise mit Überschrift, weil ein PDF-Import ohne Seitengrenzen
    unbrauchbar ist: Man findet die Stelle, kann sie aber im Original nicht
    wiederfinden. Kein Codezaun - es ist Fließtext, kein Quelltext.

    pypdf ist optional. Fehlt es, sagt die Meldung, was zu tun ist, statt
    einen ImportError durchzureichen - das ist die Windows-Lehre aus
    ENTWICKLUNG.md 2.6: Ein fehlendes Paket ist eine Auskunft, kein Absturz.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ("PDF-Import braucht das Paket 'pypdf'. Installieren mit: "
                    "pip install pypdf")
    try:
        leser = PdfReader(str(quelle))
    except Exception as exc:
        # Haeufigste Ursache: eine beschaedigte xref-Tabelle, wie sie Scanner
        # und Folienexporte laufend erzeugen. pypdf ab 5.0 baut sie neu auf,
        # aeltere Fassungen brechen ab - das gehoert in die Meldung, sonst
        # sucht man den Fehler im PDF statt in der Installation.
        return "", (f"PDF nicht lesbar: {type(exc).__name__}: {exc}. "
                    f"Installierte pypdf-Fassung: {_pypdf_version()}. Bei "
                    f"beschaedigten PDFs hilft: pip install -U \"pypdf>=5.0\"")

    if getattr(leser, "is_encrypted", False):
        try:
            leser.decrypt("")          # viele PDFs sind nur pro forma verschlüsselt
        except Exception:
            return "", "PDF ist passwortgeschützt - nicht importierbar."

    seiten = []
    for nummer, seite in enumerate(leser.pages, 1):
        try:
            text = seite.extract_text() or ""
        except Exception as exc:
            text = f"(Seite nicht lesbar: {type(exc).__name__})"
            _ = exc
        text = "\n".join(z.rstrip() for z in text.splitlines()).strip()
        if text:
            seiten.append(f"## Seite {nummer}\n\n{text}")

    if not seiten:
        return "", ("Aus dem PDF ließ sich kein Text holen - vermutlich ein Scan "
                    "ohne Texterkennung. Nichts angelegt, statt eine leere Notiz "
                    "zu hinterlassen.")
    return (f"Importiert aus `{quelle}`\n\n" + "\n\n".join(seiten) + "\n"), ""


# --------------------------------------------------------------- Text-Helfer
def _kurz_wert(wert, grenze: int = 120) -> str:
    """Parameter fürs Protokoll kürzen - ein 60-kB-Inhalt gehört nicht hinein."""
    text = str(wert)
    return text if len(text) <= grenze else text[:grenze] + f"… ({len(text)} Zeichen)"


def safe_path_fragment(value: str) -> str:
    parts = [safe_filename(p) for p in re.split(r"[\\/]+", str(value)) if p and p not in (".", "..")]
    return "/".join(parts)


def _rename_wikilinks(raw: str, alt: str, neu: str) -> str:
    """[[Alt]], [[Alt|Text]] und [[Alt#Abschnitt]] auf den neuen Namen umbiegen."""
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        if inner.strip().lower() != alt.strip().lower():
            return match.group(0)
        return "[[" + neu + match.group(2)

    return re.sub(r"\[\[([^\]\|#]+)((?:#[^\]\|]+)?(?:\|[^\]]+)?\]\])", repl, raw)


def _insert_into_section(raw: str, ueberschrift: str, zeile: str) -> Tuple[str, bool]:
    """Fügt eine Zeile ans Ende des passenden Abschnitts ein."""
    lines = raw.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", line)
        if match and similarity(match.group(2), ueberschrift) > 0.85:
            start, level = i, len(match.group(1))
            break
    if start is None:
        return raw, False
    end = len(lines)
    for i in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[i])
        if match and len(match.group(1)) <= level:
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, zeile)
    return "\n".join(lines) + ("\n" if raw.endswith("\n") else ""), True


def _im_ordner(ordner: str, gesucht: str) -> bool:
    if not gesucht:
        return True
    return ordner == gesucht or ordner.startswith(gesucht.rstrip("/") + "/")


def _abschnitt_index(zeilen: Sequence[str], ueberschrift: str) -> Optional[int]:
    """Zeilennummer der passenden Überschrift - tolerant gegen Tippfehler."""
    for i, zeile in enumerate(zeilen):
        treffer = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", zeile)
        if treffer and similarity(treffer.group(2), ueberschrift) > 0.85:
            return i
    return None


def _abschnitt_ende(zeilen: Sequence[str], start: int) -> int:
    """Ende eines Abschnitts: die nächste Überschrift gleicher oder höherer Ebene."""
    treffer = re.match(r"^(#{1,6})\s+", zeilen[start])
    ebene = len(treffer.group(1)) if treffer else 1
    for i in range(start + 1, len(zeilen)):
        naechste = re.match(r"^(#{1,6})\s+", zeilen[i])
        if naechste and len(naechste.group(1)) <= ebene:
            return i
    return len(zeilen)


def _abschnitt_ersetzen(raw: str, ueberschrift: str, inhalt: str) -> Tuple[str, bool]:
    zeilen = raw.splitlines()
    start = _abschnitt_index(zeilen, ueberschrift)
    if start is None:
        return raw, False
    ende = _abschnitt_ende(zeilen, start)
    neu = zeilen[: start + 1] + ["", *inhalt.splitlines(), ""] + zeilen[ende:]
    return "\n".join(neu).rstrip() + "\n", True


def _set_frontmatter_wert(raw: str, schluessel: str, wert: str) -> str:
    """Einfaches Schlüssel-Wert-Feld im Frontmatter setzen oder anlegen."""
    zeile = f"{schluessel}: {wert}"
    treffer = re.match(r"\A(---\s*\n)(.*?)(\n---\s*\n)", raw, re.DOTALL)
    if not treffer:
        return f"---\n{zeile}\n---\n\n" + raw.lstrip()
    innen = treffer.group(2)
    muster = re.compile(rf"^{re.escape(schluessel)}\s*:.*$", re.MULTILINE)
    innen_neu = muster.sub(zeile, innen) if muster.search(innen) else innen.rstrip() + "\n" + zeile
    return treffer.group(1) + innen_neu.rstrip() + treffer.group(3) + raw[treffer.end():]


def _set_frontmatter_list(raw: str, key: str, values: Sequence[str]) -> str:
    block = f"{key}:\n" + "\n".join(f"  - {v}" for v in values)
    match = re.match(r"\A(---\s*\n)(.*?)(\n---\s*\n)", raw, re.DOTALL)
    if not match:
        return f"---\n{block}\n---\n\n" + raw.lstrip()
    inner = match.group(2)
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*?(?=^\S|\Z)", re.MULTILINE | re.DOTALL)
    inner_neu = pattern.sub(block + "\n", inner) if pattern.search(inner) else inner.rstrip() + "\n" + block
    return match.group(1) + inner_neu.rstrip() + match.group(3) + raw[match.end():]
