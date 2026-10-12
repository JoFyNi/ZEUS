"""Lesende Werkzeuge - sie fassen die Vault nicht an.

Sie brauchen keine Sicherung, keine Transaktion und keine
Namensschranke: Ein ungefaehr getroffener Name ist beim Lesen ein
brauchbares Ergebnis und beim Schreiben ein Datenverlust.
"""
from __future__ import annotations

import json
from typing import List

from ..vault import (
    extract_section,
    split_sections,
    trim_at_boundary,
    WIKILINK_RE,
    wikilinks_finden,
)

from .ergebnis import ToolResult
from .hilfen import _rename_wikilinks


class LeseWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""


    # ============================================================== WERKZEUGE
    # -------------------------------------------------------------- 1. Suchen
    def notizen_suchen(self, frage: str, ordner: str = "", anzahl: int = 5) -> ToolResult:
        hits = self.index.search(frage, k=max(1, min(int(anzahl or 5), 12)), folder=ordner or None)
        treffer = []
        for hit in hits:
            rel = hit.metadata.get("rel_path", "")
            if rel.startswith("__struktur__"):
                continue
            treffer.append({
                "notiz": hit.metadata.get("stem"),
                "pfad": rel,
                "ordner": hit.metadata.get("folder") or "Wurzel",
                "abschnitt": hit.metadata.get("headings") or "",
                "auszug": trim_at_boundary(hit.text.split("\n\n", 1)[-1], 500),
            })
        return ToolResult(
            ok=bool(treffer),
            meldung=f"{len(treffer)} Treffer für '{frage}'." if treffer else "Nichts gefunden.",
            daten={"treffer": treffer},
        )


    # --------------------------------------------------------------- 2. Lesen
    def notiz_lesen(self, name: str, abschnitt: str = "", max_zeichen: int = 8000) -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"vorschlaege": [n.stem for n in self._notes()[:15]]})
        text = extract_section(note.body, abschnitt, max_zeichen) if abschnitt else \
            trim_at_boundary(note.body, max_zeichen)
        return ToolResult(
            True,
            f"Inhalt von '{note.stem}'" + (f", Abschnitt '{abschnitt}'" if abschnitt else ""),
            {
                "pfad": note.rel_path,
                "ordner": note.folder or "Wurzel",
                "tags": note.tags,
                "links": note.links,
                "abschnitte": [" > ".join(h) for h, _ in split_sections(note.body) if h],
                "inhalt": text,
            },
        )


    # ------------------------------------------------------------ 3. Struktur
    def struktur_anzeigen(self, ordner: str = "") -> ToolResult:
        structure = self._structure()
        if ordner:
            matches = structure.match_folder(ordner, threshold=0.55)
            if matches:
                rel = matches[0][0]
                notes = [n for n in self._notes() if n.folder == rel or n.folder.startswith(rel + "/")]
                return ToolResult(True, f"Inhalt von '{rel}'", {
                    "ordner": rel,
                    "notizen": [{"name": n.stem, "titel": n.title, "tags": n.tags} for n in notes],
                })
            return ToolResult(False, f"Ordner '{ordner}' existiert nicht.",
                              {"vorhandene_ordner": structure.folder_paths})
        return ToolResult(True, "Struktur der Vault", {
            "ordner": structure.folder_paths,
            "anzahl_notizen": structure.note_count(),
            "baum": structure.tree(),
        })


    # ================================================ ERWEITERTE BEARBEITUNG
    # Alles ab hier ist NEU hinzugekommen; keines der bisherigen Werkzeuge
    # wurde entfernt oder in seinem Verhalten geändert.

    # ------------------------------------------------- 13. Abschnitt ersetzen
    # -------------------------------------------- 13b. Blockweise Bearbeitung
    def bloecke_anzeigen(self, name: str, ab: int = 1, anzahl: int = 40) -> ToolResult:
        """Zeigt die adressierbaren Blöcke einer Notiz mit ihrer Nummer.

        Der Lesevorgang VOR dem Ändern. Ohne ihn müsste eine Blocknummer
        geraten werden, und eine geratene Nummer trifft den falschen Absatz -
        dieselbe Klasse Fehler wie ein geratener Notizname, nur schwerer zu
        bemerken, weil die Notiz hinterher plausibel aussieht.
        """
        from ..bloecke import parse_blocks

        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(name))})
        try:
            roh = note.path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"'{note.stem}' nicht lesbar: {exc}")

        bloecke = parse_blocks(roh)
        ab = max(1, int(ab or 1))
        anzahl = max(1, min(int(anzahl or 40), 200))
        ausschnitt = [b for b in bloecke if ab <= b.nummer < ab + anzahl]
        zeilen = [f"{b.nummer:3}. [{b.art}] {b.vorschau()}" for b in ausschnitt]
        rest = len(bloecke) - (ab - 1) - len(ausschnitt)
        if rest > 0:
            zeilen.append(f"     … {rest} weitere (ab={ab + anzahl})")
        return ToolResult(
            True, f"{len(bloecke)} Blöcke in '{note.stem}':\n" + "\n".join(zeilen),
            {"pfad": note.rel_path, "anzahl": len(bloecke),
             "bloecke": [{"nummer": b.nummer, "art": b.art,
                          "ueberschrift": b.ueberschrift,
                          "vorschau": b.vorschau(60)} for b in ausschnitt]})

    def verwandte_finden(self, name: str, anzahl: int = 5) -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        query = f"{note.title} {' '.join(note.tags)} {note.body[:1200]}"
        hits = self.index.search(query, k=int(anzahl) + 4)
        vorschlaege = []
        for hit in hits:
            rel = hit.metadata.get("rel_path", "")
            if rel == note.rel_path or rel.startswith("__struktur__"):
                continue
            stem = hit.metadata.get("stem")
            if any(v["notiz"] == stem for v in vorschlaege):
                continue
            vorschlaege.append({
                "notiz": stem,
                "ordner": hit.metadata.get("folder") or "Wurzel",
                "bereits_verlinkt": stem in note.links,
                "naehe": round(hit.score, 3),
            })
            if len(vorschlaege) >= int(anzahl):
                break
        return ToolResult(True, f"{len(vorschlaege)} verwandte Notizen zu '{note.stem}'.",
                          {"vorschlaege": vorschlaege})


    # ------------------------------------------------------ 10. Link-Wartung
    def links_pruefen(self, reparieren: bool = False) -> ToolResult:
        structure = self._structure()
        kaputt, repariert = [], []
        self.schritt("sammeln")
        with self.transaktion(f"Link-Wartung", "links_reparieren"):
            self.schritt("pruefen")
            for note in list(self._notes()):
                raw = note.path.read_text(encoding="utf-8")
                neu = raw
                # Nur ECHTE Links. Die Notizen unter _System/Code enthalten
                # Docstrings mit [[Notizname]] und [[{note.stem}]] - das sind
                # Beispiele und f-String-Platzhalter, keine Links. Sie zu
                # "reparieren" hieße, fremden Quelltext zu verbiegen.
                echte = set(wikilinks_finden(raw))
                for match in WIKILINK_RE.finditer(raw):
                    ziel = match.group(1).strip()
                    if ziel not in echte or structure.resolve_link(ziel):
                        continue
                    kandidaten = structure.match_note(ziel, threshold=0.78)
                    if reparieren and kandidaten:
                        neu = _rename_wikilinks(neu, ziel, kandidaten[0][0].stem)
                        repariert.append({"notiz": note.stem, "von": ziel,
                                          "nach": kandidaten[0][0].stem})
                    else:
                        kaputt.append({
                            "notiz": note.stem,
                            "link": ziel,
                            "vorschlag": kandidaten[0][0].stem if kandidaten else None,
                        })
                if reparieren and neu != raw:
                    self._track(note.path)
                    self._write(note.path, neu)
        if repariert:
            self.on_event("struktur", f"{len(repariert)} kaputte Links repariert")
        return ToolResult(True, f"{len(kaputt)} offene, {len(repariert)} reparierte Links.",
                          {"kaputt": kaputt[:25], "repariert": repariert[:25]})


    # ------------------------------------------------------------ Rückgängig
    def letzte_aenderungen(self, anzahl: int = 10) -> List[dict]:
        path = self.cfg.work_dir / "journal.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-anzahl:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
