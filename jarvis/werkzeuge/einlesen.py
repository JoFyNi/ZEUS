"""Fremde Dateien hereinholen - einzeln oder ordnerweise.

Nach aussen nur lesend: Der Agent darf beliebige Textdateien
und PDFs importieren, aber nichts ausserhalb der Vault
schreiben.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set

from ..vault import strip_frontmatter

from .ergebnis import ToolResult
from .hilfen import (
    IMPORT_ENDUNGEN, PROSA_ENDUNGEN, SPRACH_ENDUNGEN,
    _pdf_als_text,
)


class ImportWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ------------------------------------------------ 25. Datei importieren
    def datei_importieren(self, pfad: str, titel: str = "", ordner: str = "",
                          als_code: bool = True) -> ToolResult:
        """Liest eine Datei von der Platte und legt sie als Notiz in der Vault ab.

        Bewusst nur lesend nach außen: Der Agent darf beliebige Textdateien
        hereinholen, aber nichts außerhalb der Vault schreiben.
        """
        quelle = Path(str(pfad).strip().strip('"').strip("'")).expanduser()

        # Ein ganzer Ordner auf einmal. Ohne das muss für einen Semesterordner
        # dreißigmal aufgerufen werden - und ein Modell, das dreißig Aufrufe
        # hintereinander machen soll, hört nach dem zweiten auf
        # (ENTWICKLUNG.md, Muster 2.3). Die Schleife gehört nach Python.
        if quelle.is_dir():
            return self._ordner_importieren(quelle, ordner, als_code)
        if not quelle.is_file():
            return ToolResult(False, f"Datei nicht gefunden: {quelle}")
        if quelle.suffix.lower() not in IMPORT_ENDUNGEN:
            return ToolResult(False, f"Endung '{quelle.suffix}' wird nicht importiert.",
                              {"erlaubt": sorted(IMPORT_ENDUNGEN)})
        try:
            groesse = quelle.stat().st_size
        except OSError as exc:
            return ToolResult(False, f"Datei nicht lesbar: {exc}")
        if groesse > self.cfg.max_note_chars:
            return ToolResult(False, f"Datei zu groß ({groesse} Zeichen > "
                                     f"MAX_NOTE_CHARS={self.cfg.max_note_chars}).")

        endung = quelle.suffix.lower()
        sprache = SPRACH_ENDUNGEN.get(endung, "text")

        if endung == ".pdf":
            inhalt, fehler = _pdf_als_text(quelle)
            if fehler:
                return ToolResult(False, fehler, {"pfad": str(quelle)})
            tags = ["import", "pdf"]
        else:
            try:
                roh = quelle.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return ToolResult(False, f"Lesen fehlgeschlagen: {exc}")
            if "\x00" in roh:
                return ToolResult(False, "Datei sieht binär aus – nicht importiert.")

            if endung in (".md", ".markdown") and not als_code:
                inhalt = roh
            elif endung in PROSA_ENDUNGEN:
                # Fließtext gehört nicht in einen Codeblock. Im Codezaun wird er
                # nicht umgebrochen und vom Index als ein Klotz behandelt - eine
                # importierte Mitschrift war damit praktisch unauffindbar,
                # obwohl sie in der Vault lag.
                absaetze = [a.strip() for a in re.split(r"\n\s*\n", roh) if a.strip()]
                inhalt = f"Importiert aus `{quelle}`\n\n" + "\n\n".join(absaetze) + "\n"
                sprache = "text"
            else:
                zaun = "````" if "```" in roh else "```"
                inhalt = (f"Importiert aus `{quelle}`\n\n{zaun}{sprache}\n"
                          f"{roh.rstrip()}\n{zaun}\n")
            tags = ["import", sprache] if sprache != "text" else ["import"]

        ergebnis = self.notiz_erstellen(
            titel=str(titel).strip() or quelle.stem,
            inhalt=inhalt, ordner=ordner, tags=tags,
        )
        if ergebnis.ok:
            self.frontmatter_setzen(Path(ergebnis.daten["pfad"]).stem, "quelldatei", str(quelle))
            self.on_event("schreiben", f"Importiert: {quelle.name} → {ergebnis.daten['pfad']}")
        return ergebnis

    def _ordner_importieren(self, ordner_pfad: Path, ziel_ordner: str,
                            als_code: bool) -> ToolResult:
        """Alle unterstützten Dateien eines Ordners - ohne Duplikate."""
        try:
            eintraege = sorted(ordner_pfad.iterdir())
        except OSError as exc:
            return ToolResult(False, f"Ordner nicht lesbar: {exc}")
        kandidaten = [p for p in eintraege
                      if p.is_file() and p.suffix.lower() in IMPORT_ENDUNGEN]
        if not kandidaten:
            gefunden = sorted({p.suffix.lower() for p in eintraege if p.is_file() and p.suffix})
            return ToolResult(
                False,
                f"In '{ordner_pfad.name}' liegt keine importierbare Datei"
                + (f" (vorhanden: {', '.join(gefunden[:6])})" if gefunden else " – der Ordner ist leer")
                + ". Nichts importiert.",
                {"pfad": str(ordner_pfad), "erlaubt": sorted(IMPORT_ENDUNGEN)})

        schon_da = self._bereits_importiert()
        importiert: List[str] = []
        uebersprungen: List[str] = []
        fehler: List[str] = []
        for datei in kandidaten:
            if str(datei) in schon_da:
                uebersprungen.append(datei.name)
                continue
            ergebnis = self.datei_importieren(pfad=str(datei), ordner=ziel_ordner,
                                              als_code=als_code)
            if ergebnis.ok:
                importiert.append(datei.name)
            else:
                fehler.append(f"{datei.name}: {ergebnis.meldung[:70]}")

        teile = [f"{len(importiert)} Datei(en) aus '{ordner_pfad.name}' importiert"]
        if uebersprungen:
            teile.append(f"{len(uebersprungen)} schon vorhanden, übersprungen")
        if fehler:
            teile.append(f"{len(fehler)} fehlgeschlagen: " + "; ".join(fehler[:3]))
        return ToolResult(
            bool(importiert) or bool(uebersprungen),
            ", ".join(teile) + ".",
            {"importiert": importiert, "uebersprungen": uebersprungen,
             "fehler": fehler, "pfad": str(ordner_pfad)})

    def _bereits_importiert(self) -> Set[str]:
        """Welche Quelldateien liegen schon als Notiz in der Vault?

        Der Beleg steht im Frontmatter (`quelldatei`), nicht in einer eigenen
        Liste: Eine Liste neben den Daten läuft auseinander, sobald jemand eine
        Notiz von Hand löscht - und dann gilt eine Datei für immer als schon
        importiert, obwohl sie weg ist.
        """
        gefunden: Set[str] = set()
        for datei in self.root.rglob("*.md"):
            try:
                kopf, _ = strip_frontmatter(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            quelle = str((kopf or {}).get("quelldatei") or "").strip()
            if quelle:
                gefunden.add(quelle)
        return gefunden
