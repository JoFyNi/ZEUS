"""Gezielt an EINER Stelle aendern - Abschnitte und Bloecke.

Der Unterschied zu notiz_ueberschreiben ist der Schadensumfang:
Hier geht bei einem Fehlgriff ein Abschnitt verloren, dort die
ganze Notiz. Deshalb duerfen diese Werkzeuge tolerant mit
Namen umgehen, das Ganz-Ersetzen aber nicht.
"""
from __future__ import annotations

import re

from ..vault import split_sections

from .ergebnis import ToolResult, WriteError
from .hilfen import _abschnitt_ende, _abschnitt_ersetzen, _abschnitt_index


class AbschnittsWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""

    def block_ersetzen(self, name: str, block: int, inhalt: str,
                       nur_vorschau: bool = False) -> ToolResult:
        """Ersetzt EINEN Block einer Notiz - Absatz, Liste, Codeblock.

        Der dreistufige Weg, den die Aufgabe verlangt:

        1. Schnappschuss: `_track()` sichert die Datei nach `.jarvis/backups/`,
           bevor etwas passiert. Das gab es schon und wird hier nur benutzt.
        2. Blockzerlegung: über `parse_blocks` - Überschriftenebene und
           Absatzzähler, das ist der neue Teil.
        3. Vorschau: Der Unterschied entsteht, BEVOR geschrieben wird. Mit
           `nur_vorschau=True` bleibt es dabei; mit CONFIRM_WRITES=true fragt
           die Oberfläche vorher nach.
        """
        from ..bloecke import Aenderungsvorschau, block_ersetzen_text

        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(name))})
        if not str(inhalt).strip():
            return ToolResult(
                False, "Kein Inhalt übergeben. Zum ENTFERNEN eines Blocks "
                       "gibt es abschnitt_loeschen bzw. notiz_bearbeiten.")
        try:
            roh = note.path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"'{note.stem}' nicht lesbar: {exc}")

        neu, getroffen, fehler = block_ersetzen_text(roh, block, str(inhalt))
        if fehler:
            return ToolResult(False, fehler, {"pfad": note.rel_path})

        vorschau = Aenderungsvorschau(notiz=note.stem, pfad=note.rel_path,
                                      vorher=roh, nachher=neu, block=getroffen)
        if vorschau.leer:
            return ToolResult(True, f"Block {block} in '{note.stem}' war bereits "
                                    f"so – nichts geändert.",
                              {"pfad": note.rel_path, "vorschau": vorschau})
        if nur_vorschau:
            return ToolResult(True, vorschau.bericht(),
                              {"pfad": note.rel_path, "vorschau": vorschau,
                               "nur_vorschau": True})

        # Rückfrage, falls eingerichtet. Die Oberfläche stellt sie; hier steht
        # nur, wovon sie handelt.
        if self.bestaetigung is not None and self.cfg.confirm_writes:
            if not self.bestaetigung(vorschau):
                return ToolResult(False, "Änderung abgelehnt – nichts geschrieben.",
                                  {"pfad": note.rel_path, "abgelehnt": True})

        try:
            with self.transaktion(f"{note.rel_path}#{block}", "block_ersetzen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Block nicht ersetzt: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben",
                      f"Block {block} ersetzt in {note.rel_path} ({vorschau.bilanz})")
        return ToolResult(True,
                          f"Block {block} in '{note.stem}' ersetzt ({vorschau.bilanz}).",
                          {"pfad": note.rel_path, "bilanz": vorschau.bilanz,
                           "vorschau": vorschau})

    def abschnitt_ersetzen(self, name: str, abschnitt: str, inhalt: str) -> ToolResult:
        """Tauscht ein ganzes Kapitel aus, ohne den Rest der Notiz anzufassen."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        raw = note.path.read_text(encoding="utf-8")
        neu, gefunden = _abschnitt_ersetzen(raw, abschnitt, str(inhalt).strip())
        if not gefunden:
            return ToolResult(False, f"Abschnitt '{abschnitt}' nicht gefunden.",
                              {"abschnitte": [" > ".join(h) for h, _ in split_sections(note.body) if h]})
        try:
            with self.transaktion(f"{note.rel_path}#{abschnitt}", "abschnitt_ersetzen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Abschnitt nicht ersetzt: {exc}", {"pfad": note.rel_path})
        self.on_event("schreiben", f"Abschnitt '{abschnitt}' ersetzt in {note.rel_path}")
        return ToolResult(True, f"Abschnitt '{abschnitt}' in '{note.stem}' ersetzt.",
                          {"pfad": note.rel_path})


    # ------------------------------------------------ 14. Abschnitt einfügen
    def abschnitt_einfuegen(self, name: str, ueberschrift: str, inhalt: str,
                            nach_abschnitt: str = "", ebene: int = 2) -> ToolResult:
        """Fügt ein neues Kapitel ein - am Ende oder hinter einem bestehenden."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        raw = note.path.read_text(encoding="utf-8")
        marke = "#" * max(1, min(6, int(ebene or 2)))
        block = f"\n{marke} {str(ueberschrift).strip()}\n\n{str(inhalt).strip()}\n"

        if nach_abschnitt:
            zeilen = raw.splitlines()
            start = _abschnitt_index(zeilen, nach_abschnitt)
            if start is None:
                return ToolResult(False, f"Abschnitt '{nach_abschnitt}' nicht gefunden.")
            ende = _abschnitt_ende(zeilen, start)
            neu = "\n".join(zeilen[:ende]) + "\n" + block + "\n".join(zeilen[ende:])
        else:
            neu = raw.rstrip() + "\n" + block

        try:
            with self.transaktion(note.rel_path, "abschnitt_einfuegen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Abschnitt nicht eingefügt: {exc}", {"pfad": note.rel_path})
        self.on_event("schreiben", f"Abschnitt '{ueberschrift}' eingefügt in {note.rel_path}")
        return ToolResult(True, f"Abschnitt '{ueberschrift}' in '{note.stem}' eingefügt.",
                          {"pfad": note.rel_path})


    # ------------------------------------------------- 15. Abschnitt löschen
    def abschnitt_loeschen(self, name: str, abschnitt: str) -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        raw = note.path.read_text(encoding="utf-8")
        zeilen = raw.splitlines()
        start = _abschnitt_index(zeilen, abschnitt)
        if start is None:
            return ToolResult(False, f"Abschnitt '{abschnitt}' nicht gefunden.")
        ende = _abschnitt_ende(zeilen, start)
        neu = "\n".join(zeilen[:start] + zeilen[ende:]).rstrip() + "\n"
        try:
            with self.transaktion(note.rel_path, "abschnitt_loeschen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Abschnitt nicht gelöscht: {exc}", {"pfad": note.rel_path})
        self.on_event("struktur", f"Abschnitt '{abschnitt}' entfernt aus {note.rel_path}")
        return ToolResult(True, f"Abschnitt '{abschnitt}' aus '{note.stem}' entfernt "
                                f"(rückgängig mit /rueckgaengig).", {"pfad": note.rel_path})


    # =========================================== CODE UND EXTERNE DATEIEN
    # ------------------------------------------------ 23. Codeblock einfügen
    def codeblock_hinzufuegen(self, name: str, code: str, sprache: str = "python",
                              abschnitt: str = "", beschreibung: str = "") -> ToolResult:
        """Fügt Code sauber als Markdown-Codeblock ein.

        Wichtig gegenüber notiz_ergaenzen: Der Code wird eingezäunt, damit
        Rauten darin nicht als Überschriften gelesen werden und die Chunk-Bildung
        ihn nicht mitten in einer Funktion zerschneidet.
        """
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"vorschlaege": self.aehnliche_namen(name)})
        if not str(code).strip():
            return ToolResult(False, "Kein Code übergeben.")

        sprache = re.sub(r"[^\w+#-]", "", str(sprache) or "text").lower() or "text"
        roher_code = str(code).strip()
        # Falls das Modell die Zäune schon mitgeliefert hat: nicht doppeln
        roher_code = re.sub(r"\A```\w*\s*\n?|\n?```\s*\Z", "", roher_code)
        zaun = "````" if "```" in roher_code else "```"
        block = ((f"{beschreibung.strip()}\n\n" if beschreibung else "")
                 + f"{zaun}{sprache}\n{roher_code}\n{zaun}\n")

        if abschnitt:
            ergebnis = self.notiz_ergaenzen(note.stem, block, abschnitt=abschnitt)
        else:
            ergebnis = self.notiz_ergaenzen(note.stem, block)
        if ergebnis.ok:
            self.on_event("schreiben", f"Codeblock ({sprache}) in {note.rel_path}")
            return ToolResult(True, f"Codeblock ({sprache}, {len(roher_code)} Zeichen) "
                                    f"in '{note.stem}' eingefügt.", {"pfad": note.rel_path})
        return ergebnis
