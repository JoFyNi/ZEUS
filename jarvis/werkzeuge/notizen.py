"""Notizen anlegen, aendern, verschieben, loeschen, zurueckholen.

Der Schreibzugriff im engeren Sinn. Jede Aenderung hier laeuft
durch `transaktion()` aus basis.py - Sicherung vorher, Journal,
Rollback bei einem Fehlschlag mittendrin.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import List, Optional, Sequence

from ..vault import (
    fold,
    parse_note,
    safe_filename,
    strip_frontmatter,
)

from .ergebnis import ToolResult, WriteError
from .hilfen import (
    _im_ordner, _insert_into_section,
    _rename_wikilinks, _set_frontmatter_list, _set_frontmatter_wert,
    safe_path_fragment,
)


class NotizWerkzeuge:
    """Wird in VaultTools eingemischt - siehe zusammenbau.py."""

    def ordner_anlegen(self, pfad: str) -> ToolResult:
        if ".." in str(pfad) or Path(str(pfad)).is_absolute() or ":" in str(pfad):
            return ToolResult(False, f"Ungültiger Ordnerpfad '{pfad}' – nur Pfade innerhalb der Vault.")
        try:
            target = self._safe_path(safe_path_fragment(pfad))
        except ValueError as exc:
            return ToolResult(False, str(exc))
        if target.exists():
            return ToolResult(True, f"Ordner '{pfad}' existiert bereits.")
        target.mkdir(parents=True, exist_ok=True)
        self.index.refresh_structure()
        self.on_event("ordner", f"Ordner angelegt: {pfad}")
        self._journal("ordner_anlegen", pfad, [])
        return ToolResult(True, f"Ordner '{pfad}' angelegt.")


    # --------------------------------------------------------- 4. Neue Notiz
    def notiz_erstellen(
        self,
        titel: str,
        inhalt: str,
        ordner: str = "",
        tags: Optional[Sequence[str]] = None,
        quellen: Optional[Sequence[str]] = None,
    ) -> ToolResult:
        if not titel or not str(inhalt).strip():
            return ToolResult(False, "Titel und Inhalt dürfen nicht leer sein.")

        structure = self._structure()
        titel = str(titel).strip()
        canonical = structure.canonical_name(titel)

        quell_notizen = [n for n in (self.resolve_note(q) for q in (quellen or [])) if n]

        if ordner:
            zielordner = self._zielordner_waehlen(ordner)
            begruendung = "vom Agenten vorgegeben"
        elif self.folder_resolver:
            # Erst nachsehen, wo thematisch Verwandtes liegt - sonst entscheidet
            # die Zuordnung blind und alles landet im Posteingang.
            suchtext = f"{titel} {' '.join(str(t) for t in (tags or []))} {str(inhalt)[:800]}"
            verwandte = self.index.search(suchtext, k=5, include_structure=False)
            entscheidung = self.folder_resolver(
                canonical, verwandte, str(inhalt),
                hint_folders=[n.folder for n in quell_notizen if n.folder],
            )
            zielordner, begruendung = entscheidung.folder, entscheidung.reason
        else:
            zielordner, begruendung = self.cfg.inbox_folder, "Standardordner"

        try:
            target_dir = self._safe_path(zielordner) if zielordner else self.root
        except ValueError as exc:
            return ToolResult(False, str(exc))
        target_dir.mkdir(parents=True, exist_ok=True)

        stamp = dt.datetime.now()
        path = target_dir / f"{safe_filename(canonical)}.md"
        counter = 2
        while path.exists():
            path = target_dir / f"{safe_filename(canonical)}_{counter}.md"
            counter += 1

        # Bringt der Inhalt schon einen eigenen Kopf mit (Berichte tun das),
        # wird er übernommen statt ein zweiter davorgesetzt. Sonst steht der
        # innere Block als Fließtext in der Notiz - genau so sah der
        # Messbericht vom 24.08. aus: zwei '---'-Blöcke untereinander.
        eigener_kopf, rumpf = strip_frontmatter(str(inhalt).strip())
        hat_eigenen_kopf = bool(eigener_kopf)
        if hat_eigenen_kopf:
            inhalt = rumpf
            for schluessel, wert in eigener_kopf.items():
                if schluessel in ("tags", "tag") and wert:
                    tags = list(tags or []) + [
                        str(t) for t in (wert if isinstance(wert, list) else [wert])]

        alle_tags = sorted({"selbstgelernt", *(str(t).lstrip("#") for t in (tags or []) if str(t).strip())})

        frontmatter = [
            "---",
            f'title: "{canonical}"',
            f"created: {stamp.isoformat(timespec='seconds')}",
            f"source: {self.cfg.assistant_name}",
            f'ordner_begruendung: "{str(begruendung)[:120]}"',
            "tags:",
            *[f"  - {t}" for t in alle_tags],
        ]
        if quell_notizen:
            frontmatter.append("quellen:")
            frontmatter.extend(f'  - "[[{n.stem}]]"' for n in quell_notizen[:8])
        frontmatter.append("---")

        # Beginnt der Rumpf bereits mit der H1-Überschrift, nicht doppeln.
        rumpftext = str(inhalt).strip()
        erste_zeile = rumpftext.splitlines()[0].strip() if rumpftext else ""
        doppelte_ueberschrift = erste_zeile.lstrip("# ").strip().lower() == canonical.lower()
        kopfzeile = "" if doppelte_ueberschrift else f"# {canonical}\n\n"
        body = "\n".join(frontmatter) + f"\n\n{kopfzeile}{rumpftext}\n"
        if quell_notizen:
            body += f"\n## {self.cfg.link_section}\n" + "\n".join(
                f"- [[{n.stem}]]" for n in quell_notizen[:8]
            ) + "\n"

        rel = path.relative_to(self.root).as_posix()
        try:
            with self.transaktion(rel, "notiz_erstellen"):
                self._track(path)
                self._write(path, body, neue_datei=True)
        except WriteError as exc:
            self.on_event("fehler", f"Notiz NICHT angelegt: {rel} – {exc}")
            return ToolResult(False, f"Notiz '{canonical}' konnte nicht angelegt werden: {exc}",
                              {"pfad": rel, "fehler": str(exc)})

        if self.cfg.dry_run:
            return ToolResult(True, f"[Trockenlauf] '{canonical}' würde unter '{rel}' entstehen.",
                              {"pfad": rel, "ordner": zielordner or "Wurzel", "trocken": True,
                               "zeichen": len(body)})
        if not (self.root / rel).exists():   # letzte Kontrolle
            return ToolResult(False, f"Datei '{rel}' fehlt nach dem Schreiben.", {"pfad": rel})
        self.on_event("schreiben", f"Notiz angelegt: {rel}")
        return ToolResult(True, f"Notiz '{canonical}' angelegt.", {
            "pfad": rel, "ordner": zielordner or "Wurzel", "begruendung": str(begruendung),
            "zeichen": len(body),
        })


    # ------------------------------------------------------ 5. Notiz ergänzen
    def notiz_ergaenzen(self, name: str, inhalt: str, abschnitt: str = "") -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        if not str(inhalt).strip():
            return ToolResult(False, "Kein Inhalt zum Ergänzen übergeben.")

        raw = note.path.read_text(encoding="utf-8")
        addition = str(inhalt).strip()
        if abschnitt:
            neu, gefunden = _insert_into_section(raw, abschnitt, addition)
            if not gefunden:
                neu = raw.rstrip() + f"\n\n## {abschnitt}\n\n{addition}\n"
        else:
            neu = raw.rstrip() + f"\n\n{addition}\n"

        try:
            with self.transaktion(note.rel_path, "notiz_ergaenzen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"'{note.stem}' konnte nicht ergänzt werden: {exc}",
                              {"pfad": note.rel_path})
        if addition not in note.path.read_text(encoding="utf-8"):
            return ToolResult(False, f"Ergänzung in '{note.stem}' nicht auffindbar – abgebrochen.",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Notiz ergänzt: {note.rel_path}")
        return ToolResult(True, f"'{note.stem}' ergänzt.", {"pfad": note.rel_path})


    # ---------------------------------------------------- 6. Gezielt ersetzen
    def notiz_bearbeiten(self, name: str, suchtext: str, ersatztext: str, alle: bool = False) -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        raw = note.path.read_text(encoding="utf-8")
        if suchtext not in raw:
            return ToolResult(False, "Suchtext kommt in der Notiz nicht vor – bitte erst notiz_lesen.",
                              {"pfad": note.rel_path})
        neu = raw.replace(suchtext, ersatztext) if alle else raw.replace(suchtext, ersatztext, 1)
        try:
            with self.transaktion(note.rel_path, "notiz_bearbeiten"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"'{note.stem}' konnte nicht bearbeitet werden: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Notiz bearbeitet: {note.rel_path}")
        return ToolResult(True, f"'{note.stem}' bearbeitet.", {"pfad": note.rel_path})

    def notiz_ueberschreiben(self, name: str, inhalt: str) -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.",
                              {"aehnliche": self.aehnliche_namen(str(name))})
        # Überschreiben ersetzt den GESAMTEN Inhalt. Eine nur ungefähr
        # getroffene Notiz zu überschreiben heißt, eine fremde Notiz zu
        # vernichten - genau so landete ein Prüfbericht in
        # 'Lauffähiger_Code_für_Agentenarchitekturen.md'. Ergänzen und
        # Abschnitt-Ersetzen bleiben großzügig, nur das Ganz-Ersetzen nicht.
        if self.letzte_aufloesung in self.UNSICHERE_STUFEN:
            return ToolResult(
                False,
                f"'{name}' trifft keine Notiz genau – am ehesten passt "
                f"'{note.stem}' ({note.rel_path}). Überschreiben ersetzt den "
                f"gesamten Inhalt, deshalb braucht es hier den exakten Namen "
                f"oder Pfad.",
                {"gemeint_vielleicht": note.rel_path,
                 "aehnliche": self.aehnliche_namen(str(name))})
        raw = note.path.read_text(encoding="utf-8")
        frontmatter_block = ""
        match = re.match(r"\A---\s*\n.*?\n---\s*\n", raw, re.DOTALL)
        if match:
            frontmatter_block = match.group(0)
        try:
            with self.transaktion(note.rel_path, "notiz_ueberschreiben"):
                self._track(note.path)
                self._write(note.path, frontmatter_block + str(inhalt).strip() + "\n")
        except WriteError as exc:
            return ToolResult(False, f"'{note.stem}' konnte nicht überschrieben werden: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Notiz neu geschrieben: {note.rel_path}")
        return ToolResult(True, f"'{note.stem}' überschrieben (Frontmatter erhalten).",
                          {"pfad": note.rel_path})


    # ------------------------------------------------- 7. Verschieben/Umbenennen
    def notiz_verschieben(self, name: str, ziel_ordner: str = "", neuer_name: str = "") -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")

        structure = self._structure()
        if ziel_ordner:
            matches = structure.match_folder(ziel_ordner, threshold=0.6)
            zielordner = matches[0][0] if matches else safe_path_fragment(ziel_ordner)
        else:
            zielordner = note.folder

        neuer_stem = safe_filename(neuer_name) if neuer_name else note.stem
        try:
            target_dir = self._safe_path(zielordner) if zielordner else self.root
        except ValueError as exc:
            return ToolResult(False, str(exc))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{neuer_stem}.md"
        if target.exists() and target != note.path:
            return ToolResult(False, f"'{target.relative_to(self.root).as_posix()}' existiert bereits.")

        alt_rel = note.rel_path
        alt_stem = note.stem
        neu_rel = target.relative_to(self.root).as_posix()
        umbenannt: List[str] = []

        try:
            with self.transaktion(f"{alt_rel} -> {neu_rel}", "notiz_verschieben") as tx:
                self._track(note.path)

                # Backlinks in allen anderen Notizen mitziehen
                if neuer_stem != alt_stem:
                    for other in list(self._notes()):
                        if other.rel_path == alt_rel or alt_stem not in other.body:
                            continue
                        raw = other.path.read_text(encoding="utf-8")
                        neu = _rename_wikilinks(raw, alt_stem, neuer_stem)
                        if neu != raw:
                            self._track(other.path)
                            self._write(other.path, neu)
                            umbenannt.append(other.rel_path)

                self._remove_from_index(alt_rel)
                shutil.move(str(note.path), str(target))
                if not target.exists():
                    raise WriteError("Zieldatei existiert nach dem Verschieben nicht.")
                neue_note = parse_note(target, self.root)
                if neue_note:
                    self.index.add_note_to_index(neue_note)
                self.index.refresh_structure()
                tx.dateien.append({"rel": neu_rel, "backup": None, "existierte": False})
        except (WriteError, OSError) as exc:
            return ToolResult(False, f"Verschieben fehlgeschlagen: {exc}", {"pfad": alt_rel})

        self.on_event("struktur", f"Verschoben: {alt_rel} → {neu_rel}")
        return ToolResult(True, f"'{alt_stem}' liegt jetzt unter '{neu_rel}'.", {
            "vorher": alt_rel, "nachher": neu_rel, "links_aktualisiert": umbenannt,
        })


    # -------------------------------------------------------------- 8. Löschen
    def notiz_loeschen(self, name: str, grund: str = "") -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        trash = self.root / self.cfg.trash_folder
        trash.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = trash / f"{note.stem}_{stamp}.md"

        grund_guard = self._guard(note.path)
        if grund_guard:
            return ToolResult(False, grund_guard, {"pfad": note.rel_path})
        try:
            with self.transaktion(note.rel_path, "notiz_loeschen"):
                self._track(note.path)
                self._remove_from_index(note.rel_path)
                shutil.move(str(note.path), str(target))
                self.index.refresh_structure()
        except OSError as exc:
            return ToolResult(False, f"Löschen fehlgeschlagen: {exc}", {"pfad": note.rel_path})
        self.on_event("struktur", f"In den Papierkorb: {note.rel_path}")
        return ToolResult(True, f"'{note.stem}' in den Papierkorb verschoben (nicht endgültig gelöscht).",
                          {"papierkorb": target.relative_to(self.root).as_posix(), "grund": grund})


    # -------------------------------------------- 21. Aus Papierkorb zurück
    def notiz_wiederherstellen(self, name: str, ordner: str = "") -> ToolResult:
        """Holt eine Notiz aus dem Papierkorb zurück."""
        trash = self.root / self.cfg.trash_folder
        if not trash.exists():
            return ToolResult(False, "Der Papierkorb ist leer.")
        gesucht = fold(str(name).replace(".md", ""))
        kandidaten = [p for p in trash.glob("*.md") if gesucht in fold(p.stem)]
        if not kandidaten:
            return ToolResult(False, f"'{name}' liegt nicht im Papierkorb.",
                              {"im_papierkorb": [p.stem for p in trash.glob('*.md')][:20]})
        quelle = max(kandidaten, key=lambda p: p.stat().st_mtime)
        sauberer_name = re.sub(r"_\d{8}_\d{6}$", "", quelle.stem)

        inhalt = quelle.read_text(encoding="utf-8")
        zielordner = ordner or self.cfg.inbox_folder
        treffer = self._structure().match_folder(zielordner, threshold=0.6)
        zielordner = treffer[0][0] if treffer else safe_path_fragment(zielordner)
        ziel = self._safe_path(zielordner) / f"{sauberer_name}.md" if zielordner else \
            self.root / f"{sauberer_name}.md"
        if ziel.exists():
            return ToolResult(False, f"'{ziel.name}' existiert bereits – erst umbenennen.")

        try:
            with self.transaktion(sauberer_name, "notiz_wiederherstellen"):
                self._track(ziel)
                self._write(ziel, inhalt)
        except WriteError as exc:
            return ToolResult(False, f"Wiederherstellen fehlgeschlagen: {exc}")
        self.on_event("struktur", f"Wiederhergestellt: {ziel.relative_to(self.root).as_posix()}")
        return ToolResult(True, f"'{sauberer_name}' wiederhergestellt.",
                          {"pfad": ziel.relative_to(self.root).as_posix()})


    # --------------------------------------------- 22. Ordner umbenennen
    def ordner_umbenennen(self, alt: str, neu: str) -> ToolResult:
        """Benennt einen Ordner um und zieht alle Notizen mit."""
        treffer = self._structure().match_folder(alt, threshold=0.6)
        if not treffer:
            return ToolResult(False, f"Ordner '{alt}' nicht gefunden.",
                              {"vorhandene_ordner": self._structure().folder_paths})
        alt_rel = treffer[0][0]
        neuer_name = safe_filename(str(neu).strip())
        if not neuer_name:
            return ToolResult(False, "Ungültiger neuer Ordnername.")
        elternteil = Path(alt_rel).parent.as_posix()
        neu_rel = f"{elternteil}/{neuer_name}" if elternteil not in ("", ".") else neuer_name

        quelle = self._safe_path(alt_rel)
        ziel = self._safe_path(neu_rel)
        if ziel.exists():
            return ToolResult(False, f"'{neu_rel}' existiert bereits.")
        grund = self._guard(quelle)
        if grund:
            return ToolResult(False, grund)

        betroffen = [n.rel_path for n in self._notes() if _im_ordner(n.folder, alt_rel)]
        for rel in betroffen:
            self._remove_from_index(rel)
        try:
            shutil.move(str(quelle), str(ziel))
        except OSError as exc:
            return ToolResult(False, f"Ordner nicht verschiebbar: {exc}")
        self.index.sync()
        self._journal("ordner_umbenennen", f"{alt_rel} -> {neu_rel}", [])
        self.on_event("struktur", f"Ordner umbenannt: {alt_rel} → {neu_rel}")
        return ToolResult(True, f"Ordner '{alt_rel}' heißt jetzt '{neu_rel}'.",
                          {"vorher": alt_rel, "nachher": neu_rel, "notizen": len(betroffen)})


    # ------------------------------------------------------------- 11. Tags
    def tags_setzen(self, name: str, tags: Sequence[str], modus: str = "ergaenzen") -> ToolResult:
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        neue = [str(t).lstrip("#").strip() for t in (tags or []) if str(t).strip()]
        if not neue:
            return ToolResult(False, "Keine Tags übergeben.")
        bestand = [] if modus == "ersetzen" else list(note.frontmatter.get("tags") or note.tags)
        zusammen = sorted({*(str(t).lstrip("#") for t in bestand), *neue})

        raw = note.path.read_text(encoding="utf-8")
        neu = _set_frontmatter_list(raw, "tags", zusammen)
        try:
            with self.transaktion(note.rel_path, "tags_setzen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Tags konnten nicht gesetzt werden: {exc}",
                              {"pfad": note.rel_path})
        self.on_event("schreiben", f"Tags gesetzt: {note.rel_path}")
        return ToolResult(True, f"Tags von '{note.stem}': {', '.join(zusammen)}", {"tags": zusammen})


    # ------------------------------------------------ 20. Frontmatter setzen
    def frontmatter_setzen(self, name: str, schluessel: str, wert: str) -> ToolResult:
        """Setzt ein beliebiges Frontmatter-Feld (Status, Priorität, Quelle …)."""
        note = self.resolve_note(name)
        if not note:
            return ToolResult(False, f"Keine Notiz namens '{name}' gefunden.")
        schluessel = re.sub(r"[^\w\-]", "", str(schluessel))
        if not schluessel:
            return ToolResult(False, "Ungültiger Feldname.")
        raw = note.path.read_text(encoding="utf-8")
        neu = _set_frontmatter_wert(raw, schluessel, str(wert))
        try:
            with self.transaktion(note.rel_path, "frontmatter_setzen"):
                self._track(note.path)
                self._write(note.path, neu)
        except WriteError as exc:
            return ToolResult(False, f"Feld nicht gesetzt: {exc}", {"pfad": note.rel_path})
        return ToolResult(True, f"'{schluessel}: {wert}' in '{note.stem}' gesetzt.",
                          {"pfad": note.rel_path})

    def rueckgaengig(self) -> ToolResult:
        path = self.cfg.work_dir / "journal.jsonl"
        if not path.exists():
            return ToolResult(False, "Kein Änderungsjournal vorhanden.")
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return ToolResult(False, "Nichts rückgängig zu machen.")
        entry = json.loads(lines[-1])
        wiederhergestellt, geloescht = [], []

        for eintrag in entry.get("dateien", []):
            ziel = self.root / eintrag["rel"]
            backup = eintrag.get("backup")
            if backup:
                quelle = self.root / backup
                if quelle.exists():
                    ziel.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(quelle, ziel)
                    wiederhergestellt.append(eintrag["rel"])
            elif not eintrag.get("existierte", False) and ziel.exists():
                self._remove_from_index(eintrag["rel"])
                ziel.unlink()
                geloescht.append(eintrag["rel"])

        path.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")
        self.index.sync()
        self.on_event("struktur", f"Rückgängig: {entry.get('aktion')} – {entry.get('beschreibung')}")
        return ToolResult(True, f"'{entry.get('aktion')}' zurückgenommen.", {
            "wiederhergestellt": wiederhergestellt, "entfernt": geloescht,
        })
