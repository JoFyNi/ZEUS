"""Der Unterbau aller Werkzeuge: Pfade, Namen, Schreiben, Sicherung.

Hier steht alles, was JEDES Werkzeug braucht und was kein Werkzeug
selbst noch einmal machen darf: die Aufloesung eines Notiznamens in
sieben Stufen, das atomare Schreiben mit Rueckleseprobe, Sicherung,
Journal, Transaktion und Ruecknahme - und `ausfuehren`, das jeden
Werkzeugaufruf durch dieselben Pruefungen schickt.

Die Pruefungen stehen bewusst HIER und nicht in den einzelnen
Werkzeugen: Dort koennen sie dreizehnmal vergessen werden, und beim
vierzehnten Werkzeug, das jemand hinzufuegt, fehlen sie sicher.
"""
from __future__ import annotations

import inspect

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ..config import settings
from ..vault import (
    Note,
    VaultStructure,
    fold,
    parse_note,
    safe_filename,
    similarity,
)

from .ergebnis import ToolResult, WriteError, _Transaktion
from .hilfen import (
    _kurz_wert,
    safe_path_fragment,
)
from .schemas import (
    SCHREIBENDE_WERKZEUGE, TOOL_NAMES, TOOL_PARAMS,
    _LIST_PARAMS, _clean_params, _pflicht_pruefen,
    _uebersetzen,
)


