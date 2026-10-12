"""Notizen miteinander in Beziehung setzen.

Verknuepfen, automatisch verlinken, Uebersichtsnotizen,
Zusammenfuehren und Aufteilen. Alles, was mehr als eine Notiz
gleichzeitig betrifft - und deshalb am ehesten halb
durchlaufen kann. Darum liegt jede dieser Aktionen in einer
Transaktion.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ..vault import (
    extract_section,
    fold,
    split_sections,
    WIKILINK_RE,
    wikilinks_finden,
)

from .ergebnis import ToolResult, WriteError
from .hilfen import (
    _im_ordner, _insert_into_section,
    _rename_wikilinks,
    safe_path_fragment,
)


class VerknuepfungsWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ----------------------------------------------------------- 9. Verlinken
    def notizen_verknuepfen(self, von: str, nach: str, beschreibung: str = "",
                            beidseitig: bool = True) -> ToolResult:
        # Ein leeres Ziel ist kein Tippfehler, sondern ein Missverständnis:
        # Das Modell wollte tote Links ENTFERNEN und hat dafür das
        # Verknüpfungswerkzeug missbraucht. Die Meldung sagt deshalb, welches
        # Werkzeug gemeint war - sonst probiert es dasselbe gleich nochmal.
        if not str(von).strip() or not str(nach).strip():
            leer = "von" if not str(von).strip() else "nach"
            return ToolResult(
                False,
                f"'{leer}' ist leer. notizen_verknuepfen setzt einen Link ZWISCHEN zwei "
                f"vorhandenen Notizen und kann keine Links entfernen. "
                f"Zum Entfernen toter Verweise: links_bereinigen(name=\"…\"). "
                f"Zum Umbiegen auf eine ähnliche Notiz: links_pruefen(reparieren=true).",
                {"richtiges_werkzeug": "links_bereinigen"})
        if isinstance(von, (list, tuple)) or isinstance(nach, (list, tuple)):
            return ToolResult(False, "notizen_verknuepfen nimmt je EINEN Notiznamen, "
                                     "keine Liste. Für mehrere: einen Aufruf je Paar.")
        a = self.resolve_note(von)
        a_stufe = self.letzte_aufloesung
        b = self.resolve_note(nach)
        b_stufe = self.letzte_aufloesung
        if not a or not b:
            fehlend = von if not a else nach
            return ToolResult(False, f"Notiz '{fehlend}' nicht gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(fehlend))})

        # Ein Link an der falschen Notiz sieht wie Erfolg aus und ist keiner.
        # Genau das passierte: 'ARVIS Architektur Overview' wurde stillschweigend
        # zu 'Architektur' aufgelöst, und drei Verknüpfungen landeten an einer
        # bestehenden Notiz, die damit nichts zu tun hatte. Sichtbar war das nur
        # in einer beiläufigen Protokollzeile.
        for roh, note, stufe in ((von, a, a_stufe), (nach, b, b_stufe)):
            if stufe in self.UNSICHERE_STUFEN and fold(str(roh)) != fold(note.stem):
                return ToolResult(
                    False,
                    f"'{roh}' wurde nur ungefähr auf '{note.stem}' zurückgeführt "
                    f"({stufe}). Ein Link an der falschen Notiz ist schlimmer als "
                    f"keiner – deshalb wird hier nicht geraten. Nimm den Namen "
                    f"buchstabengetreu aus der Vault, oder lege die Notiz zuerst an.",
                    {"gemeint_vielleicht": note.stem,
                     "aehnliche": self.aehnliche_namen(str(roh))})
        if a.rel_path == b.rel_path:
            return ToolResult(False, "Eine Notiz kann nicht auf sich selbst verweisen.")

        geaendert: List[str] = []
        try:
            with self.transaktion(f"{a.stem} <-> {b.stem}", "notizen_verknuepfen"):
                for quelle, ziel in ([(a, b), (b, a)] if beidseitig else [(a, b)]):
                    raw = quelle.path.read_text(encoding="utf-8")
                    if re.search(r"\[\[" + re.escape(ziel.stem) + r"([#|\]])", raw):
                        continue
                    zeile = f"- [[{ziel.stem}]]" + (f" – {beschreibung}" if beschreibung else "")
                    neu, gefunden = _insert_into_section(raw, self.cfg.link_section, zeile)
                    if not gefunden:
                        neu = raw.rstrip() + f"\n\n## {self.cfg.link_section}\n{zeile}\n"
                    self._track(quelle.path)
                    self._write(quelle.path, neu)
                    geaendert.append(quelle.rel_path)
        except WriteError as exc:
            return ToolResult(False, f"Verknüpfen fehlgeschlagen: {exc}")

        if not geaendert:
            return ToolResult(True, "Verknüpfung bestand bereits.")
        self.on_event("struktur", f"Verknüpft: {a.stem} ↔ {b.stem}")
        return ToolResult(True, f"'{a.stem}' und '{b.stem}' verknüpft.", {"geaendert": geaendert})


    # ------------------------------------------------- 10b. Tote Links tilgen
    def links_bereinigen(self, name: str = "", modus: str = "entschaerfen") -> ToolResult:
        """Entfernt ins Leere laufende [[Links]] - das fehlende Gegenstück.

        Bisher gab es nur links_pruefen(reparieren=True): Das biegt einen toten
        Link auf eine ÄHNLICHE Notiz um. Gibt es keine ähnliche, blieb er
        stehen, und es gab kein Werkzeug, ihn loszuwerden. Das Modell hat sich
        daraufhin notizen_verknuepfen(nach="") ausgedacht und ist in einer
        Schleife hängengeblieben. Diese Lücke ist hiermit geschlossen.

        modus:
          entschaerfen - [[Tot]] wird zu Klartext 'Tot' (Standard, verlustfrei)
          entfernen    - Link samt Text raus
          markieren    - [[Tot]] wird zu [[Tot]] ⚠️, nichts geht verloren
        """
        modus = str(modus).strip().lower() or "entschaerfen"
        if modus not in ("entschaerfen", "entfernen", "markieren"):
            return ToolResult(False, f"Unbekannter Modus '{modus}'.",
                              {"erlaubt": ["entschaerfen", "entfernen", "markieren"]})

        structure = self._structure()
        if name:
            note = self.resolve_note(name)
            if not note:
                return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                                  {"aehnliche": self.aehnliche_namen(name)})
            betroffen = [note]
        else:
            betroffen = list(self._notes())

        geaendert: List[dict] = []
        with self.transaktion("Tote Links bereinigen", "links_bereinigen"):
            for note in betroffen:
                try:
                    raw = note.path.read_text(encoding="utf-8")
                except OSError as exc:
                    geaendert.append({"notiz": note.stem, "fehler": str(exc)})
                    continue
                neu = raw
                entfernt: List[str] = []
                echte = set(wikilinks_finden(raw))
                self.schritt("entscheiden")
                for match in WIKILINK_RE.finditer(raw):
                    ziel = match.group(1).strip()
                    if ziel not in echte or structure.resolve_link(ziel):
                        continue
                    ganzer = match.group(0)
                    anzeige = ziel.split("|")[-1].split("#")[0].strip() or ziel
                    if modus == "entfernen":
                        ersatz = ""
                    elif modus == "markieren":
                        ersatz = f"{ganzer} ⚠️"
                    else:
                        ersatz = anzeige
                    neu = neu.replace(ganzer, ersatz)
                    entfernt.append(ziel)
                if entfernt and neu != raw:
                    neu = re.sub(r"[ \t]{2,}", " ", neu)
                    self._track(note.path)
                    self._write(note.path, neu)
                    geaendert.append({"notiz": note.stem, "links": sorted(set(entfernt))})

        anzahl = sum(len(e.get("links", [])) for e in geaendert)
        if not geaendert:
            return ToolResult(True, "Keine toten Links gefunden – nichts zu tun.",
                              {"geaendert": []})
        self.on_event("struktur", f"{anzahl} tote Links bereinigt ({modus})")
        return ToolResult(True, f"{anzahl} tote Links in {len(geaendert)} Notizen "
                                f"bereinigt (Modus: {modus}).",
                          {"geaendert": geaendert[:25]})


    # -------------------------------------------------- 18. Massenverlinkung
    def auto_verlinken(self, ordner: str = "", pro_notiz: int = 3,
                       mindestnaehe: float = 0.35) -> ToolResult:
        """Verknüpft inhaltlich verwandte Notizen automatisch miteinander.

        Genau das, was du 'später neue Verlinkungen einbauen' genannt hast:
        über den Bestand laufen, semantische Nachbarn suchen und die Verweise
        setzen - ohne bestehende Links anzufassen.
        """
        notizen = [n for n in self._notes()
                   if not ordner or _im_ordner(n.folder, ordner)]
        if not notizen:
            return ToolResult(False, f"Keine Notizen in '{ordner or 'der Vault'}'.")

        # Zwei Fehler der alten Fassung, beide im Protokoll vom 20.–24.08. sichtbar:
        #
        # 1. Je Verknüpfung wurde SOFORT geschrieben, und weil beidseitig
        #    verlinkt wird, zweimal. Aus 142 Verknüpfungen wurden 350
        #    Schreibvorgänge; '01_Skript_Deep_Learning.md' (48 000 Zeichen)
        #    wurde fünfmal komplett neu geschrieben, um je ~50 Zeichen
        #    anzuhängen.
        # 2. 'pro_notiz' begrenzte nur die AUSGEHENDEN Links. Eingehende kamen
        #    unbegrenzt dazu - eine beliebte Notiz sammelte neun Verweise
        #    "thematisch verwandt" ein.
        #
        # Jetzt: erst alle Paare bestimmen, Obergrenze je Notiz in BEIDE
        # Richtungen prüfen, dann jede Datei genau einmal schreiben.
        obergrenze = max(1, int(getattr(self.cfg, "max_links_pro_notiz", 8)))
        nach_stem = {n.stem: n for n in notizen}
        vorhanden: Dict[str, int] = {}
        zusatz: Dict[str, List[Tuple[str, float]]] = {}

        for note in notizen:
            try:
                roh = note.path.read_text(encoding="utf-8")
            except OSError:
                continue
            abschnitt = extract_section(roh, self.cfg.link_section) or ""
            vorhanden[note.stem] = len(re.findall(r"\[\[", abschnitt))

        paare: List[Tuple[str, str, float]] = []
        gesehen = set()
        for note in notizen:
            vorschlaege = self.verwandte_finden(note.stem, anzahl=int(pro_notiz) + 2)
            if not vorschlaege.ok:
                continue
            offen = int(pro_notiz)
            for kandidat in vorschlaege.daten.get("vorschlaege", []):
                if offen <= 0:
                    break
                ziel = kandidat.get("notiz", "")
                if (kandidat.get("bereits_verlinkt")
                        or kandidat.get("naehe", 0) < mindestnaehe
                        or ziel not in nach_stem):
                    continue
                schluessel = tuple(sorted((note.stem, ziel)))
                if schluessel in gesehen:
                    continue
                gesehen.add(schluessel)
                paare.append((note.stem, ziel, float(kandidat.get("naehe") or 0)))
                offen -= 1

        # Die engsten Paare zuerst - wenn die Obergrenze greift, sollen die
        # aussagekräftigen Verweise überleben, nicht die zufällig ersten.
        paare.sort(key=lambda p: -p[2])
        gesetzt: List[dict] = []
        verworfen = 0
        for links, rechts, naehe in paare:
            if (vorhanden.get(links, 0) + len(zusatz.get(links, [])) >= obergrenze
                    or vorhanden.get(rechts, 0) + len(zusatz.get(rechts, [])) >= obergrenze):
                verworfen += 1
                continue
            zusatz.setdefault(links, []).append((rechts, naehe))
            zusatz.setdefault(rechts, []).append((links, naehe))
            gesetzt.append({"von": links, "nach": rechts, "naehe": round(naehe, 2)})

        geschrieben = 0
        if zusatz:
            try:
                with self.transaktion(f"auto_verlinken ({len(gesetzt)} Paare)",
                                      "auto_verlinken"):
                    for stem, eintraege in zusatz.items():
                        note = nach_stem.get(stem)
                        if note is None:
                            continue
                        raw = note.path.read_text(encoding="utf-8")
                        neue_zeilen = [
                            f"- [[{ziel}]] – verwandt ({naehe:.2f})"
                            for ziel, naehe in eintraege
                            if not re.search(r"\[\[" + re.escape(ziel) + r"([#|\]])", raw)
                        ]
                        if not neue_zeilen:
                            continue
                        neu = raw
                        for zeile in neue_zeilen:
                            neu, gefunden = _insert_into_section(
                                neu, self.cfg.link_section, zeile)
                            if not gefunden:
                                neu = neu.rstrip() + (
                                    f"\n\n## {self.cfg.link_section}\n{zeile}\n")
                        self._track(note.path)
                        self._write(note.path, neu)     # EIN Schreibvorgang je Notiz
                        geschrieben += 1
            except WriteError as exc:
                return ToolResult(False, f"Verlinken fehlgeschlagen: {exc}")

        self.on_event("struktur", f"{len(gesetzt)} Verknüpfungen in "
                                  f"{geschrieben} Notizen gesetzt")
        meldung = (f"{len(gesetzt)} neue Verknüpfungen in {geschrieben} Notizen "
                   f"(ein Schreibvorgang je Notiz).")
        if verworfen:
            meldung += (f" {verworfen} weitere übergangen – Obergrenze von "
                        f"{obergrenze} Verweisen je Notiz erreicht.")
        return ToolResult(True, meldung,
                          {"verknuepfungen": gesetzt[:40], "geprueft": len(notizen),
                           "geschrieben": geschrieben, "uebergangen": verworfen})


    # ------------------------------------------------- 19. Übersichtsnotiz
    def uebersichtsnotiz(self, ordner: str, titel: str = "") -> ToolResult:
        """Legt eine Einstiegsnotiz an, die alle Notizen eines Ordners verlinkt."""
        treffer = self._structure().match_folder(ordner, threshold=0.55)
        rel = treffer[0][0] if treffer else safe_path_fragment(ordner)
        self.schritt("sammeln")
        notizen = [n for n in self._notes() if _im_ordner(n.folder, rel)]
        if not notizen:
            return ToolResult(False, f"Keine Notizen in '{rel}'.",
                              {"vorhandene_ordner": self._structure().folder_paths})

        name = str(titel).strip() or f"Übersicht {Path(rel).name}"
        self.schritt("gruppieren")
        nach_tag: Dict[str, List[str]] = {}
        for note in notizen:
            schluessel = note.tags[0] if note.tags else "Sonstige"
            nach_tag.setdefault(schluessel, []).append(note.stem)

        zeilen = [f"Einstiegspunkt für **{rel}** – {len(notizen)} Notizen.\n"]
        for tag in sorted(nach_tag):
            zeilen.append(f"## {tag}")
            zeilen.extend(f"- [[{s}]]" for s in sorted(nach_tag[tag]))
            zeilen.append("")

        # NUR exakt suchen. resolve_note() ist großzügig und hätte hier
        # 'Fertigkeiten Baum' auf die fremde Notiz 'Fertigkeiten_Definitionen'
        # abgebildet - die wurde daraufhin komplett überschrieben. Eine
        # Übersichtsnotiz trifft entweder ihren eigenen Titel, oder sie wird
        # neu angelegt. Nie etwas dazwischen.
        self.schritt("exakt")
        vorhanden = self.notiz_exakt(name)
        if vorhanden and vorhanden.folder == rel:
            return self.notiz_ueberschreiben(vorhanden.stem, f"# {name}\n\n" + "\n".join(zeilen))
        return self.notiz_erstellen(titel=name, inhalt="\n".join(zeilen), ordner=rel,
                                    tags=["übersicht", "moc"])


    # ------------------------------------------- 16. Notizen zusammenführen
    def notizen_zusammenfuehren(self, quellen: Sequence[str], ziel_titel: str = "",
                                ordner: str = "", quellen_loeschen: bool = True) -> ToolResult:
        """Führt mehrere Notizen zu einer zusammen - gegen Dubletten.

        Die Quellnotizen wandern in den Papierkorb (nicht endgültig gelöscht),
        Verweise auf sie werden auf die neue Notiz umgebogen.
        """
        notizen = [n for n in (self.resolve_note(q) for q in (quellen or [])) if n]
        if len(notizen) < 2:
            return ToolResult(False, "Mindestens zwei auffindbare Notizen nötig.",
                              {"gefunden": [n.stem for n in notizen]})

        titel = str(ziel_titel).strip() or notizen[0].title
        zielordner = ordner or notizen[0].folder
        tags = sorted({t for n in notizen for t in n.tags})

        teile = []
        for n in notizen:
            koerper = n.body.strip()
            koerper = re.sub(r"\A#\s+.*\n?", "", koerper)   # eigene H1 entfernen
            teile.append(f"## Aus [[{n.stem}]]\n\n{koerper}")
        inhalt = "\n\n".join(teile)

        erstellt = self.notiz_erstellen(titel=titel, inhalt=inhalt, ordner=zielordner, tags=tags)
        if not erstellt.ok:
            return erstellt
        neuer_stem = Path(erstellt.daten["pfad"]).stem

        umgebogen: List[str] = []
        try:
            with self.transaktion(f"{len(notizen)} Notizen → {titel}", "notizen_zusammenfuehren"):
                for quelle in notizen:
                    for andere in list(self._notes()):
                        if andere.rel_path in {q.rel_path for q in notizen}:
                            continue
                        roh = andere.path.read_text(encoding="utf-8")
                        ersetzt = _rename_wikilinks(roh, quelle.stem, neuer_stem)
                        if ersetzt != roh:
                            self._track(andere.path)
                            self._write(andere.path, ersetzt)
                            umgebogen.append(andere.rel_path)
        except WriteError as exc:
            return ToolResult(False, f"Verweise nicht umgebogen: {exc}")

        entfernt = []
        if quellen_loeschen:
            for quelle in notizen:
                if self.notiz_loeschen(quelle.stem, grund=f"zusammengeführt in {titel}").ok:
                    entfernt.append(quelle.rel_path)

        self.on_event("struktur", f"{len(notizen)} Notizen zusammengeführt zu {titel}")
        return ToolResult(True, f"{len(notizen)} Notizen zu '{titel}' zusammengeführt.", {
            "pfad": erstellt.daten["pfad"], "zusammengefuehrt": [n.rel_path for n in notizen],
            "verweise_umgebogen": umgebogen, "in_papierkorb": entfernt,
        })


    # ---------------------------------------------------- 17. Notiz aufteilen
    def notiz_aufteilen(self, name: str, ordner: str = "", ab_ebene: int = 2) -> ToolResult:
        """Zerlegt eine überlange Notiz in Kapitelnotizen und macht das Original
        zur Übersicht, die auf die Teile verweist."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        abschnitte = [(h, t) for h, t in split_sections(note.body) if len(h) == int(ab_ebene)]
        if len(abschnitte) < 2:
            return ToolResult(False, f"'{note.stem}' hat keine {int(ab_ebene)} Kapitel zum Aufteilen.")

        zielordner = ordner or note.folder
        erzeugt: List[str] = []
        for headings, text in abschnitte:
            kapitel = headings[-1]
            koerper = re.sub(r"\A#{1,6}\s+.*\n?", "", text.strip())
            if len(koerper.strip()) < 40:
                continue
            ergebnis = self.notiz_erstellen(
                titel=f"{kapitel}", inhalt=koerper, ordner=zielordner,
                tags=note.tags[:4], quellen=[note.stem],
            )
            if ergebnis.ok:
                erzeugt.append(Path(ergebnis.daten["pfad"]).stem)

        if not erzeugt:
            return ToolResult(False, "Keine Kapitel mit genug Inhalt gefunden.")

        uebersicht = (
            f"Diese Notiz wurde in Kapitel aufgeteilt.\n\n## Kapitel\n"
            + "\n".join(f"- [[{s}]]" for s in erzeugt) + "\n"
        )
        self.notiz_ueberschreiben(note.stem, f"# {note.title}\n\n{uebersicht}")
        self.on_event("struktur", f"'{note.stem}' in {len(erzeugt)} Kapitel aufgeteilt")
        return ToolResult(True, f"'{note.stem}' in {len(erzeugt)} Notizen aufgeteilt.",
                          {"pfad": note.rel_path, "kapitel": erzeugt})
