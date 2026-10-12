"""Listen als Listen behandeln.

`notiz_ergaenzen` haengt an - ans Dateiende oder ans Ende eines
Abschnitts. Fuer Fliesstext ist das richtig, fuer eine
Aufgabenliste falsch. Diese beiden Werkzeuge reihen einen Punkt
IN die Liste ein und aendern beim Abhaken die vorhandene Zeile,
statt einen Kommentar dazuzuschreiben.
"""
from __future__ import annotations


from ..vault import fold

from .ergebnis import ToolResult, WriteError
from .hilfen import (
    _abschnittsgrenzen,
    _abschnittsnamen,
    _listenbereich, _punkttext,
    _CHECKBOX_RE, _LISTENZEILE_RE,
)


class ListenWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ------------------------------------------------- 5b. Listen als Listen
    # notiz_ergaenzen hängt an - ans Dateiende oder ans Ende eines Abschnitts.
    # Für Fließtext ist das richtig, für eine Aufgabenliste falsch: "Füge einen
    # Punkt hinzu" gehört IN die Liste, nicht als loser Absatz darunter. Und
    # "X ist erledigt" soll die vorhandene Zeile ändern, nicht einen Kommentar
    # dazuschreiben. Beides braucht ein Werkzeug, das die Liste als Liste
    # begreift - sonst wächst unter jeder Aufgabenliste ein zweiter Text an.
    def listenpunkt_hinzufuegen(self, name: str, text: str, abschnitt: str = "",
                                position: str = "ende", checkbox: bool = True) -> ToolResult:
        """Reiht einen Punkt in eine BESTEHENDE Liste ein."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(name))})
        if not str(text).strip():
            return ToolResult(False, "Kein Text für den Listenpunkt übergeben.")

        roh = note.path.read_text(encoding="utf-8")
        zeilen = roh.split("\n")
        bereich = _listenbereich(zeilen, abschnitt)
        if bereich is None:
            # Nicht blind anhängen. Eine Liste zu erfinden, wo keine ist, sieht
            # wie Erfolg aus und zerlegt die Notiz - notiz_ergaenzen ist dafür
            # das ehrliche Werkzeug, und die Meldung nennt es.
            wo = f"im Abschnitt '{abschnitt}'" if abschnitt else "in dieser Notiz"
            return ToolResult(
                False,
                f"In '{note.stem}' gibt es {wo} keine Liste, in die sich ein Punkt "
                f"einreihen ließe. Für freien Text nimm notiz_ergaenzen; soll hier "
                f"eine Liste entstehen, lege sie mit abschnitt_einfuegen an.",
                {"pfad": note.rel_path, "abschnitte": _abschnittsnamen(zeilen)})

        erste, letzte = bereich
        muster = zeilen[erste]
        einzug = muster[: len(muster) - len(muster.lstrip())]
        marke = muster.lstrip()[:1] if muster.lstrip()[:1] in "-*+" else "-"
        eintrag = (f"{einzug}{marke} [ ] {str(text).strip()}" if checkbox
                   else f"{einzug}{marke} {str(text).strip()}")
        stelle = erste if str(position).strip().lower() in ("anfang", "oben", "start",
                                                            "beginn") else letzte + 1
        neu_zeilen = zeilen[:stelle] + [eintrag] + zeilen[stelle:]
        neu = "\n".join(neu_zeilen)

        try:
            with self.transaktion(note.rel_path, "listenpunkt_hinzufuegen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"'{note.stem}' nicht änderbar: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Listenpunkt ergänzt: {note.rel_path}")
        return ToolResult(True, f"Punkt '{str(text).strip()[:60]}' in '{note.stem}' "
                                f"eingereiht.",
                          {"pfad": note.rel_path, "zeile": stelle + 1, "eintrag": eintrag})

    def listenpunkt_erledigt(self, name: str, text: str, modus: str = "auto",
                             abschnitt: str = "") -> ToolResult:
        """Hakt einen vorhandenen Punkt ab, streicht ihn durch oder entfernt ihn.

        Bei mehr als einem Treffer wird NICHT geraten. Ein falsch abgehakter
        Punkt ist genau die Art stiller Fehlschlag, gegen die dieses Projekt
        an allen anderen Stellen auch gesichert ist.
        """
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(name))})
        gesucht = str(text).strip()
        if not gesucht:
            return ToolResult(False, "Kein Text übergeben, der gefunden werden soll.")

        roh = note.path.read_text(encoding="utf-8")
        zeilen = roh.split("\n")
        grenzen = _abschnittsgrenzen(zeilen, abschnitt) if abschnitt else (0, len(zeilen))
        if grenzen is None:
            return ToolResult(False, f"'{note.stem}' hat keinen Abschnitt '{abschnitt}'.",
                              {"abschnitte": _abschnittsnamen(zeilen)})
        von, bis = grenzen

        treffer = [i for i in range(von, bis)
                   if _LISTENZEILE_RE.match(zeilen[i])
                   and fold(gesucht) in fold(_punkttext(zeilen[i]))]
        # Ein exakter Treffer schlägt mehrere ungefähre - "Bericht" neben
        # "Bericht schreiben" und "Bericht pruefen" bleibt trotzdem mehrdeutig.
        genau = [i for i in treffer if fold(_punkttext(zeilen[i])) == fold(gesucht)]
        if len(genau) == 1:
            treffer = genau

        if not treffer:
            offen = [_punkttext(z) for z in zeilen[von:bis] if _LISTENZEILE_RE.match(z)]
            return ToolResult(
                False,
                f"In '{note.stem}' gibt es keinen Listenpunkt, der '{gesucht[:50]}' "
                f"enthält. Nichts geändert.",
                {"pfad": note.rel_path, "vorhanden": offen[:12]})
        if len(treffer) > 1:
            kandidaten = [_punkttext(zeilen[i]) for i in treffer]
            return ToolResult(
                False,
                f"'{gesucht[:40]}' passt auf {len(treffer)} Punkte in '{note.stem}': "
                + "; ".join(k[:40] for k in kandidaten[:5])
                + ". Nenne den Punkt genauer - hier wird nicht geraten.",
                {"pfad": note.rel_path, "treffer": kandidaten})

        stelle = treffer[0]
        zeile = zeilen[stelle]
        art = str(modus or "auto").strip().lower()
        hat_kasten = bool(_CHECKBOX_RE.match(zeile))
        if art in ("auto", ""):
            art = "abhaken" if hat_kasten else "durchstreichen"

        if art in ("entfernen", "loeschen", "löschen", "remove"):
            neu_zeilen = zeilen[:stelle] + zeilen[stelle + 1:]
            was = "entfernt"
        elif art in ("durchstreichen", "streichen", "strike"):
            inhalt = _punkttext(zeile)
            if inhalt.startswith("~~") and inhalt.endswith("~~"):
                return ToolResult(True, f"'{inhalt}' war bereits durchgestrichen.",
                                  {"pfad": note.rel_path, "unveraendert": True})
            neu_zeilen = list(zeilen)
            neu_zeilen[stelle] = zeile.replace(inhalt, f"~~{inhalt}~~", 1)
            was = "durchgestrichen"
        else:
            if not hat_kasten:
                return ToolResult(
                    False,
                    f"'{_punkttext(zeile)}' hat keine Checkbox - abhaken geht nicht. "
                    f"Nimm modus='durchstreichen' oder modus='entfernen'.",
                    {"pfad": note.rel_path})
            neu_zeilen = list(zeilen)
            neu_zeilen[stelle] = _CHECKBOX_RE.sub(lambda m: m.group(1) + "[x] ", zeile, count=1)
            was = "abgehakt"

        try:
            with self.transaktion(note.rel_path, "listenpunkt_erledigt"):
                self._track(note.path)
                self._write(note.path, "\n".join(neu_zeilen))
        except WriteError as exc:
            return ToolResult(False, f"'{note.stem}' nicht änderbar: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Listenpunkt {was}: {note.rel_path}")
        return ToolResult(True, f"'{_punkttext(zeile)[:60]}' in '{note.stem}' {was}.",
                          {"pfad": note.rel_path, "modus": art, "zeile": stelle + 1})