class VaultBasis:
    """Zustand, Pfade, Sicherung, Namensaufloesung, Dispatcher."""

    # Aufloesungsstufen, die NICHT sicher genug sind, um darauf zu
    # schreiben - siehe _namen_pruefen().
    UNSICHERE_STUFEN = {"wortueberlappung", "tippfehler", "semantisch"}
    # Parameter, die eine Notiz BENENNEN. Nur sie werden vor einem
    # Schreibvorgang auf einen geratenen Namen geprueft.
    NAMENSPARAMETER = ("name", "von", "nach", "quelle", "notiz")
    # Ab wie vielen Zeilen eine Codedatei beim Import geteilt wird.
    CODE_TEIL_ZEILEN = 400

    def __init__(self, index, cfg=settings, folder_resolver: Optional[Callable] = None,
                 on_event: Optional[Callable[[str, str], None]] = None,
                 bestaetigung: Optional[Callable] = None):
        self.cfg = cfg
        self.index = index
        self._git = None                  # Vault-Historie, erst bei Bedarf
        self.folder_resolver = folder_resolver
        self.on_event = on_event or (lambda art, text: None)
        # Wer meldet, WELCHER Schritt eines Werkzeugs gerade läuft. Wird von
        # der Oberfläche gesetzt, damit der Stern des Werkzeugs mitwächst.
        # Standard: niemand hört zu, und das kostet nichts.
        self.on_schritt = None
        self._laufendes_werkzeug = ""
        from ..protokoll import protokoll as _protokoll
        self.log = _protokoll(cfg)
        # Rückfrage vor einer Änderung. Wird von der Oberfläche gesetzt und
        # nur ausgewertet, wenn CONFIRM_WRITES=true ist. Vorher war diese
        # Einstellung eine Attrappe: Sie stand in der Konfiguration und wurde
        # nirgends gelesen - man verlässt sich darauf und bekommt nie eine
        # Rückfrage zu sehen.
        self.bestaetigung = bestaetigung
        self._tx: Optional[_Transaktion] = None
        self._letzter_einzelschnappschuss: List[dict] = []
        self._neue_dateien = 0
        # Über welche Stufe resolve_note zuletzt getroffen hat. Destruktive
        # Werkzeuge prüfen das, bevor sie eine nur ungefähr getroffene Notiz
        # überschreiben.
        self.letzte_aufloesung = ""

    def lauf_zuruecksetzen(self) -> None:
        """Zählwerk für das Limit neuer Notizen zurücksetzen (einmal pro Auftrag)."""
        self._neue_dateien = 0


    # ------------------------------------------------------------- Grundlagen
    @property
    def root(self) -> Path:
        return self.index.structure.vault_root if self.index.structure else self.cfg.vault_path.resolve()

    def _safe_path(self, rel: str) -> Path:
        """Verhindert Ausbrüche aus der Vault (…/.., absolute Pfade)."""
        candidate = (self.root / str(rel).strip().lstrip("/\\")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Pfad liegt außerhalb der Vault: {rel}")
        return candidate

    def _notes(self) -> List[Note]:
        if self.index.structure is None:
            self.index.refresh_structure()
        return self.index.notes

    def _structure(self) -> VaultStructure:
        """Die Vault-Struktur - notfalls jetzt erst aufgebaut.

        Wer ein Werkzeug direkt aufruft, ohne vorher sync() laufen zu lassen
        (z. B. `--codereview`, das nur einen Bericht ablegen will), bekam hier
        ein None und damit einen Absturz mitten im Schreiben. Struktur ist
        billig aufzubauen - also im Zweifel aufbauen statt abstürzen.
        """
        if self.index.structure is None:
            self.index.refresh_structure()
        return self.index.structure

    def _zielordner_waehlen(self, ordner: str) -> str:
        """Wohin eine Notiz mit ausdrücklich genanntem Ordner gehört.

        Vorher lief das über match_folder(threshold=0.6) auf den GANZEN Pfad.
        Das ist für einen einzelnen, vielleicht vertippten Namen richtig
        ("Deep learning" -> "Deep Learning"), für einen mehrstufigen Pfad aber
        falsch: '_System/Code/Pruefungen' landete so in einem völlig anderen
        Ordner, weil irgendein vorhandener Ordner über der Schwelle lag - der
        angeforderte Unterordner wurde nie angelegt.

        Jetzt wird Stufe für Stufe aufgelöst: exakt vorhanden gewinnt, sonst
        ein Tippfehler-Treffer UNTER DEMSELBEN Elternordner, sonst wird die
        Stufe wörtlich genommen und später angelegt.
        """
        sauber = safe_path_fragment(str(ordner).strip().strip("/"))
        if not sauber:
            return ""
        structure = self._structure()
        vorhanden = {p.lower(): p for p in structure.folder_paths}

        if sauber.lower() in vorhanden:
            return vorhanden[sauber.lower()]

        pfad: List[str] = []
        for teil in sauber.split("/"):
            kandidat = "/".join(pfad + [teil])
            treffer = vorhanden.get(kandidat.lower())
            if treffer:
                pfad = treffer.split("/")
                continue
            eltern = "/".join(pfad)
            geschwister = [p for p in structure.folder_paths
                           if p.rsplit("/", 1)[0] == eltern] if eltern else \
                          [p for p in structure.folder_paths if "/" not in p]
            bester, bestwert = "", 0.7          # nur echte Tippfehler
            for geschwisterpfad in geschwister:
                wert = similarity(teil, geschwisterpfad.rsplit("/", 1)[-1])
                if wert > bestwert:
                    bester, bestwert = geschwisterpfad.rsplit("/", 1)[-1], wert
            pfad.append(bester or teil)
        return "/".join(pfad)

    def notiz_exakt(self, name: str) -> Optional[Note]:
        """Nur ein EXAKTER Treffer - keine Ähnlichkeit, keine Semantik.

        Für die Frage "gibt es schon eine Notiz mit genau diesem Titel?" ist
        resolve_note die falsche Wahl: Es sucht notfalls semantisch weiter und
        liefert IRGENDEINE thematisch verwandte Notiz. Wer damit anschließend
        überschreibt, zerstört fremde Notizen - genau das ist passiert:
        'Code-Prüfung 2026-08-24' landete in
        'Deep Learning/Lauffähiger_Code_für_Agentenarchitekturen.md'.
        """
        if not name:
            return None
        roh = re.sub(r"\.md$", "", str(name).strip().strip("[]").strip(),
                     flags=re.IGNORECASE)
        if not roh:
            return None
        nadel = roh.lower()
        gefaltet = fold(roh)
        for note in self._notes():
            if note.rel_path.lower() in (nadel, nadel + ".md"):
                return note
            if nadel in {note.stem.lower(), note.title.lower(),
                         *(a.lower() for a in note.aliases)}:
                return note
            # Unterstriche statt Leerzeichen sind dieselbe Notiz, mehr nicht.
            if gefaltet and gefaltet in {fold(note.stem), fold(note.title)}:
                return note
        return None

    def resolve_note(self, name: str) -> Optional[Note]:
        """Findet eine Notiz anhand Pfad, Dateiname, Titel, Alias - oder ungefähr.

        Sechs Stufen, von streng nach großzügig. Der Grund für die letzten drei:
        Ein Modell schreibt "Erfolgreiche Optimierung von nicht-linearen Modellen",
        während die Datei "Optimierung_nichtlinearer_Modelle.md" heißt. Rein
        buchstabenweise Ähnlichkeit findet das nicht - Wortüberlappung und
        semantische Suche schon.
        """
        if not name:
            return None
        raw = str(name).strip().strip("[]").strip()
        raw = re.sub(r"\.md$", "", raw, flags=re.IGNORECASE)
        if not raw:
            return None
        # '*' oder 'alle' ist kein Notizname. Das Modell hat das im Messlauf
        # versucht ("Keine Notiz namens '*' gefunden") - die Meldung sagte
        # nicht, dass Platzhalter hier grundsaetzlich nicht gehen.
        if raw in ("*", "**", "?", "alle", "all", "*.md"):
            self.letzte_aufloesung = "platzhalter"
            return None
        notes = self._notes()
        if not notes:
            return None

        # 1) exakter Pfad
        for note in notes:
            if note.rel_path.lower() in (raw.lower(), raw.lower() + ".md"):
                return self._gemerkt(note, "pfad")
        # 2) exakter Name, Titel oder Alias
        needle = raw.lower()
        for note in notes:
            if needle in {note.stem.lower(), note.title.lower(),
                          *(a.lower() for a in note.aliases)}:
                return self._gemerkt(note, "name")
        # 3) Unterstriche/Bindestriche ignorieren ("Lernende Agenten" ↔ "Lernende_Agenten")
        gefaltet = fold(raw)
        for note in notes:
            if gefaltet in {fold(note.stem), fold(note.title),
                            *(fold(a) for a in note.aliases)}:
                return self._gemerkt(note, "gefaltet")
        # 4) Pfadende
        for note in notes:
            if note.rel_path.lower().endswith("/" + needle + ".md"):
                return self._gemerkt(note, "pfadende")
        # 5) Wortüberlappung - deckt längere Umschreibungen ab
        treffer = self._per_wortueberlappung(raw, notes)
        if treffer:
            return self._gemerkt(treffer, "wortueberlappung")
        # 6) Buchstabenähnlichkeit (Tippfehler)
        best, best_score = None, 0.74
        for note in notes:
            score = max(similarity(raw, note.stem), similarity(raw, note.title))
            if score > best_score:
                best, best_score = note, score
        if best:
            return self._gemerkt(best, "tippfehler")
        # 7) letzte Instanz: semantische Suche - aber nur mit Beleg.
        #    Ohne Schwelle liefert die Vektorsuche IMMER einen Treffer, auch für
        #    ein Thema, das in der Vault gar nicht vorkommt. Deshalb muss der
        #    gefundene Name mindestens ein bedeutungstragendes Wort teilen.
        from ..index import tokens

        gesucht = set(tokens(raw))
        if not gesucht:
            return None
        try:
            for hit in self.index.search(raw, k=3, include_structure=False):
                kandidat = self.index.note_by_rel(hit.metadata.get("rel_path", ""))
                if not kandidat:
                    continue
                kennzeichen = set(tokens(f"{kandidat.stem} {kandidat.title} "
                                         f"{' '.join(kandidat.tags)}"))
                if gesucht & kennzeichen:
                    return self._gemerkt(kandidat, "semantisch")
        except Exception:
            pass
        return None

    def _gemerkt(self, note: Note, stufe: str) -> Note:
        self.letzte_aufloesung = stufe
        return note

    def _per_wortueberlappung(self, raw: str, notes: Sequence[Note]) -> Optional[Note]:
        """Wie viele bedeutungstragende Wörter teilen sich beide Namen?

        Der Nenner ist das LÄNGERE der beiden Namen, und das ist der ganze
        Punkt. Vorher stand hier `min()`, und damit reichte ein einziges
        gemeinsames Wort, sobald der Notizname kurz war:

            'ARVIS Architektur Overview'  →  1 gemeinsames Wort
            'Architektur'                 →  min(3, 1) = 1  →  Wert 1,00

        Der Agent wollte eine NEUE Übersichtsnotiz verknüpfen und hat
        stattdessen Joshs bestehende Notiz 'Architektur' erwischt - drei
        Verknüpfungen landeten an der falschen Stelle, sichtbar nur in einer
        beiläufigen Zeile im Protokoll.

        Mit `max()` ergibt derselbe Fall 0,33 und wird abgelehnt, während die
        Fälle, für die diese Stufe gebaut wurde ('Lernende Agenten' ↔
        'Lernende_Agenten'), bei 1,00 bleiben. Der Nenner entscheidet hier
        über Datenverlust, nicht über Bequemlichkeit.
        """
        from ..index import tokens

        gesucht = set(tokens(raw))
        if not gesucht:
            return None
        bester, bestwert = None, 0.0
        for note in notes:
            vorhanden = set(tokens(f"{note.stem} {note.title}"))
            if not vorhanden:
                continue
            gemeinsam = gesucht & vorhanden
            if not gemeinsam:
                continue
            wert = len(gemeinsam) / max(len(gesucht), len(vorhanden))
            if wert > bestwert:
                bester, bestwert = note, wert
        return bester if bestwert >= 0.7 else None

    def aehnliche_namen(self, name: str, anzahl: int = 5) -> List[str]:
        """Vorschläge, wenn ein Name nicht aufgelöst werden konnte."""
        from ..index import tokens

        gesucht = set(tokens(str(name)))
        bewertet = []
        for note in self._notes():
            vorhanden = set(tokens(f"{note.stem} {note.title}"))
            ueberlappung = len(gesucht & vorhanden) / max(1, len(gesucht | vorhanden))
            bewertet.append((max(similarity(str(name), note.stem), ueberlappung), note.stem))
        bewertet.sort(key=lambda kv: -kv[0])
        return [stem for _, stem in bewertet[:anzahl]]


    # ------------------------------------------------- Protokoll / Guardrails
    def _log(self, ereignis: str, **felder) -> None:
        if not self.cfg.log_enabled:
            return
        try:
            log_dir = self.cfg.work_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            zeile = {"zeit": dt.datetime.now().isoformat(timespec="milliseconds"),
                     "ereignis": ereignis, **felder}
            with open(log_dir / f"jarvis-{dt.date.today():%Y%m%d}.jsonl", "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Absicht: Das Protokoll ist Beiwerk. Es darf einen Schreibvorgang
            # niemals scheitern lassen, nur weil der Logordner klemmt.
            pass

    def _guard(self, path: Path, inhalt: str = "", neue_datei: bool = False) -> Optional[str]:
        """Gibt einen Ablehnungsgrund zurück - oder None, wenn alles erlaubt ist.

        Das Limit zählt bewusst nur NEU ANGELEGTE Notizen. Folgeänderungen
        (Verlinkung, Tag-Pflege, Backlink-Umbau) dürfen es nicht aufbrauchen,
        sonst blockiert sich der Agent nach einem großen Stapel selbst.
        """
        try:
            rel = path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return f"Pfad liegt außerhalb der Vault: {path}"
        for geschuetzt in self.cfg.protected_list:
            if rel == geschuetzt or rel.startswith(geschuetzt.rstrip("/") + "/"):
                return f"'{geschuetzt}' ist als geschützt konfiguriert (PROTECTED_FOLDERS)."
        if inhalt and len(inhalt) > self.cfg.max_note_chars:
            return f"Inhalt zu groß ({len(inhalt)} > MAX_NOTE_CHARS={self.cfg.max_note_chars})."
        if neue_datei and 0 < self.cfg.max_files_per_run <= self._neue_dateien:
            return (f"Limit von {self.cfg.max_files_per_run} neuen Notizen pro Auftrag erreicht "
                    f"(MAX_FILES_PER_RUN).")
        return None


    # ------------------------------------------------------ Backup / Journal
    def _backup(self, path: Path) -> Optional[str]:
        if not self.cfg.backups_enabled or not path.exists():
            return None
        target_dir = self.cfg.work_dir / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = target_dir / f"{stamp}_{safe_filename(path.stem)}.md"
        shutil.copy2(path, target)
        return str(target.relative_to(self.root))

    def _journal(self, aktion: str, beschreibung: str, dateien: Sequence[dict]) -> None:
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "zeit": dt.datetime.now().isoformat(timespec="seconds"),
            "aktion": aktion,
            "beschreibung": beschreibung,
            "dateien": list(dateien),
        }
        with open(self.cfg.work_dir / "journal.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _snapshot(self, path: Path) -> dict:
        rel = path.resolve().relative_to(self.root).as_posix()
        return {"rel": rel, "backup": self._backup(path), "existierte": path.exists()}


    # ------------------------------------------------------- Schreiben intern
    def _atomic_write(self, path: Path, content: str) -> None:
        """Erst in eine Temp-Datei, fsync, dann atomar umbenennen.

        Damit kann es keine halb geschriebene Notiz geben - selbst wenn der
        Prozess mitten im Schreiben stirbt, bleibt die alte Datei intakt.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    # Absicht: Aufräumen der Zwischendatei. Bleibt sie liegen,
                    # ist das folgenlos - der eigentliche Fehler steht schon fest.
                    pass

    def _write(self, path: Path, content: str, neue_datei: bool = False) -> None:
        """Schreiben MIT Rücklesekontrolle und Wiederholung.

        Es wird erst als erledigt gemeldet, wenn die Datei auf der Platte
        existiert, byteweise dem Soll entspricht und wieder parsebar ist.
        """
        grund = self._guard(path, content, neue_datei=neue_datei)
        if grund:
            raise WriteError(grund)

        if self.cfg.dry_run:
            self._log("dry_run_write", pfad=str(path), zeichen=len(content))
            return

        soll = hashlib.sha1(content.encode("utf-8")).hexdigest()
        versuche = max(1, self.cfg.write_retries)
        letzter_fehler = ""

        for versuch in range(1, versuche + 1):
            try:
                self._atomic_write(path, content)
                # --- Backend-Kontrolle: wirklich passiert? ---
                if not path.exists():
                    raise WriteError("Datei existiert nach dem Schreiben nicht.")
                zurueck = path.read_text(encoding="utf-8")
                ist = hashlib.sha1(zurueck.encode("utf-8")).hexdigest()
                if ist != soll:
                    raise WriteError(
                        f"Inhalt weicht ab (erwartet {len(content)} Zeichen, "
                        f"gelesen {len(zurueck)})."
                    )
                if parse_note(path, self.root) is None:
                    raise WriteError("Datei ist nach dem Schreiben nicht lesbar.")
                break
            except (OSError, WriteError) as exc:
                letzter_fehler = str(exc)
                self._log("write_retry", pfad=str(path), versuch=versuch, fehler=letzter_fehler)
                if versuch == versuche:
                    self._log("write_failed", pfad=str(path), fehler=letzter_fehler)
                    raise WriteError(
                        f"Schreiben von '{path.name}' nach {versuche} Versuchen fehlgeschlagen: "
                        f"{letzter_fehler}"
                    )
                time.sleep(0.25 * versuch)

        note = parse_note(path, self.root)
        if note:
            self.index.add_note_to_index(note)
        self.index.refresh_structure()
        self.schritt("schreiben")
        self.log.aenderung(
            f"{'angelegt' if neue_datei else 'geändert'}: {path.name}",
            self._laufendes_werkzeug or "vault",
            datei=str(path), zeichen=len(content), neu=bool(neue_datei))
        if neue_datei:
            self._neue_dateien += 1
        self._log("write_ok", pfad=str(path), zeichen=len(content), neu=neue_datei)

    def _remove_from_index(self, rel_path: str) -> None:
        ids = self.index.manifest.get("files", {}).pop(rel_path, {}).get("ids", [])
        if ids:
            self.index._delete_ids(ids)


    # ------------------------------------------------------- Schrittmeldung
    def schritt(self, schluessel: str) -> None:
        """Sagt, welcher innere Schritt gerade läuft - mehr nicht.

        Absichtlich anspruchslos: ein Aufruf, kein Zustand, kein Rückgabewert.
        Ein Werkzeug darf daran nicht scheitern können, deshalb wird jeder
        Fehler des Zuhörers geschluckt. Wer nicht zuhört, kostet nichts.
        """
        melder = self.on_schritt
        if melder is None:
            return
        try:
            melder(self._laufendes_werkzeug or "", str(schluessel))
        except Exception:                                # noqa: BLE001
            pass


    # ------------------------------------------------------------ Transaktion
    @contextmanager
    def transaktion(self, beschreibung: str, aktion: str = "stapel"):
        """Mehrere Änderungen als eine Einheit - mit Rollback im Fehlerfall.

        Verschachtelte Aufrufe klinken sich in die äußere Transaktion ein, damit
        ein Stapel von zehn Notizen ein einziger Journaleintrag bleibt und
        gemeinsam zurückgenommen werden kann.
        """
        if self._tx is not None:          # bereits in einer Transaktion
            yield self._tx
            return

        self.schritt("sichern")
        tx = _Transaktion(aktion=aktion, beschreibung=beschreibung)
        self._tx = tx
        try:
            yield tx
        except Exception:
            self._rollback(tx)
            self._tx = None
            raise
        else:
            self._tx = None
            if tx.dateien:
                self._journal(tx.aktion, tx.beschreibung, tx.dateien)
                # Ein Commit je Transaktion, nicht je Datei: Ein Stapel von
                # zehn Notizen ist EINE Aenderung. Schlaegt der Commit fehl -
                # kein Git installiert, kein Repository, Index gesperrt -,
                # wird das protokolliert und weitergearbeitet. Ein fehlendes
                # Programm darf das Schreiben von Notizen nicht verhindern.
                self._vault_sichern(tx.aktion, tx.beschreibung)

    def _vault_sichern(self, aktion: str, beschreibung: str) -> None:
        """Haengt die Vault-Historie an eine abgeschlossene Transaktion."""
        if not getattr(self.cfg, "vault_git", False):
            return
        try:
            if self._git is None:
                from ..vaultgit import Vaultgit
                self._git = Vaultgit(self.root, melden=self._melden_leise)
            self._git.sichern(beschreibung, aktion)
        except Exception as exc:                       # bewusst alles
            self._melden_leise(f"Vault-Historie uebersprungen: "
                               f"{type(exc).__name__}: {exc}")

    def _melden_leise(self, text: str) -> None:
        """Meldungen der Historie gehoeren ins Protokoll, nicht in den Chat."""
        melden = getattr(self, "on_event", None)
        if callable(melden):
            try:
                melden("warnung", text)
            except Exception:
                pass

    def _track(self, path: Path) -> None:
        """Vor jeder Änderung den Ist-Zustand sichern."""
        eintrag = self._snapshot(path)
        if self._tx is not None:
            self._tx.dateien.append(eintrag)
        else:
            self._letzter_einzelschnappschuss = [eintrag]

    def _rollback(self, tx: "_Transaktion") -> None:
        for eintrag in reversed(tx.dateien):
            ziel = self.root / eintrag["rel"]
            backup = eintrag.get("backup")
            try:
                if backup:
                    quelle = self.root / backup
                    if quelle.exists():
                        ziel.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(quelle, ziel)
                elif not eintrag.get("existierte", False) and ziel.exists():
                    self._remove_from_index(eintrag["rel"])
                    ziel.unlink()
            except OSError as exc:
                self._log("rollback_fehler", pfad=eintrag["rel"], fehler=str(exc))
        self.index.refresh_structure()
        self._log("rollback", aktion=tx.aktion, dateien=len(tx.dateien))

    def _namen_pruefen(self, werkzeug: str, parameter: dict) -> Optional[ToolResult]:
        """Verweigert einen Schreibzugriff auf eine nur geratene Notiz."""
        for schluessel in self.NAMENSPARAMETER:
            roh = parameter.get(schluessel)
            if not roh or not isinstance(roh, str):
                continue
            note = self.resolve_note(roh)
            if note is None:
                continue                      # das Werkzeug meldet das selbst
            if self.letzte_aufloesung not in self.UNSICHERE_STUFEN:
                continue
            if fold(roh) == fold(note.stem) or fold(roh) == fold(note.title):
                continue                      # nur andere Schreibweise
            return ToolResult(
                False,
                f"'{roh}' konnte nur ungefähr auf '{note.stem}' zurückgeführt werden "
                f"({self.letzte_aufloesung}). Bei einem schreibenden Werkzeug wird "
                f"hier nicht geraten – eine Änderung an der falschen Notiz sieht wie "
                f"Erfolg aus und ist keiner. Nimm den Namen buchstabengetreu aus der "
                f"Vault (struktur_anzeigen oder notizen_suchen), oder lege die Notiz "
                f"mit notiz_erstellen an, falls es sie noch nicht gibt.",
                {"parameter": schluessel, "gemeint_vielleicht": note.stem,
                 "aehnliche": self.aehnliche_namen(roh)})
        return None

    def ausfuehren(self, werkzeug: str, parameter: dict) -> ToolResult:
        funktion = getattr(self, werkzeug, None)
        if werkzeug not in TOOL_NAMES or funktion is None:
            # Auch das gehört ins Protokoll: Ein angefordertes Werkzeug, das es
            # nicht gibt, ist ein Fähigkeitsdefizit - und das will man sehen,
            # nicht nur in einer Antwort lesen.
            self.log.fehler(f"Unbekanntes Werkzeug angefordert: '{werkzeug}'", "werkzeuge")
            return ToolResult(False, f"Unbekanntes Werkzeug '{werkzeug}'.",
                              {"verfuegbar": sorted(TOOL_NAMES)})

        # Feldnamen geradeziehen, BEVOR irgendetwas geprüft wird - sonst
        # prüft die Platzhalter- und Pflichtkontrolle Felder, die das Werkzeug
        # nie zu sehen bekommt, und meldet Fehler an der falschen Stelle.
        parameter = _uebersetzen(werkzeug, parameter or {})

        # Platzhalter statt Name. Im Messlauf stand "Keine Notiz namens '*'
        # gefunden" - das sagt nicht, dass Platzhalter hier grundsätzlich nicht
        # gehen und welches Werkzeug stattdessen gemeint ist.
        for schluessel in ("name", "titel", "von", "quelle", "alt"):
            wert = str((parameter or {}).get(schluessel, "")).strip()
            if wert in ("*", "**", "?", "*.md", "alle", "all"):
                return ToolResult(
                    False,
                    f"'{schluessel}={wert}' ist ein Platzhalter, kein Notizname. "
                    f"Werkzeuge arbeiten immer an EINER benannten Notiz. "
                    f"Für mehrere: erst notizen_suchen oder struktur_anzeigen, "
                    f"dann {werkzeug} einmal je Treffer aufrufen. "
                    f"Für die ganze Vault gibt es vault_wartung, links_pruefen, "
                    f"links_bereinigen und auto_verlinken.",
                    {"platzhalter": wert, "stattdessen": ["notizen_suchen",
                                                          "struktur_anzeigen"]})

        # Liste, wo ein einzelner Name erwartet wird. Vorher wurde daraus per
        # str() die Zeichenfolge "['a', 'b']" - die findet keine Notiz, und die
        # Meldung ("Notiz '…' nicht gefunden") verriet die Ursache nicht.
        for schluessel, wert in (parameter or {}).items():
            if (isinstance(wert, (list, tuple)) and schluessel not in _LIST_PARAMS
                    and schluessel in TOOL_PARAMS.get(werkzeug, [])):
                return ToolResult(
                    False,
                    f"'{schluessel}' erwartet EINEN Wert, bekam aber eine Liste mit "
                    f"{len(wert)} Einträgen. Rufe {werkzeug} einmal je Eintrag auf.",
                    {"erhalten": [str(w)[:40] for w in list(wert)[:5]],
                     "erwartet": "einzelner Text"})
        # Geratener Name + Schreibzugriff = stiller Datenverlust.
        #
        # resolve_note() löst absichtlich großzügig auf - fürs LESEN ist das
        # richtig, ein ungefährer Treffer ist besser als keiner. Beim SCHREIBEN
        # kehrt sich das um: 'ARVIS Architektur Overview' wurde auf die
        # bestehende Notiz 'Architektur' zurückgeführt, und drei Verknüpfungen
        # landeten dort. Gemeldet wurde Erfolg.
        #
        # Die Prüfung steht hier statt in den einzelnen Werkzeugen, weil sie
        # sonst dreizehnmal vergessen werden kann - und beim vierzehnten
        # Werkzeug, das jemand hinzufügt, sicher fehlt.
        self._laufendes_werkzeug = werkzeug
        schreibend = werkzeug in SCHREIBENDE_WERKZEUGE
        if schreibend:
            self.schritt("pruefen")
            fehler = self._namen_pruefen(werkzeug, parameter or {})
            if fehler is not None:
                return fehler

        # Erst jetzt auf Vollständigkeit prüfen - NACH der Namenskontrolle.
        # Ein geratener Notizname ist der gefährlichere Fehler: Er schreibt in
        # die falsche Datei und meldet Erfolg. Ein fehlendes Pflichtfeld führt
        # nur dazu, dass nichts passiert. Die gefährlichere Prüfung gehört
        # zuerst, sonst verdeckt die harmlose Meldung die ernste.
        fehlend = _pflicht_pruefen(werkzeug, parameter)
        if fehlend is not None:
            self.log.fehler(fehlend.meldung, werkzeug)
            return fehlend

        sauber = _clean_params(werkzeug, parameter)
        try:
            # Erst pruefen, ob die Argumente ueberhaupt zur Signatur passen.
            # Danach ist JEDER TypeError einer aus dem Werkzeuginneren und
            # darf nicht mehr dem Aufrufer angelastet werden.
            inspect.signature(funktion).bind(**sauber)
        except TypeError as exc:
            self.log.fehler(f"Falsche Parameter: {exc}", werkzeug)
            return ToolResult(False, f"Falsche Parameter fuer '{werkzeug}': {exc}",
                              {"erwartet": TOOL_PARAMS.get(werkzeug, [])})
        try:
            ergebnis = funktion(**sauber)
            # "verbuchen" erst NACH dem Werkzeug: Index, Journal und
            # Strichliste sind zu dem Zeitpunkt wirklich nachgezogen. Vorher
            # gemeldet wäre es eine Behauptung.
            geglueckt = ergebnis is not None and getattr(ergebnis, "ok", False)
            if geglueckt:
                self.schritt("verbuchen")
            # Das Protokoll bekommt BEIDES - was geklappt hat und was nicht.
            # Ein Protokoll, das nur Erfolge kennt, beantwortet die einzige
            # Frage nicht, für die man es aufschlägt.
            self.log.melden(
                "erfolg" if geglueckt else "fehler",
                getattr(ergebnis, "meldung", "(ohne Meldung)"),
                werkzeug,
                parameter={k: _kurz_wert(v) for k, v in (parameter or {}).items()})
            return ergebnis
        except Exception as exc:
            # Auch ein TypeError landet hier - die Signatur wurde oben
            # geprueft, er kommt also aus dem Werkzeug selbst. Der Typ
            # gehoert in die Meldung: "Fehler in notiz_erstellen" allein
            # sagt nicht, wonach man suchen soll.
            self.log.fehler(f"{type(exc).__name__}: {exc}", werkzeug)
            return ToolResult(False, f"Fehler in '{werkzeug}': "
                                     f"{type(exc).__name__}: {exc}")
        finally:
            self._laufendes_werkzeug = ""
