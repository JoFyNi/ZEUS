"""Selbstinspektion: der eigene Quellcode.

Bewusst nur LESEND nach aussen. Zeus darf seinen Code ansehen,
durchsuchen und als Notizen ablegen. Aendern kann er ihn hier
nicht - dafuer gibt es den Weg ueber die Quarantaene in
kern/selbst.py, mit Testlauf und Freigabe.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..vault import trim_at_boundary

from .ergebnis import ToolResult
from .hilfen import IMPORT_ENDUNGEN, SPRACH_ENDUNGEN


class CodeWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ======================================= SELBSTINSPEKTION: EIGENER CODE
    # Bewusst NUR lesend nach außen. Der Agent darf seinen Quellcode ansehen,
    # verstehen und Verbesserungen vorschlagen - ändern kann er ihn nicht.
    # Ein Agent, der sich selbst umschreibt, während er läuft, ist kein Feature,
    # sondern eine Fehlerquelle ohne Rückweg.

    def _code_wurzel_pruefen(self, pfad: Path) -> Optional[Path]:
        """Liegt der Pfad in einem freigegebenen Codeverzeichnis?"""
        try:
            aufgeloest = pfad.expanduser().resolve()
        except OSError:
            return None
        for wurzel in self.cfg.code_root_list:
            try:
                if aufgeloest == wurzel or wurzel in aufgeloest.parents:
                    return aufgeloest
            except OSError:
                continue
        return None


    # -------------------------------------------------- 26. Code auflisten
    def code_dateien(self, muster: str = "*.py", unterordner: str = "") -> ToolResult:
        """Zeigt, welche Quelldateien es gibt - der Einstieg in die Selbstprüfung."""
        muster = str(muster).strip() or "*.py"
        treffer: List[dict] = []
        for wurzel in self.cfg.code_root_list:
            basis = wurzel / unterordner if unterordner else wurzel
            if not self._code_wurzel_pruefen(basis) or not basis.is_dir():
                continue
            for datei in sorted(basis.rglob(muster)):
                if not datei.is_file():
                    continue
                if any(teil.startswith(".") or teil in ("__pycache__", "vector_store",
                                                        "node_modules", "venv", ".venv")
                       for teil in datei.relative_to(wurzel).parts):
                    continue
                try:
                    zeilen = sum(1 for _ in datei.open(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                treffer.append({
                    "datei": datei.relative_to(wurzel).as_posix(),
                    "pfad": str(datei),
                    "zeilen": zeilen,
                    "bytes": datei.stat().st_size,
                })
                if len(treffer) >= self.cfg.max_code_files:
                    break
        return ToolResult(
            bool(treffer),
            f"{len(treffer)} Quelldateien gefunden." if treffer
            else f"Keine Dateien zu '{muster}' in den freigegebenen Verzeichnissen.",
            {"dateien": treffer,
             "wurzeln": [str(w) for w in self.cfg.code_root_list]},
        )


    # ---------------------------------------------------- 27. Quellcode lesen
    def quellcode_lesen(self, datei: str, von_zeile: int = 1,
                        bis_zeile: int = 0) -> ToolResult:
        """Liest eine Quelldatei - ganz oder einen Zeilenbereich."""
        kandidat = Path(str(datei).strip().strip('"').strip("'"))
        gefunden = self._code_wurzel_pruefen(kandidat)
        if gefunden is None or not gefunden.is_file():
            # Relativ zu einer Wurzel probieren
            for wurzel in self.cfg.code_root_list:
                versuch = self._code_wurzel_pruefen(wurzel / str(datei))
                if versuch and versuch.is_file():
                    gefunden = versuch
                    break
        if gefunden is None or not gefunden.is_file():
            return ToolResult(False, f"'{datei}' liegt nicht in einem freigegebenen "
                                     f"Codeverzeichnis oder existiert nicht.",
                              {"wurzeln": [str(w) for w in self.cfg.code_root_list]})
        try:
            zeilen = gefunden.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return ToolResult(False, f"Lesen fehlgeschlagen: {exc}")

        start = max(1, int(von_zeile or 1))
        ende = int(bis_zeile) if bis_zeile else len(zeilen)
        ende = min(max(start, ende), len(zeilen))
        ausschnitt = zeilen[start - 1 : ende]
        # Zeilennummern mitgeben, damit sich Vorschläge eindeutig verorten lassen
        nummeriert = "\n".join(f"{start + i:5}| {z}" for i, z in enumerate(ausschnitt))
        return ToolResult(True, f"{gefunden.name}, Zeilen {start}–{ende} von {len(zeilen)}.", {
            "pfad": str(gefunden), "gesamtzeilen": len(zeilen),
            "von": start, "bis": ende,
            "code": trim_at_boundary(nummeriert, 12000),
        })


    # ------------------------------------------------------ 28. Code suchen
    def code_suchen(self, begriff: str, muster: str = "*.py",
                    anzahl: int = 30) -> ToolResult:
        """Sucht eine Zeichenkette im Quellcode - mit Datei und Zeilennummer."""
        begriff = str(begriff).strip()
        if not begriff:
            return ToolResult(False, "Kein Suchbegriff übergeben.")
        treffer: List[dict] = []
        for wurzel in self.cfg.code_root_list:
            for datei in sorted(wurzel.rglob(str(muster) or "*.py")):
                if not datei.is_file() or any(
                        teil.startswith(".") or teil in ("__pycache__", "vector_store")
                        for teil in datei.relative_to(wurzel).parts):
                    continue
                try:
                    for nummer, zeile in enumerate(
                            datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if begriff.lower() in zeile.lower():
                            treffer.append({
                                "datei": datei.relative_to(wurzel).as_posix(),
                                "zeile": nummer,
                                "text": zeile.strip()[:200],
                            })
                            if len(treffer) >= int(anzahl):
                                break
                except OSError:
                    continue
                if len(treffer) >= int(anzahl):
                    break
        return ToolResult(bool(treffer),
                          f"{len(treffer)} Fundstellen für '{begriff}'." if treffer
                          else f"'{begriff}' kommt im Quellcode nicht vor.",
                          {"treffer": treffer})


    # ------------------------------------------- 29. Codebasis in die Vault
    def codebasis_importieren(self, muster: str = "*.py", ordner: str = "",
                              erneuern: bool = False) -> ToolResult:
        """Legt den Quellcode als Notizen ab, damit er durchsuchbar wird.

        Danach findet die semantische Suche auch Code - "wo wird der Index
        aufgebaut?" liefert dann die passende Datei statt einer Vermutung.

        Drei Dinge, die vorher schiefgingen und hier gelöst sind:

        * **Doppelte Codebasis.** Lag der Code zweimal auf der Platte, wurde
          jede Datei zweimal abgelegt. Verglichen wird jetzt der INHALT.
        * **Zu große Dateien.** `tools.py` und `agent.py` wurden mit "zu groß"
          übersprungen - also ausgerechnet die Dateien, um die es bei einer
          Frage nach dem eigenen Code fast immer geht. Sie werden jetzt in
          nummerierte Teile zerlegt, mit Zeilenbereich im Kopf.
        * **Erschöpftes Schreibbudget.** Nach MAX_FILES_PER_RUN Notizen war
          Schluss, gemeldet wurde trotzdem Erfolg, und der nächste Aufruf fing
          wieder von vorn an. Jetzt bleibt der Rest namentlich offen, und der
          Folgeaufruf überspringt, was schon dasteht.
        """
        liste = self.code_dateien(muster=muster)
        if not liste.ok:
            return liste
        zielordner = ordner or self.cfg.code_folder
        importiert, uebersprungen, doppelt = [], [], []
        schon_da, offen = [], []
        gesehen: Dict[str, str] = {}
        grenze_erreicht = False

        for eintrag in liste.daten["dateien"]:
            titel = eintrag["datei"].replace("/", " · ")

            # 1. Inhaltsgleiche Doppel. Der Vergleich ist billig und läuft
            #    deshalb VOR der Budgetprüfung - sonst bliebe eine Kopie am
            #    Ende als "offen" stehen und sähe nach fehlender Arbeit aus.
            fingerabdruck = self._fingerabdruck(eintrag["pfad"])
            if fingerabdruck and fingerabdruck in gesehen:
                doppelt.append(f"{eintrag['datei']} = {gesehen[fingerabdruck]}")
                continue
            if fingerabdruck:
                gesehen[fingerabdruck] = eintrag["datei"]

            # 2. Zuschneiden - ganz oder in Teilen. Das erzeugt noch nichts,
            #    es sagt nur, welche Notizen es geben MÜSSTE.
            teile, grund = self._code_teile(eintrag, titel)
            if grund:
                uebersprungen.append({"datei": eintrag["datei"], "grund": grund})
                continue

            # 3. Was schon dasteht, wird übersprungen - Teil für Teil. Beim
            #    Fortsetzen kommt es genau darauf an: Wenn von acht Teilen
            #    sechs geschrieben wurden, müssen die zwei fehlenden dran sein
            #    und nicht wieder alle acht.
            fehlend = [(t, i) for t, i in teile
                       if erneuern or self.notiz_exakt(t) is None]
            if not fehlend:
                schon_da.append(eintrag["datei"])
                continue

            # 4. Erst jetzt das Budget. Ist es aufgebraucht, wird der Rest
            #    NICHT still verschluckt, sondern namentlich zurückgemeldet.
            if grenze_erreicht:
                offen.append(eintrag["datei"])
                continue

            for teil_titel, inhalt in fehlend:
                ergebnis = self._teil_ablegen(teil_titel, inhalt, zielordner,
                                              eintrag["pfad"])
                if ergebnis.ok:
                    importiert.append(ergebnis.daten.get("pfad", teil_titel))
                elif "MAX_FILES_PER_RUN" in (ergebnis.meldung or ""):
                    grenze_erreicht = True
                    if eintrag["datei"] not in offen:
                        offen.append(eintrag["datei"])
                    break
                else:
                    uebersprungen.append({"datei": teil_titel,
                                          "grund": ergebnis.meldung})

        self.on_event("schreiben", f"{len(importiert)} Quelldateien in die Vault übernommen")
        meldung = f"{len(importiert)} Notizen nach '{zielordner}' übernommen"
        if schon_da:
            meldung += f", {len(schon_da)} Dateien waren schon da"
        if doppelt:
            meldung += f", {len(doppelt)} inhaltsgleiche Doppel übersprungen"
        if uebersprungen:
            meldung += f", {len(uebersprungen)} nicht importierbar"
        meldung += "."
        if offen:
            meldung += (f" {len(offen)} Dateien blieben offen – das Schreibbudget "
                        f"(MAX_FILES_PER_RUN={self.cfg.max_files_per_run}) war "
                        f"erschöpft. Denselben Aufruf einfach wiederholen: bereits "
                        f"Importiertes wird übersprungen, es geht dort weiter, wo "
                        f"es aufgehört hat.")
        return ToolResult(bool(importiert) or bool(schon_da), meldung,
                          {"importiert": importiert[:40],
                           "uebersprungen": uebersprungen[:10],
                           "doppelte": doppelt[:10],
                           "schon_vorhanden": len(schon_da),
                           "offen": offen[:20],
                           "fortsetzen": bool(offen),
                           "ordner": zielordner})

    def _fingerabdruck(self, pfad: str) -> str:
        try:
            return hashlib.sha1(Path(pfad).read_bytes()).hexdigest()
        except OSError:
            # Nicht lesbar: dann eben ohne Dublettenschutz weiter - der
            # eigentliche Lesefehler kommt gleich beim Zuschneiden ohnehin.
            return ""

    def _code_teile(self, eintrag: dict,
                    titel: str) -> Tuple[List[Tuple[str, str]], str]:
        """Zuschnitt einer Quelldatei: [(Notiztitel, Inhalt)], oder ein Grund.

        Schreibt nichts. Das ist Absicht: Erst wenn feststeht, welche Notizen
        es geben müsste, lässt sich sagen, welche davon fehlen - und nur die
        werden geschrieben. Genau daran hing die Fortsetzbarkeit.

        Der Zuschnitt folgt den ZEILEN, nicht den Zeichen: Eine Notiz, die
        mitten in einer Zeile abbricht, ist für die spätere Suche wertlos, und
        der Zeilenbereich im Kopf jedes Teils ist das, was einen Treffer
        überhaupt auf eine Stelle im echten Quelltext zurückführt.
        """
        pfad = Path(eintrag["pfad"])
        if pfad.suffix.lower() not in IMPORT_ENDUNGEN:
            return [], f"Endung '{pfad.suffix}' wird nicht importiert."
        try:
            roh = pfad.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [], f"Nicht lesbar: {exc}"
        if "\x00" in roh:
            return [], "Datei sieht binär aus – nicht importiert."

        sprache = SPRACH_ENDUNGEN.get(pfad.suffix.lower(), "text")
        grenze = self.cfg.max_note_chars

        def block(text: str, kopf: str) -> str:
            zaun = "````" if "```" in text else "```"
            return f"{kopf}\n\n{zaun}{sprache}\n{text.rstrip()}\n{zaun}\n"

        if len(roh) <= grenze * 0.9:
            return [(titel, block(roh, f"Importiert aus `{pfad}`"))], ""

        zeilen = roh.splitlines()
        stuecke: List[List[str]] = []
        laufend: List[str] = []
        laenge = 0
        for zeile in zeilen:
            if laufend and (len(laufend) >= self.CODE_TEIL_ZEILEN
                            or laenge + len(zeile) > grenze * 0.75):
                stuecke.append(laufend)
                laufend, laenge = [], 0
            laufend.append(zeile)
            laenge += len(zeile) + 1
        if laufend:
            stuecke.append(laufend)

        teile: List[Tuple[str, str]] = []
        anfang = 1
        for nummer, stueck in enumerate(stuecke, 1):
            ende = anfang + len(stueck) - 1
            kopf = (f"Importiert aus `{pfad}`, Zeilen {anfang}–{ende} "
                    f"(Teil {nummer} von {len(stuecke)})")
            teile.append((f"{titel} ({nummer} von {len(stuecke)})",
                          block("\n".join(stueck), kopf)))
            anfang = ende + 1
        return teile, ""

    def _teil_ablegen(self, teil_titel: str, inhalt: str, zielordner: str,
                      quelle: str) -> ToolResult:
        sprache = SPRACH_ENDUNGEN.get(Path(quelle).suffix.lower(), "text")
        ergebnis = self.notiz_erstellen(
            titel=teil_titel, inhalt=inhalt, ordner=zielordner,
            tags=["import", sprache] if sprache != "text" else ["import"],
        )
        if ergebnis.ok:
            self.frontmatter_setzen(Path(ergebnis.daten["pfad"]).stem,
                                    "quelldatei", str(quelle))
        return ergebnis


    # -------------------------------------------------- 24. Code auslesen
    def code_auslesen(self, name: str, sprache: str = "") -> ToolResult:
        """Holt die Codeblöcke aus einer Notiz - zum Prüfen, Ändern, Weiterverwenden."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        bloecke = []
        for treffer in re.finditer(r"^(`{3,})([\w+#-]*)\s*\n(.*?)^\1\s*$",
                                   note.body, re.DOTALL | re.MULTILINE):
            gefunden = (treffer.group(2) or "text").lower()
            if sprache and gefunden != str(sprache).lower():
                continue
            bloecke.append({"sprache": gefunden, "zeilen": treffer.group(3).count("\n") + 1,
                            "code": treffer.group(3).rstrip()[:4000]})
        return ToolResult(bool(bloecke),
                          f"{len(bloecke)} Codeblöcke in '{note.stem}'."
                          if bloecke else f"'{note.stem}' enthält keinen Code"
                                          + (f" in {sprache}" if sprache else "") + ".",
                          {"pfad": note.rel_path, "bloecke": bloecke})
