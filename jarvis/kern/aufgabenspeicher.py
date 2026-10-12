"""Der Aufgabenspeicher: Arbeit, die einen Neustart überlebt.

Bis hierher lebte jede mehrstufige Arbeit im Arbeitsspeicher. `Arbeitsplan`
(gespraech/arbeitsplan.py) kennt Schritte und Zustände - aber wenn die Antwort
fertig ist, ist er weg. Nachgemessen: null Schreibzugriffe im ganzen Modul.

Damit war eine ganze Klasse von Verhalten unmöglich, egal wie gut der Rest
gebaut war:

* Eine Aufgabe, die auf eine Freigabe wartet, kann nicht warten - der Prozess
  gibt die Antwort zurück und vergisst sie.
* Ein Absturz mitten in einer vierteiligen Aufgabe hinterlässt keinen Stand,
  an dem man wieder ansetzen könnte.
* Der Zeitplaner kann nichts einreihen, was länger dauert als eine Anfrage.
* "Woran arbeitest du gerade?" hat keine Antwort, weil es kein Gerade gibt.

Vorhanden waren drei Zustände (`offen`, `erledigt`, `festgefahren` in
aufgaben.py) für etwas anderes: die Nachverfolgung fehlgeschlagener
Werkzeugaufrufe. Das ist eine Pannenliste, kein Aufgabenlebenszyklus.

Zur Wahl der Ablage: SQLite. Gründe, in dieser Reihenfolge:

1. **Atomarität.** Ein Zustandswechsel und sein Protokolleintrag müssen
   zusammen gelingen oder zusammen ausbleiben. Mit JSON-Dateien heißt das
   selbstgebautes Sperren - und das ist genau die Sorte Code, die bei einem
   Absturz zur Unzeit halb geschriebene Zustände hinterlässt.
2. **Nebenläufigkeit.** Der Zeitplaner lässt mehrere Arbeiter laufen
   (kern/scheduler.py). Zwei Arbeiter, die dieselbe JSON-Datei schreiben,
   verlieren einer des anderen Änderung.
3. **Kein neues Paket.** sqlite3 ist in Python enthalten. Die Leitlinie
   "lokal, ohne Konto" bleibt unangetastet.

Was NICHT hierher gehört: Wissen. Notizen bleiben in der Vault, Lektionen im
Lernwerk, Einbettungen im Vektorspeicher. Hier liegt ausschliesslich der
Zustand laufender Arbeit - siehe die Trennung in ENTWICKLUNG.md, Leitlinie
"Die Vault-Struktur ist die Substanz".
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

# ----------------------------------------------------------------- Zustände
# Die Namen folgen dem Lebenszyklus einer Aufgabe, nicht der Technik. Wer
# `WARTET_AUF_FREIGABE` liest, weiss ohne Nachschlagen, was los ist.
NEU = "neu"
ANALYSE = "analyse"
PLANUNG = "planung"
WARTET_AUF_ABHAENGIGKEIT = "wartet_auf_abhaengigkeit"
BEREIT = "bereit"
LAEUFT = "laeuft"
WARTET_AUF_FREIGABE = "wartet_auf_freigabe"
WARTET_AUF_EINGABE = "wartet_auf_eingabe"
PRUEFUNG = "pruefung"
NEUPLANUNG = "neuplanung"
GESCHEITERT = "gescheitert"
PAUSIERT = "pausiert"
FERTIG = "fertig"
ABGEBROCHEN = "abgebrochen"

# Endzustände: Von hier geht es nicht weiter. Ein Wiederaufnehmen legt eine
# neue Aufgabe an, statt eine abgeschlossene wiederzubeleben - sonst wächst
# der Versuchszähler über Wochen und ein Fehlschlag von gestern blockiert den
# Lauf von morgen (dieselbe Lehre wie in kern/scheduler.py).
ENDZUSTAENDE = {FERTIG, ABGEBROCHEN, GESCHEITERT}

# Welcher Wechsel erlaubt ist. Eine Tabelle statt verstreuter if-Abfragen:
# Ein ungültiger Wechsel soll auffallen, wo er passiert, und nicht drei
# Schichten später als unerklärlicher Zustand.
UEBERGAENGE: Dict[str, set] = {
    NEU: {ANALYSE, PLANUNG, BEREIT, ABGEBROCHEN},
    ANALYSE: {PLANUNG, WARTET_AUF_EINGABE, GESCHEITERT, ABGEBROCHEN},
    PLANUNG: {BEREIT, WARTET_AUF_ABHAENGIGKEIT, WARTET_AUF_FREIGABE,
              WARTET_AUF_EINGABE, GESCHEITERT, ABGEBROCHEN},
    # NEUPLANUNG ist hier kein Schoenheitsfehler: Eine Aufgabe, deren
    # Vorbedingung endgueltig gescheitert ist, wartet sonst fuer immer. Sie
    # muss einen anderen Weg bekommen duerfen, ohne selbst zu scheitern.
    WARTET_AUF_ABHAENGIGKEIT: {BEREIT, PAUSIERT, NEUPLANUNG, GESCHEITERT,
                               ABGEBROCHEN},
    BEREIT: {LAEUFT, WARTET_AUF_FREIGABE, PAUSIERT, ABGEBROCHEN},
    # WARTET_AUF_ABHAENGIGKEIT aus LAEUFT heraus: Eine Wurzelaufgabe, die
    # gerade ihren Auftrag zerlegt hat, stellt dabei fest, dass sie auf ihre
    # eigenen Kinder warten muss. Ohne diesen Ausgang muesste sie sich
    # schlafen legen (PAUSIERT) - und pausiert heisst "ein Mensch hat
    # angehalten", nicht "es fehlt noch etwas".
    LAEUFT: {PRUEFUNG, WARTET_AUF_ABHAENGIGKEIT, WARTET_AUF_FREIGABE,
             WARTET_AUF_EINGABE, NEUPLANUNG, GESCHEITERT, PAUSIERT,
             ABGEBROCHEN, FERTIG},
    WARTET_AUF_FREIGABE: {BEREIT, LAEUFT, ABGEBROCHEN, PAUSIERT},
    WARTET_AUF_EINGABE: {BEREIT, LAEUFT, PLANUNG, ABGEBROCHEN, PAUSIERT},
    PRUEFUNG: {FERTIG, NEUPLANUNG, GESCHEITERT},
    NEUPLANUNG: {PLANUNG, BEREIT, GESCHEITERT, ABGEBROCHEN},
    PAUSIERT: {BEREIT, LAEUFT, ABGEBROCHEN},
    # Endzustände haben bewusst keine Ausgänge.
    FERTIG: set(),
    GESCHEITERT: set(),
    ABGEBROCHEN: set(),
}

# Zustände, in denen eine Aufgabe auf einen MENSCHEN wartet. Der Unterschied
# zu "pausiert" ist wichtig: Hier liegt der Ball bei Josh, nicht bei Zeus.
WARTET_AUF_MENSCH = {WARTET_AUF_FREIGABE, WARTET_AUF_EINGABE}

# Risikostufen fuer Freigaben (Master-Prompt §23).
RISIKO_GERING = "gering"
RISIKO_MITTEL = "mittel"
RISIKO_HOCH = "hoch"

# Rangfolge, damit sich Risiken VERGLEICHEN lassen. Ohne sie steht an jeder
# Aufrufstelle wieder eine eigene if-Kette, und zwei davon sind sich nach
# einem halben Jahr uneinig, ob "mittel" nun freigabepflichtig ist.
RISIKO_RANG = {RISIKO_GERING: 0, RISIKO_MITTEL: 1, RISIKO_HOCH: 2}

# --------------------------------------------------------------- Zielzustände
# Ein Ziel ist langlebiger als eine Aufgabe: Es beschreibt das GEWUENSCHTE
# ERGEBNIS und bleibt bestehen, waehrend darunter Aufgaben entstehen,
# scheitern und neu geplant werden.
ZIEL_AKTIV = "aktiv"
ZIEL_ERREICHT = "erreicht"
ZIEL_GESCHEITERT = "gescheitert"
ZIEL_ABGEBROCHEN = "abgebrochen"
ZIEL_PAUSIERT = "pausiert"
ZIEL_ENDZUSTAENDE = {ZIEL_ERREICHT, ZIEL_GESCHEITERT, ZIEL_ABGEBROCHEN}

# Welche ART Aufgabe. Die Unterscheidung braucht der Laeufer: Eine
# Zielaufgabe wird ZERLEGT, eine Schrittaufgabe wird AUSGEFUEHRT. Ohne das
# Merkmal muesste er aus der Elternschaft raten, und Raten ist genau das,
# was dieses Projekt an anderer Stelle schon zweimal gekostet hat.
ART_ZIEL = "ziel"            # Wurzel einer Zerlegung
ART_SCHRITT = "schritt"      # ein ausfuehrbarer Schritt
ART_ABLAUF = "ablauf"        # Schritt eines gespeicherten Ablaufs
ART_WARTUNG = "wartung"      # Selbstpflege
ART_ALLGEMEIN = "allgemein"


class Zustandsfehler(RuntimeError):
    """Ein verbotener Zustandswechsel. Fällt dort auf, wo er versucht wird."""


@dataclass
class Aufgabe:
    """Eine Einheit Arbeit - der Master-Prompt nennt das Universal Task Model."""
    id: str
    titel: str
    beschreibung: str = ""
    eltern_id: Optional[str] = None
    ziel_id: Optional[str] = None
    zustand: str = NEU
    prioritaet: int = 5                     # 1 = dringend, 9 = irgendwann
    versuche: int = 0
    max_versuche: int = 3
    neuplanungen: int = 0
    max_neuplanungen: int = 2
    auftrag: str = ""                       # der Originaltext des Nutzers
    ergebnis: str = ""
    fehler: str = ""
    risiko: str = RISIKO_GERING
    art: str = ART_ALLGEMEIN
    arbeiter: str = ""              # welcher Arbeiter sie gerade haelt
    belegt_bis: float = 0.0         # bis wann die Belegung gilt
    ablauf_lauf_id: Optional[str] = None
    faellig_ab: float = 0.0         # nicht vor diesem Zeitpunkt anfassen
    daten: dict = field(default_factory=dict)
    erstellt: float = field(default_factory=time.time)
    geaendert: float = field(default_factory=time.time)

    @property
    def offen(self) -> bool:
        return self.zustand not in ENDZUSTAENDE

    @property
    def wartet_auf_mensch(self) -> bool:
        return self.zustand in WARTET_AUF_MENSCH

    def stichpunkt(self) -> str:
        marke = {FERTIG: "✔", GESCHEITERT: "✖", ABGEBROCHEN: "–",
                 LAEUFT: "▶", PAUSIERT: "⏸"}.get(self.zustand, "○")
        text = f"{marke} {self.titel}  [{self.zustand}]"
        if self.zustand == GESCHEITERT and self.fehler:
            text += f" – {self.fehler[:70]}"
        return text


@dataclass
class Ziel:
    """Ein gewuenschtes ERGEBNIS - nicht ein Weg dorthin.

    Der Unterschied zur Aufgabe ist der ganze Grund, warum es diesen Typ
    gibt. Eine Aufgabe kann endgueltig scheitern; das Ziel darf deshalb
    nicht mitscheitern, sonst ist Neuplanung unmoeglich. Genau daran ist
    die erste Fassung gescheitert: Der Arbeitsplan WAR das Ziel, und mit
    seinem letzten Schritt war auch der Auftrag erledigt - ob er erreicht
    war oder nicht.

    `ergebnis` ist bewusst Text und keine Pruefvorschrift: Woran sich
    Erfolg messen laesst, formuliert der Nutzer oder der Planer. Was sich
    MECHANISCH pruefen laesst, prueft die Verifikation - die sitzt eine
    Ebene tiefer, am einzelnen Werkzeugaufruf.
    """
    id: str
    beschreibung: str
    ergebnis: str = ""
    bedingungen: List[str] = field(default_factory=list)
    prioritaet: int = 5
    frist: Optional[float] = None
    zustand: str = ZIEL_AKTIV
    quelle: str = "nutzer"
    auftrag: str = ""
    befund: str = ""
    daten: dict = field(default_factory=dict)
    erstellt: float = field(default_factory=time.time)
    geaendert: float = field(default_factory=time.time)

    @property
    def offen(self) -> bool:
        return self.zustand not in ZIEL_ENDZUSTAENDE

    def stichpunkt(self) -> str:
        marke = {ZIEL_ERREICHT: "\u2714", ZIEL_GESCHEITERT: "\u2716",
                 ZIEL_ABGEBROCHEN: "\u2013", ZIEL_PAUSIERT: "\u23f8"}.get(
                     self.zustand, "\u25b6")
        text = f"{marke} {self.beschreibung[:90]}  [{self.zustand}]"
        if self.befund:
            text += f" \u2013 {self.befund[:70]}"
        return text


_SCHEMA = """
CREATE TABLE IF NOT EXISTS aufgaben (
    id              TEXT PRIMARY KEY,
    titel           TEXT NOT NULL,
    beschreibung    TEXT DEFAULT '',
    eltern_id       TEXT,
    ziel_id         TEXT,
    zustand         TEXT NOT NULL,
    prioritaet      INTEGER DEFAULT 5,
    versuche        INTEGER DEFAULT 0,
    max_versuche    INTEGER DEFAULT 3,
    neuplanungen    INTEGER DEFAULT 0,
    max_neuplanungen INTEGER DEFAULT 2,
    auftrag         TEXT DEFAULT '',
    ergebnis        TEXT DEFAULT '',
    fehler          TEXT DEFAULT '',
    risiko          TEXT DEFAULT 'gering',
    art             TEXT DEFAULT 'allgemein',
    arbeiter        TEXT DEFAULT '',
    belegt_bis      REAL DEFAULT 0,
    ablauf_lauf_id  TEXT,
    faellig_ab      REAL DEFAULT 0,
    daten           TEXT DEFAULT '{}',
    erstellt        REAL NOT NULL,
    geaendert       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zustand ON aufgaben(zustand);
CREATE INDEX IF NOT EXISTS idx_eltern  ON aufgaben(eltern_id);

-- Abhaengigkeiten als eigene Tabelle statt als Liste im Datensatz: So laesst
-- sich "was blockiert diese Aufgabe" und "was blockiert sie" gleich gut
-- fragen, und eine geloeschte Aufgabe hinterlaesst keine toten Verweise.
CREATE TABLE IF NOT EXISTS abhaengigkeiten (
    aufgabe_id   TEXT NOT NULL,
    benoetigt_id TEXT NOT NULL,
    PRIMARY KEY (aufgabe_id, benoetigt_id)
);

-- Jeder Zustandswechsel, mit Grund und Urheber. Das ist der Audit Trail aus
-- Master-Prompt §28: WER hat WANN WAS WARUM geaendert.
CREATE TABLE IF NOT EXISTS verlauf (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    aufgabe_id TEXT NOT NULL,
    von        TEXT,
    nach       TEXT NOT NULL,
    grund      TEXT DEFAULT '',
    akteur     TEXT DEFAULT 'zeus',
    zeit       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verlauf ON verlauf(aufgabe_id, zeit);

-- Ergebnisse als verwaltete Artefakte (Master-Prompt §26).
CREATE TABLE IF NOT EXISTS artefakte (
    id         TEXT PRIMARY KEY,
    aufgabe_id TEXT NOT NULL,
    art        TEXT NOT NULL,
    ort        TEXT NOT NULL,
    beschreibung TEXT DEFAULT '',
    erstellt   REAL NOT NULL
);

-- Freigaben: die Frage an den Menschen, ihr Kontext und die Antwort.
CREATE TABLE IF NOT EXISTS freigaben (
    id         TEXT PRIMARY KEY,
    aufgabe_id TEXT NOT NULL,
    frage      TEXT NOT NULL,
    risiko     TEXT DEFAULT 'mittel',
    moeglichkeiten TEXT DEFAULT '[]',
    antwort    TEXT DEFAULT '',
    schluessel TEXT DEFAULT '',
    erteilt    INTEGER DEFAULT 0,
    gefragt    REAL NOT NULL,
    beantwortet REAL
);
CREATE INDEX IF NOT EXISTS idx_freigabe ON freigaben(aufgabe_id, erteilt);

-- ZIELE. Der Unterschied zur Aufgabe ist nicht kosmetisch: Eine Aufgabe ist
-- ein Weg, ein Ziel ist ein Ergebnis. Scheitert eine Aufgabe endgueltig, darf
-- das Ziel weiterbestehen und einen anderen Weg bekommen - genau das ist
-- Neuplanung. Ohne eigene Tabelle waere das Ziel die oberste Aufgabe, und
-- deren Endzustand wuerde die Neuplanung ausschliessen.
CREATE TABLE IF NOT EXISTS ziele (
    id            TEXT PRIMARY KEY,
    beschreibung  TEXT NOT NULL,
    ergebnis      TEXT DEFAULT '',      -- woran sich Erfolg messen laesst
    bedingungen   TEXT DEFAULT '[]',    -- Randbedingungen des Nutzers
    prioritaet    INTEGER DEFAULT 5,
    frist         REAL,
    zustand       TEXT NOT NULL DEFAULT 'aktiv',
    quelle        TEXT DEFAULT 'nutzer',-- nutzer | zeitplan | ereignis | ablauf
    auftrag       TEXT DEFAULT '',      -- der Originaltext
    befund        TEXT DEFAULT '',      -- warum erreicht/gescheitert
    daten         TEXT DEFAULT '{}',
    erstellt      REAL NOT NULL,
    geaendert     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ziel_zustand ON ziele(zustand);

-- LAEUFE: die Telemetrie je Aufgabe. Master-Prompt §31 verlangt, dass eine
-- Ausfuehrung nachvollziehbar bleibt statt zu einer undurchsichtigen
-- Modellinteraktion zu werden. Eine Zeile je Schritt, mit Ein- und Ausgabe,
-- Urteil und Dauer.
CREATE TABLE IF NOT EXISTS laeufe (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    aufgabe_id TEXT NOT NULL,
    ziel_id    TEXT,
    schritt    INTEGER DEFAULT 0,
    art        TEXT NOT NULL,       -- plan|werkzeug|modell|verifikation|…
    name       TEXT DEFAULT '',
    eingabe    TEXT DEFAULT '',
    ausgabe    TEXT DEFAULT '',
    ok         INTEGER DEFAULT 1,
    urteil     TEXT DEFAULT '',
    dauer      REAL DEFAULT 0,
    fehler     TEXT DEFAULT '',
    zeit       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lauf ON laeufe(aufgabe_id, id);

-- WIRKUNGEN: die Idempotenzsperre (Master-Prompt §33). Eine Nebenwirkung mit
-- gleichem Schluessel wird NICHT zweimal ausgeloest - sie liefert beim
-- zweiten Mal das gemerkte Ergebnis. Ohne das wiederholt ein Wiederanlauf
-- nach einem Absturz jede bereits geschehene Aenderung.
CREATE TABLE IF NOT EXISTS wirkungen (
    schluessel TEXT PRIMARY KEY,
    aufgabe_id TEXT,
    werkzeug   TEXT,
    ergebnis   TEXT DEFAULT '{}',
    zeit       REAL NOT NULL
);

-- ZEITPLAENE, die einen NEUSTART ueberleben. Der vorhandene Auftragsplaner
-- haelt Termine im Arbeitsspeicher; ein Neustart vergisst sie. Was hier
-- steht, erzeugt beim Faelligwerden ein Ziel - nicht einen Funktionsaufruf.
CREATE TABLE IF NOT EXISTS zeitplaene (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    auftrag       TEXT NOT NULL,
    art           TEXT DEFAULT 'ziel',   -- ziel | ablauf
    ablauf_name   TEXT DEFAULT '',
    alle_sekunden REAL DEFAULT 0,
    faellig_ab    REAL NOT NULL,
    aktiv         INTEGER DEFAULT 1,
    zuletzt       REAL,
    laeufe        INTEGER DEFAULT 0,
    daten         TEXT DEFAULT '{}',
    erstellt      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zeitplan ON zeitplaene(aktiv, faellig_ab);

-- ABLAEUFE (Workflows) und ihre LAEUFE. Ein Ablauf ist eine gespeicherte
-- Kette aus Bedingung, Werkzeug, Warten, Freigabe, Pruefung und Verzweigung.
CREATE TABLE IF NOT EXISTS ablaeufe (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    beschreibung TEXT DEFAULT '',
    ausloeser    TEXT DEFAULT '{}',
    schritte     TEXT NOT NULL,
    aktiv        INTEGER DEFAULT 1,
    erstellt     REAL NOT NULL,
    geaendert    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ablauflaeufe (
    id         TEXT PRIMARY KEY,
    ablauf_id  TEXT NOT NULL,
    ablauf_name TEXT DEFAULT '',
    aufgabe_id TEXT,
    ziel_id    TEXT,
    zustand    TEXT NOT NULL,
    zeiger     INTEGER DEFAULT 0,
    umgebung   TEXT DEFAULT '{}',
    protokoll  TEXT DEFAULT '[]',
    fehler     TEXT DEFAULT '',
    begonnen   REAL NOT NULL,
    geaendert  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ablauflauf ON ablauflaeufe(zustand);

-- TOTE BRIEFE: was endgueltig nicht zugestellt werden konnte. Eine Aufgabe,
-- die auch nach allen Versuchen scheitert, verschwindet NICHT - sie landet
-- hier und bleibt sichtbar. Ohne diese Ablage ist "gescheitert" ein Zustand,
-- den niemand mehr ansieht.
CREATE TABLE IF NOT EXISTS tote_briefe (
    id         TEXT PRIMARY KEY,
    aufgabe_id TEXT NOT NULL,
    titel      TEXT DEFAULT '',
    grund      TEXT DEFAULT '',
    versuche   INTEGER DEFAULT 0,
    daten      TEXT DEFAULT '{}',
    gesichtet  INTEGER DEFAULT 0,
    zeit       REAL NOT NULL
);
"""

# Spalten, die zu einer bereits bestehenden Datenbank dazukommen. SQLite kann
# nur einzeln anfuegen, und ein zweiter Aufruf wirft - deshalb die Liste hier
# und das stille Ueberspringen unten. Ein Schema, das nur bei leerer Datenbank
# stimmt, ist kein Schema.
_NACHRUESTEN = [
    ("aufgaben", "art", "TEXT DEFAULT 'allgemein'"),
    ("aufgaben", "arbeiter", "TEXT DEFAULT ''"),
    ("aufgaben", "belegt_bis", "REAL DEFAULT 0"),
    ("aufgaben", "ablauf_lauf_id", "TEXT"),
    ("aufgaben", "faellig_ab", "REAL DEFAULT 0"),
    ("freigaben", "schluessel", "TEXT DEFAULT ''"),
]


class Aufgabenspeicher:
    """Persistente Aufgaben mit kontrolliertem Lebenszyklus.

    Jede Methode, die schreibt, ist in sich atomar. Wer mehrere Änderungen
    als Einheit braucht, nimmt `einheit()`.
    """

    def __init__(self, pfad: Path):
        self.pfad = Path(pfad)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False, weil der Zeitplaner mehrere Arbeiter fuehrt.
        # Die Sperre unten macht daraus trotzdem einen geordneten Zugriff.
        self._db = sqlite3.connect(str(self.pfad), check_same_thread=False,
                                   isolation_level=None, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._sperre = threading.RLock()
        with self._sperre:
            # WAL: Leser blockieren den Schreiber nicht. Bei mehreren
            # Arbeitern ist das der Unterschied zwischen fluessig und
            # "database is locked".
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(_SCHEMA)
            self._nachruesten()

    def _nachruesten(self) -> None:
        """Faengt eine Datenbank ein, die vor einer Schemaerweiterung entstand.

        Ohne das laeuft das Programm mit einer Datei weiter, in der eine
        Spalte fehlt, und die erste Abfrage darauf wirft mitten im Betrieb -
        an einer Stelle, die mit der Ursache nichts zu tun hat.
        """
        for tabelle, spalte, typ in _NACHRUESTEN:
            try:
                vorhanden = {z["name"] for z in
                             self._db.execute(f"PRAGMA table_info({tabelle})")}
            except sqlite3.Error:
                continue
            if spalte in vorhanden:
                continue
            try:
                self._db.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
            except sqlite3.Error:
                pass

    def schliessen(self) -> None:
        with self._sperre:
            try:
                self._db.close()
            except Exception:
                pass

    @contextmanager
    def einheit(self):
        """Mehrere Änderungen als eine Transaktion."""
        with self._sperre:
            self._db.execute("BEGIN")
            try:
                yield self._db
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    # ------------------------------------------------------------- Anlegen
    def anlegen(self, titel: str, beschreibung: str = "", *,
                eltern_id: Optional[str] = None, ziel_id: Optional[str] = None,
                prioritaet: int = 5, auftrag: str = "",
                risiko: str = RISIKO_GERING,
                art: str = ART_ALLGEMEIN,
                ablauf_lauf_id: Optional[str] = None,
                faellig_ab: float = 0.0,
                max_versuche: Optional[int] = None,
                benoetigt: Optional[List[str]] = None,
                daten: Optional[dict] = None) -> Aufgabe:
        """Legt eine Aufgabe an. Mit Abhängigkeiten startet sie blockiert."""
        titel = str(titel or "").strip()
        if not titel:
            raise ValueError("Eine Aufgabe ohne Titel ist keine Aufgabe.")

        aufgabe = Aufgabe(
            id=uuid.uuid4().hex[:16], titel=titel[:300],
            beschreibung=str(beschreibung or ""), eltern_id=eltern_id,
            ziel_id=ziel_id, prioritaet=max(1, min(int(prioritaet), 9)),
            auftrag=str(auftrag or ""), risiko=risiko, art=str(art),
            ablauf_lauf_id=ablauf_lauf_id, faellig_ab=float(faellig_ab or 0.0),
            daten=dict(daten or {}),
        )
        if max_versuche is not None:
            aufgabe.max_versuche = max(1, int(max_versuche))
        # Eine Aufgabe, die auf etwas wartet, darf nicht als bereit gelten -
        # sonst zieht der Zeitplaner sie und sie scheitert sofort.
        benoetigt = [b for b in (benoetigt or []) if b]
        if benoetigt:
            aufgabe.zustand = WARTET_AUF_ABHAENGIGKEIT

        with self.einheit() as db:
            db.execute(
                "INSERT INTO aufgaben (id,titel,beschreibung,eltern_id,ziel_id,"
                "zustand,prioritaet,versuche,max_versuche,neuplanungen,"
                "max_neuplanungen,auftrag,ergebnis,fehler,risiko,art,arbeiter,"
                "belegt_bis,ablauf_lauf_id,faellig_ab,daten,erstellt,geaendert) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aufgabe.id, aufgabe.titel, aufgabe.beschreibung, aufgabe.eltern_id,
                 aufgabe.ziel_id, aufgabe.zustand, aufgabe.prioritaet, 0,
                 aufgabe.max_versuche, 0, aufgabe.max_neuplanungen, aufgabe.auftrag,
                 "", "", aufgabe.risiko, aufgabe.art, "", 0.0,
                 aufgabe.ablauf_lauf_id, aufgabe.faellig_ab,
                 json.dumps(aufgabe.daten, ensure_ascii=False),
                 aufgabe.erstellt, aufgabe.geaendert))
            for b in benoetigt:
                db.execute("INSERT OR IGNORE INTO abhaengigkeiten VALUES (?,?)",
                           (aufgabe.id, b))
            db.execute("INSERT INTO verlauf (aufgabe_id,von,nach,grund,akteur,zeit) "
                       "VALUES (?,?,?,?,?,?)",
                       (aufgabe.id, None, aufgabe.zustand, "angelegt", "zeus",
                        time.time()))
        return aufgabe

    # ---------------------------------------------------------------- Lesen
    def holen(self, aufgabe_id: str) -> Optional[Aufgabe]:
        with self._sperre:
            zeile = self._db.execute("SELECT * FROM aufgaben WHERE id=?",
                                     (aufgabe_id,)).fetchone()
        return self._zu_aufgabe(zeile) if zeile else None

    def alle(self, zustand: Optional[str] = None,
             nur_offene: bool = False, grenze: int = 200) -> List[Aufgabe]:
        frage = "SELECT * FROM aufgaben"
        werte: list = []
        if zustand:
            frage += " WHERE zustand=?"
            werte.append(zustand)
        elif nur_offene:
            platz = ",".join("?" * len(ENDZUSTAENDE))
            frage += f" WHERE zustand NOT IN ({platz})"
            werte.extend(sorted(ENDZUSTAENDE))
        frage += " ORDER BY prioritaet ASC, erstellt ASC LIMIT ?"
        werte.append(int(grenze))
        with self._sperre:
            zeilen = self._db.execute(frage, werte).fetchall()
        return [self._zu_aufgabe(z) for z in zeilen]

    def kinder(self, eltern_id: str) -> List[Aufgabe]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM aufgaben WHERE eltern_id=? ORDER BY erstellt",
                (eltern_id,)).fetchall()
        return [self._zu_aufgabe(z) for z in zeilen]

    def blockiert_von(self, aufgabe_id: str) -> List[str]:
        """Aufgaben, auf die diese hier noch wartet - nur die unfertigen."""
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT a.id FROM abhaengigkeiten d JOIN aufgaben a "
                "ON a.id = d.benoetigt_id "
                "WHERE d.aufgabe_id=? AND a.zustand != ?",
                (aufgabe_id, FERTIG)).fetchall()
        return [z["id"] for z in zeilen]

    def verlauf(self, aufgabe_id: str) -> List[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT von,nach,grund,akteur,zeit FROM verlauf "
                "WHERE aufgabe_id=? ORDER BY zeit, id", (aufgabe_id,)).fetchall()
        return [dict(z) for z in zeilen]

    # -------------------------------------------------------- Zustandswechsel
    def wechseln(self, aufgabe_id: str, nach: str, grund: str = "",
                 akteur: str = "zeus") -> Aufgabe:
        """Ändert den Zustand - oder wirft, wenn der Wechsel verboten ist.

        Das Werfen ist Absicht. Ein stillschweigend abgelehnter Wechsel
        hinterlaesst eine Aufgabe, die irgendwo haengt, ohne dass jemand
        erfaehrt warum - genau die Art Fehler, die dieses Projekt schon
        dreimal gekostet hat (ENTWICKLUNG.md, Muster 2.1).
        """
        with self.einheit() as db:
            zeile = db.execute("SELECT * FROM aufgaben WHERE id=?",
                               (aufgabe_id,)).fetchone()
            if not zeile:
                raise Zustandsfehler(f"Keine Aufgabe mit der Kennung '{aufgabe_id}'.")
            von = zeile["zustand"]
            if nach == von:
                return self._zu_aufgabe(zeile)
            erlaubt = UEBERGAENGE.get(von, set())
            if nach not in erlaubt:
                raise Zustandsfehler(
                    f"Wechsel '{von}' → '{nach}' ist nicht vorgesehen. "
                    f"Erlaubt waere: {', '.join(sorted(erlaubt)) or '(nichts, Endzustand)'}.")

            jetzt = time.time()
            db.execute("UPDATE aufgaben SET zustand=?, geaendert=? WHERE id=?",
                       (nach, jetzt, aufgabe_id))
            db.execute("INSERT INTO verlauf (aufgabe_id,von,nach,grund,akteur,zeit) "
                       "VALUES (?,?,?,?,?,?)",
                       (aufgabe_id, von, nach, str(grund or "")[:400], akteur, jetzt))
            zeile = db.execute("SELECT * FROM aufgaben WHERE id=?",
                               (aufgabe_id,)).fetchone()
        return self._zu_aufgabe(zeile)

    def darf_wechseln(self, von: str, nach: str) -> bool:
        return nach in UEBERGAENGE.get(von, set())

    def aktualisieren(self, aufgabe_id: str, **felder) -> Optional[Aufgabe]:
        """Setzt einzelne Felder. Der Zustand geht NUR über wechseln()."""
        erlaubt = {"titel", "beschreibung", "prioritaet", "versuche",
                   "neuplanungen", "ergebnis", "fehler", "risiko", "daten",
                   "max_versuche", "max_neuplanungen", "art", "faellig_ab",
                   "arbeiter", "belegt_bis", "ablauf_lauf_id", "ziel_id"}
        setzen = {k: v for k, v in felder.items() if k in erlaubt}
        if not setzen:
            return self.holen(aufgabe_id)
        if "daten" in setzen:
            setzen["daten"] = json.dumps(setzen["daten"], ensure_ascii=False)
        teile = ", ".join(f"{k}=?" for k in setzen)
        with self.einheit() as db:
            db.execute(f"UPDATE aufgaben SET {teile}, geaendert=? WHERE id=?",
                       list(setzen.values()) + [time.time(), aufgabe_id])
        return self.holen(aufgabe_id)

    # ----------------------------------------------------------- Abarbeiten
    def naechste(self) -> Optional[Aufgabe]:
        """Die dringlichste Aufgabe, die JETZT laufen könnte.

        Blockierte werden dabei stillschweigend freigegeben, sobald ihre
        Vorbedingungen fertig sind - sonst muesste jemand anders daran denken,
        und "jemand anders" ist in einem Jahr niemand.
        """
        for aufgabe in self.alle(zustand=WARTET_AUF_ABHAENGIGKEIT):
            if not self.blockiert_von(aufgabe.id):
                self.wechseln(aufgabe.id, BEREIT, "Vorbedingungen erfüllt", "system")
        jetzt = time.time()
        for aufgabe in self.alle(zustand=BEREIT, grenze=50):
            if aufgabe.faellig_ab and aufgabe.faellig_ab > jetzt:
                continue
            return aufgabe
        return None

    def blockaden_loesen(self) -> Dict[str, List[str]]:
        """Raeumt die Warteschlange auf - einmal je Runde, an einer Stelle.

        Zwei Faelle, die sich sonst niemand ansieht:

        * Die Vorbedingung ist FERTIG -> die Aufgabe darf laufen.
        * Die Vorbedingung ist endgueltig GESCHEITERT oder ABGEBROCHEN ->
          die Aufgabe wartet auf etwas, das nie kommt. Sie geht in die
          Neuplanung, statt bis zum Sankt-Nimmerleins-Tag zu warten.

        Der zweite Fall ist der Grund fuer diese Methode. `naechste()` loest
        nur den ersten - und ein Aufgabengraph, in dem ein Zweig gescheitert
        ist, bleibt damit fuer immer halb offen.
        """
        freigegeben: List[str] = []
        umgeleitet: List[str] = []
        for aufgabe in self.alle(zustand=WARTET_AUF_ABHAENGIGKEIT, grenze=500):
            offen = self.blockiert_von(aufgabe.id)
            if not offen:
                try:
                    self.wechseln(aufgabe.id, BEREIT, "Vorbedingungen erfuellt",
                                  "system")
                    freigegeben.append(aufgabe.id)
                except Zustandsfehler:
                    pass
                continue
            tot = [k for k in offen
                   if (self.holen(k) or Aufgabe(id="", titel="")).zustand
                   in (GESCHEITERT, ABGEBROCHEN)]
            if tot:
                try:
                    self.wechseln(aufgabe.id, NEUPLANUNG,
                                  f"Vorbedingung endgueltig gescheitert: "
                                  f"{', '.join(tot[:3])}", "system")
                    umgeleitet.append(aufgabe.id)
                except Zustandsfehler:
                    pass
        return {"freigegeben": freigegeben, "umgeleitet": umgeleitet}

    def fehlschlag(self, aufgabe_id: str, fehler: str) -> Aufgabe:
        """Bucht einen Fehlversuch und entscheidet über Wiederholung.

        Die Entscheidung gehoert hierher und nicht an die Aufrufstelle: Sonst
        zaehlt jede Stelle anders, und irgendwo zaehlt eine gar nicht - der
        Weg zur Endlosschleife (Master-Prompt §36).
        """
        aufgabe = self.holen(aufgabe_id)
        if not aufgabe:
            raise Zustandsfehler(f"Keine Aufgabe '{aufgabe_id}'.")
        versuche = aufgabe.versuche + 1
        self.aktualisieren(aufgabe_id, versuche=versuche, fehler=str(fehler)[:600])
        if versuche < aufgabe.max_versuche and aufgabe.neuplanungen < aufgabe.max_neuplanungen:
            return self.wechseln(aufgabe_id, NEUPLANUNG,
                                 f"Versuch {versuche} gescheitert: {str(fehler)[:120]}")
        return self.wechseln(aufgabe_id, GESCHEITERT,
                             f"nach {versuche} Versuchen aufgegeben: {str(fehler)[:120]}")

    # ------------------------------------------------------------- Freigaben
    def freigabe_erbitten(self, aufgabe_id: str, frage: str,
                          risiko: str = RISIKO_MITTEL,
                          moeglichkeiten: Optional[List[str]] = None,
                          schluessel: str = "") -> str:
        """Stellt eine Frage an den Menschen und legt die Aufgabe schlafen.

        `schluessel` ist das Wiedererkennungsmerkmal der Handlung, um die es
        geht. Ohne ihn wuerde nach dem Fortsetzen erneut gefragt: Die Aufgabe
        laeuft wieder an, stoesst auf denselben Aufruf und weiss nicht, dass
        er bereits genehmigt ist. Zweimal dieselbe Frage ist der schnellste
        Weg, dass ein Mensch aufhoert, sie zu lesen.
        """
        kennung = uuid.uuid4().hex[:16]
        with self.einheit() as db:
            db.execute(
                "INSERT INTO freigaben (id,aufgabe_id,frage,risiko,moeglichkeiten,"
                "schluessel,gefragt) VALUES (?,?,?,?,?,?,?)",
                (kennung, aufgabe_id, str(frage)[:600], risiko,
                 json.dumps(list(moeglichkeiten or []), ensure_ascii=False),
                 str(schluessel or ""), time.time()))
        self.wechseln(aufgabe_id, WARTET_AUF_FREIGABE, str(frage)[:200])
        return kennung

    def freigabe_stand(self, aufgabe_id: str, schluessel: str) -> Optional[dict]:
        """Wurde genau DIESE Handlung fuer diese Aufgabe schon entschieden?

        Gibt den juengsten Eintrag zurueck - auch eine Ablehnung. Der
        Unterschied zwischen "nie gefragt" (None), "erlaubt" und "abgelehnt"
        ist genau der, an dem sich entscheidet, ob gefragt, gehandelt oder
        umgeplant wird.
        """
        if not schluessel:
            return None
        with self._sperre:
            zeile = self._db.execute(
                "SELECT * FROM freigaben WHERE aufgabe_id=? AND schluessel=? "
                "AND beantwortet IS NOT NULL ORDER BY beantwortet DESC LIMIT 1",
                (aufgabe_id, str(schluessel))).fetchone()
        if not zeile:
            return None
        eintrag = dict(zeile)
        eintrag["erteilt"] = bool(eintrag.get("erteilt"))
        return eintrag

    def offene_freigaben(self) -> List[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT f.*, a.titel FROM freigaben f JOIN aufgaben a "
                "ON a.id=f.aufgabe_id WHERE f.erteilt=0 AND f.beantwortet IS NULL "
                "ORDER BY f.gefragt").fetchall()
        raus = []
        for z in zeilen:
            eintrag = dict(z)
            eintrag["moeglichkeiten"] = json.loads(eintrag.get("moeglichkeiten") or "[]")
            raus.append(eintrag)
        return raus

    def freigabe_beantworten(self, freigabe_id: str, erteilt: bool,
                             antwort: str = "") -> Optional[Aufgabe]:
        """Josh antwortet. Danach läuft die Aufgabe weiter oder ist beendet."""
        with self.einheit() as db:
            zeile = db.execute("SELECT * FROM freigaben WHERE id=?",
                               (freigabe_id,)).fetchone()
            if not zeile:
                return None
            db.execute("UPDATE freigaben SET erteilt=?, antwort=?, beantwortet=? "
                       "WHERE id=?",
                       (1 if erteilt else 0, str(antwort)[:600], time.time(),
                        freigabe_id))
            aufgabe_id = zeile["aufgabe_id"]
        if erteilt:
            return self.wechseln(aufgabe_id, BEREIT,
                                 f"freigegeben: {antwort[:120]}", "nutzer")
        return self.wechseln(aufgabe_id, ABGEBROCHEN,
                             f"abgelehnt: {antwort[:120]}", "nutzer")

    # ------------------------------------------------------------ Artefakte
    def artefakt_ablegen(self, aufgabe_id: str, art: str, ort: str,
                         beschreibung: str = "") -> str:
        kennung = uuid.uuid4().hex[:16]
        with self.einheit() as db:
            db.execute("INSERT INTO artefakte (id,aufgabe_id,art,ort,beschreibung,"
                       "erstellt) VALUES (?,?,?,?,?,?)",
                       (kennung, aufgabe_id, str(art), str(ort),
                        str(beschreibung)[:400], time.time()))
        return kennung

    def artefakte(self, aufgabe_id: str) -> List[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM artefakte WHERE aufgabe_id=? ORDER BY erstellt",
                (aufgabe_id,)).fetchall()
        return [dict(z) for z in zeilen]

    # --------------------------------------------------------- Nach Neustart
    def wiederaufnehmen(self) -> List[Aufgabe]:
        """Räumt nach einem Absturz auf und sagt, was offen ist.

        Eine Aufgabe, die beim Absturz auf LAEUFT stand, wird NICHT blind
        weitergeführt - niemand weiss, wie weit sie kam. Sie geht auf
        NEUPLANUNG: Der Planer sieht den Verlauf und entscheidet neu. Das ist
        Master-Prompt §39, und der Grund dafür steht dort auch: Ein blind
        wiederholter Schritt kann eine Wirkung doppelt auslösen.
        """
        wieder: List[Aufgabe] = []
        for aufgabe in self.alle(zustand=LAEUFT):
            wieder.append(self.wechseln(
                aufgabe.id, NEUPLANUNG,
                "beim Neustart als laufend vorgefunden - Stand unbekannt",
                "system"))
        for aufgabe in self.alle(zustand=PRUEFUNG):
            wieder.append(self.wechseln(
                aufgabe.id, NEUPLANUNG,
                "Prüfung beim Neustart unterbrochen", "system"))
        # Die Belegung MUSS mit weg. Nach einem Neustart gibt es den Arbeiter
        # nicht mehr, der sie gehalten hat - seine Frist laeuft aber noch.
        # Ohne dieses Freigeben findet der neue Arbeiter die Aufgabe zwar im
        # richtigen Zustand vor, darf sie aber nicht anfassen, und die
        # Warteschlange meldet "nichts zu tun", obwohl Arbeit dasteht. Genau
        # so aufgetreten: Nach dem Absturztest lief gar nichts weiter.
        for aufgabe in wieder:
            self.freigeben(aufgabe.id)
        return wieder

    def kennzahlen(self) -> dict:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT zustand, COUNT(*) n FROM aufgaben GROUP BY zustand").fetchall()
        nach_zustand = {z["zustand"]: z["n"] for z in zeilen}
        return {
            "gesamt": sum(nach_zustand.values()),
            "offen": sum(n for z, n in nach_zustand.items() if z not in ENDZUSTAENDE),
            "wartet_auf_mensch": sum(n for z, n in nach_zustand.items()
                                     if z in WARTET_AUF_MENSCH),
            "nach_zustand": nach_zustand,
            "offene_freigaben": len(self.offene_freigaben()),
        }

    # ------------------------------------------------------------------ Ziele
    def ziel_anlegen(self, beschreibung: str, *, ergebnis: str = "",
                     bedingungen: Optional[List[str]] = None,
                     prioritaet: int = 5, frist: Optional[float] = None,
                     quelle: str = "nutzer", auftrag: str = "",
                     daten: Optional[dict] = None) -> "Ziel":
        """Legt ein Ziel an. Aufgaben entstehen daraus, nicht umgekehrt."""
        beschreibung = str(beschreibung or "").strip()
        if not beschreibung:
            raise ValueError("Ein Ziel ohne Beschreibung ist kein Ziel.")
        ziel = Ziel(id=uuid.uuid4().hex[:16], beschreibung=beschreibung[:600],
                    ergebnis=str(ergebnis or ""),
                    bedingungen=list(bedingungen or []),
                    prioritaet=max(1, min(int(prioritaet), 9)),
                    frist=frist, quelle=str(quelle or "nutzer"),
                    auftrag=str(auftrag or beschreibung),
                    daten=dict(daten or {}))
        with self.einheit() as db:
            db.execute(
                "INSERT INTO ziele (id,beschreibung,ergebnis,bedingungen,prioritaet,"
                "frist,zustand,quelle,auftrag,befund,daten,erstellt,geaendert) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ziel.id, ziel.beschreibung, ziel.ergebnis,
                 json.dumps(ziel.bedingungen, ensure_ascii=False), ziel.prioritaet,
                 ziel.frist, ziel.zustand, ziel.quelle, ziel.auftrag, "",
                 json.dumps(ziel.daten, ensure_ascii=False),
                 ziel.erstellt, ziel.geaendert))
        return ziel

    def ziel(self, ziel_id: str) -> Optional["Ziel"]:
        with self._sperre:
            zeile = self._db.execute("SELECT * FROM ziele WHERE id=?",
                                     (ziel_id,)).fetchone()
        return self._zu_ziel(zeile) if zeile else None

    def ziele(self, zustand: Optional[str] = None, nur_offene: bool = False,
              grenze: int = 100) -> List["Ziel"]:
        frage, werte = "SELECT * FROM ziele", []
        if zustand:
            frage += " WHERE zustand=?"
            werte.append(zustand)
        elif nur_offene:
            platz = ",".join("?" * len(ZIEL_ENDZUSTAENDE))
            frage += f" WHERE zustand NOT IN ({platz})"
            werte.extend(sorted(ZIEL_ENDZUSTAENDE))
        frage += " ORDER BY prioritaet ASC, erstellt ASC LIMIT ?"
        werte.append(int(grenze))
        with self._sperre:
            zeilen = self._db.execute(frage, werte).fetchall()
        return [self._zu_ziel(z) for z in zeilen]

    def ziel_setzen(self, ziel_id: str, zustand: str,
                    befund: str = "") -> Optional["Ziel"]:
        erlaubt = {ZIEL_AKTIV, ZIEL_PAUSIERT} | ZIEL_ENDZUSTAENDE
        if zustand not in erlaubt:
            raise ValueError(f"'{zustand}' ist kein Zielzustand.")
        with self.einheit() as db:
            db.execute("UPDATE ziele SET zustand=?, befund=?, geaendert=? WHERE id=?",
                       (zustand, str(befund)[:600], time.time(), ziel_id))
        return self.ziel(ziel_id)

    def aufgaben_zum_ziel(self, ziel_id: str) -> List[Aufgabe]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM aufgaben WHERE ziel_id=? ORDER BY erstellt",
                (ziel_id,)).fetchall()
        return [self._zu_aufgabe(z) for z in zeilen]

    # ------------------------------------------------------- Arbeiter/Belegung
    # Zustaende, aus denen ein Arbeiter Arbeit zieht. BEREIT ist der
    # Normalfall; NEUPLANUNG gehoert dazu, weil eine Aufgabe, die einen
    # anderen Weg braucht, sonst niemanden faende, der ihn sucht - sie laege
    # fuer immer im Zustand "muss neu geplant werden", und niemand plant.
    HOLBAR = (BEREIT, NEUPLANUNG)

    def beanspruchen(self, arbeiter: str, dauer: float = 300.0,
                     zustaende: Optional[tuple] = None) -> Optional[Aufgabe]:
        """Holt sich EINE Aufgabe - atomar, damit kein Arbeiter doppelt zieht.

        Der springende Punkt ist das `WHERE zustand=?` im UPDATE: Zwei
        Arbeiter, die dieselbe Zeile anfassen wollen, koennen nicht beide
        erfolgreich sein, weil der zweite eine Zeile vorfindet, die nicht
        mehr in dem Zustand ist, den er erwartet hat. Erst SELECT und dann
        UPDATE haette genau dieses Loch.

        BEREIT wird dabei zu LAEUFT. NEUPLANUNG bleibt, was es ist: Der
        Laeufer erkennt daran, dass er einen Weg suchen soll statt zu
        handeln, und ein Wechsel nach LAEUFT waere hier ohnehin nicht
        vorgesehen. Belegt wird sie trotzdem - sonst zoegen zwei Arbeiter
        gleichzeitig dieselbe Neuplanung.
        """
        jetzt = time.time()
        holbar = tuple(zustaende or self.HOLBAR)
        platz = ",".join("?" * len(holbar))
        with self.einheit() as db:
            zeile = db.execute(
                f"SELECT * FROM aufgaben WHERE zustand IN ({platz}) "
                f"AND (faellig_ab IS NULL OR faellig_ab<=?) "
                f"AND (arbeiter IS NULL OR arbeiter='' OR belegt_bis<=?) "
                f"ORDER BY prioritaet ASC, erstellt ASC LIMIT 1",
                (*holbar, jetzt, jetzt)).fetchone()
            if not zeile:
                return None
            von = zeile["zustand"]
            nach = LAEUFT if von == BEREIT else von
            geaendert = db.execute(
                "UPDATE aufgaben SET zustand=?, arbeiter=?, belegt_bis=?, geaendert=? "
                "WHERE id=? AND zustand=? AND (arbeiter IS NULL OR arbeiter='' "
                "OR belegt_bis<=?)",
                (nach, str(arbeiter), jetzt + max(1.0, float(dauer)), jetzt,
                 zeile["id"], von, jetzt))
            if geaendert.rowcount != 1:
                return None          # ein anderer Arbeiter war schneller
            if nach != von:
                db.execute("INSERT INTO verlauf (aufgabe_id,von,nach,grund,akteur,zeit) "
                           "VALUES (?,?,?,?,?,?)",
                           (zeile["id"], von, nach, f"uebernommen von {arbeiter}",
                            str(arbeiter), jetzt))
            zeile = db.execute("SELECT * FROM aufgaben WHERE id=?",
                               (zeile["id"],)).fetchone()
        return self._zu_aufgabe(zeile)

    def belegung_verlaengern(self, aufgabe_id: str, dauer: float = 300.0) -> None:
        """Ein Arbeiter, der noch lebt, sagt das. Sonst holt ihn die Aufraeumung."""
        with self.einheit() as db:
            db.execute("UPDATE aufgaben SET belegt_bis=? WHERE id=?",
                       (time.time() + max(1.0, float(dauer)), aufgabe_id))

    def freigeben(self, aufgabe_id: str) -> None:
        with self.einheit() as db:
            db.execute("UPDATE aufgaben SET arbeiter='', belegt_bis=0 WHERE id=?",
                       (aufgabe_id,))

    def verwaiste_einsammeln(self) -> List[Aufgabe]:
        """Aufgaben, deren Arbeiter nicht mehr antwortet.

        Ein abgestuerzter Arbeiter hinterlaesst eine Aufgabe auf LAEUFT, die
        nie wieder angefasst wird. Nach Ablauf der Belegung gilt sie als
        verwaist und geht in die Neuplanung - NICHT blind zurueck auf bereit,
        weil niemand weiss, wie weit sie kam (dieselbe Lehre wie in
        `wiederaufnehmen`).
        """
        jetzt = time.time()
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT id FROM aufgaben WHERE zustand=? AND belegt_bis>0 "
                "AND belegt_bis<?", (LAEUFT, jetzt)).fetchall()
        raus = []
        for z in zeilen:
            try:
                raus.append(self.wechseln(z["id"], NEUPLANUNG,
                                          "Arbeiter meldet sich nicht mehr", "system"))
                self.freigeben(z["id"])
            except Zustandsfehler:
                continue
        # Belegungen, die in einem anderen Zustand haengen geblieben sind
        # (etwa eine Neuplanung, deren Arbeiter starb), werden nur gelockert -
        # ihr Zustand stimmt ja noch, es fehlt nur jemand, der sie anfasst.
        with self.einheit() as db:
            gelockert = db.execute(
                "UPDATE aufgaben SET arbeiter='', belegt_bis=0 "
                "WHERE belegt_bis>0 AND belegt_bis<? AND zustand!=?",
                (jetzt, LAEUFT)).rowcount
        # Die Zahl gehoert nach oben: Die Arbeiterstube fasst nach einem
        # leeren Zug genau dann noch einmal nach, wenn das Aufraeumen etwas
        # bewegt hat. Ein stillschweigend gelockerter Datensatz waere
        # Bewegung, von der niemand erfaehrt - und die Schlange meldete
        # "nichts zu tun", obwohl gerade etwas frei geworden ist.
        self._zuletzt_gelockert = int(gelockert or 0)
        return raus

    def gelockert(self) -> int:
        """Wie viele Belegungen der letzte Aufraeumlauf geloest hat."""
        return int(getattr(self, "_zuletzt_gelockert", 0))

    # ------------------------------------------------------------- Telemetrie
    def lauf_vermerken(self, aufgabe_id: str, art: str, *, name: str = "",
                       eingabe: str = "", ausgabe: str = "", ok: bool = True,
                       urteil: str = "", dauer: float = 0.0, fehler: str = "",
                       schritt: int = 0, ziel_id: Optional[str] = None) -> int:
        """Eine Zeile Telemetrie. Keine Ausnahme nach oben - Messen darf nie
        das Gemessene kaputtmachen."""
        try:
            with self.einheit() as db:
                zeiger = db.execute(
                    "INSERT INTO laeufe (aufgabe_id,ziel_id,schritt,art,name,eingabe,"
                    "ausgabe,ok,urteil,dauer,fehler,zeit) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aufgabe_id, ziel_id, int(schritt), str(art), str(name)[:120],
                     str(eingabe)[:2000], str(ausgabe)[:2000], 1 if ok else 0,
                     str(urteil)[:40], float(dauer), str(fehler)[:600], time.time()))
                return int(zeiger.lastrowid or 0)
        except Exception:
            return 0

    def laeufe(self, aufgabe_id: str, grenze: int = 200) -> List[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM laeufe WHERE aufgabe_id=? ORDER BY id LIMIT ?",
                (aufgabe_id, int(grenze))).fetchall()
        return [dict(z) for z in zeilen]

    # -------------------------------------------------------------- Idempotenz
    def wirkung_gemerkt(self, schluessel: str) -> Optional[dict]:
        with self._sperre:
            zeile = self._db.execute("SELECT * FROM wirkungen WHERE schluessel=?",
                                     (schluessel,)).fetchone()
        if not zeile:
            return None
        eintrag = dict(zeile)
        try:
            eintrag["ergebnis"] = json.loads(eintrag.get("ergebnis") or "{}")
        except ValueError:
            eintrag["ergebnis"] = {}
        return eintrag

    def wirkung_merken(self, schluessel: str, werkzeug: str, ergebnis: dict,
                       aufgabe_id: Optional[str] = None) -> None:
        with self.einheit() as db:
            db.execute("INSERT OR REPLACE INTO wirkungen "
                       "(schluessel,aufgabe_id,werkzeug,ergebnis,zeit) "
                       "VALUES (?,?,?,?,?)",
                       (schluessel, aufgabe_id, str(werkzeug),
                        json.dumps(ergebnis, ensure_ascii=False, default=str),
                        time.time()))

    # -------------------------------------------------------------- Zeitplaene
    def zeitplan_anlegen(self, name: str, auftrag: str, *, alle_sekunden: float = 0,
                         faellig_ab: Optional[float] = None, art: str = "ziel",
                         ablauf_name: str = "",
                         daten: Optional[dict] = None) -> dict:
        kennung = uuid.uuid4().hex[:16]
        faellig = float(faellig_ab if faellig_ab is not None
                        else time.time() + max(0.0, float(alle_sekunden)))
        with self.einheit() as db:
            db.execute("INSERT INTO zeitplaene (id,name,auftrag,art,ablauf_name,"
                       "alle_sekunden,faellig_ab,aktiv,zuletzt,laeufe,daten,erstellt) "
                       "VALUES (?,?,?,?,?,?,?,1,NULL,0,?,?)",
                       (kennung, str(name)[:120], str(auftrag)[:1000], str(art),
                        str(ablauf_name or ""), float(alle_sekunden), faellig,
                        json.dumps(dict(daten or {}), ensure_ascii=False),
                        time.time()))
        return self.zeitplan(kennung) or {}

    def zeitplan(self, kennung: str) -> Optional[dict]:
        with self._sperre:
            zeile = self._db.execute("SELECT * FROM zeitplaene WHERE id=?",
                                     (kennung,)).fetchone()
        return self._zu_zeitplan(zeile) if zeile else None

    def zeitplaene(self, nur_aktive: bool = True) -> List[dict]:
        frage = "SELECT * FROM zeitplaene"
        if nur_aktive:
            frage += " WHERE aktiv=1"
        frage += " ORDER BY faellig_ab"
        with self._sperre:
            zeilen = self._db.execute(frage).fetchall()
        return [self._zu_zeitplan(z) for z in zeilen]

    def faellige_zeitplaene(self, jetzt: Optional[float] = None) -> List[dict]:
        jetzt = time.time() if jetzt is None else float(jetzt)
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT * FROM zeitplaene WHERE aktiv=1 AND faellig_ab<=? "
                "ORDER BY faellig_ab", (jetzt,)).fetchall()
        return [self._zu_zeitplan(z) for z in zeilen]

    def zeitplan_quittieren(self, kennung: str,
                            jetzt: Optional[float] = None) -> None:
        """Nach dem Ausloesen: naechsten Termin setzen oder stilllegen.

        Ein einmaliger Termin wird INAKTIV, nicht geloescht - sonst laesst
        sich hinterher nicht mehr nachsehen, ob er gelaufen ist.
        """
        jetzt = time.time() if jetzt is None else float(jetzt)
        eintrag = self.zeitplan(kennung)
        if not eintrag:
            return
        takt = float(eintrag.get("alle_sekunden") or 0)
        with self.einheit() as db:
            if takt > 0:
                db.execute("UPDATE zeitplaene SET faellig_ab=?, zuletzt=?, "
                           "laeufe=laeufe+1 WHERE id=?",
                           (jetzt + takt, jetzt, kennung))
            else:
                db.execute("UPDATE zeitplaene SET aktiv=0, zuletzt=?, laeufe=laeufe+1 "
                           "WHERE id=?", (jetzt, kennung))

    def zeitplan_abschalten(self, kennung: str) -> None:
        with self.einheit() as db:
            db.execute("UPDATE zeitplaene SET aktiv=0 WHERE id=?", (kennung,))

    # --------------------------------------------------------------- Ablaeufe
    def ablauf_sichern(self, name: str, schritte: list, *, beschreibung: str = "",
                       ausloeser: Optional[dict] = None, aktiv: bool = True) -> dict:
        name = str(name or "").strip()
        if not name:
            raise ValueError("Ein Ablauf ohne Namen laesst sich nicht aufrufen.")
        if not isinstance(schritte, list) or not schritte:
            raise ValueError("Ein Ablauf ohne Schritte tut nichts.")
        jetzt = time.time()
        vorhanden = self.ablauf(name)
        kennung = vorhanden["id"] if vorhanden else uuid.uuid4().hex[:16]
        with self.einheit() as db:
            if vorhanden:
                db.execute("UPDATE ablaeufe SET beschreibung=?, ausloeser=?, "
                           "schritte=?, aktiv=?, geaendert=? WHERE id=?",
                           (str(beschreibung),
                            json.dumps(dict(ausloeser or {}), ensure_ascii=False),
                            json.dumps(schritte, ensure_ascii=False),
                            1 if aktiv else 0, jetzt, kennung))
            else:
                db.execute("INSERT INTO ablaeufe (id,name,beschreibung,ausloeser,"
                           "schritte,aktiv,erstellt,geaendert) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           (kennung, name, str(beschreibung),
                            json.dumps(dict(ausloeser or {}), ensure_ascii=False),
                            json.dumps(schritte, ensure_ascii=False),
                            1 if aktiv else 0, jetzt, jetzt))
        return self.ablauf(name) or {}

    def ablauf(self, name_oder_id: str) -> Optional[dict]:
        with self._sperre:
            zeile = self._db.execute(
                "SELECT * FROM ablaeufe WHERE name=? OR id=?",
                (str(name_oder_id), str(name_oder_id))).fetchone()
        return self._zu_ablauf(zeile) if zeile else None

    def ablaeufe(self, nur_aktive: bool = False) -> List[dict]:
        frage = "SELECT * FROM ablaeufe"
        if nur_aktive:
            frage += " WHERE aktiv=1"
        frage += " ORDER BY name"
        with self._sperre:
            zeilen = self._db.execute(frage).fetchall()
        return [self._zu_ablauf(z) for z in zeilen]

    def ablauf_loeschen(self, name_oder_id: str) -> bool:
        eintrag = self.ablauf(name_oder_id)
        if not eintrag:
            return False
        with self.einheit() as db:
            db.execute("DELETE FROM ablaeufe WHERE id=?", (eintrag["id"],))
        return True

    def ablauflauf_anlegen(self, ablauf_id: str, ablauf_name: str = "", *,
                           umgebung: Optional[dict] = None,
                           aufgabe_id: Optional[str] = None,
                           ziel_id: Optional[str] = None) -> dict:
        kennung = uuid.uuid4().hex[:16]
        jetzt = time.time()
        with self.einheit() as db:
            db.execute("INSERT INTO ablauflaeufe (id,ablauf_id,ablauf_name,aufgabe_id,"
                       "ziel_id,zustand,zeiger,umgebung,protokoll,fehler,begonnen,"
                       "geaendert) VALUES (?,?,?,?,?,?,0,?,'[]','',?,?)",
                       (kennung, ablauf_id, str(ablauf_name), aufgabe_id, ziel_id,
                        LAEUFT, json.dumps(dict(umgebung or {}), ensure_ascii=False),
                        jetzt, jetzt))
        return self.ablauflauf(kennung) or {}

    def ablauflauf(self, kennung: str) -> Optional[dict]:
        with self._sperre:
            zeile = self._db.execute("SELECT * FROM ablauflaeufe WHERE id=?",
                                     (kennung,)).fetchone()
        return self._zu_ablauflauf(zeile) if zeile else None

    def ablauflaeufe(self, zustand: Optional[str] = None,
                     grenze: int = 50) -> List[dict]:
        frage, werte = "SELECT * FROM ablauflaeufe", []
        if zustand:
            frage += " WHERE zustand=?"
            werte.append(zustand)
        frage += " ORDER BY begonnen DESC LIMIT ?"
        werte.append(int(grenze))
        with self._sperre:
            zeilen = self._db.execute(frage, werte).fetchall()
        return [self._zu_ablauflauf(z) for z in zeilen]

    def ablauflauf_fortschreiben(self, kennung: str, *, zeiger: Optional[int] = None,
                                 zustand: Optional[str] = None,
                                 umgebung: Optional[dict] = None,
                                 protokoll: Optional[list] = None,
                                 fehler: Optional[str] = None,
                                 aufgabe_id: Optional[str] = None) -> Optional[dict]:
        setzen, werte = [], []
        if zeiger is not None:
            setzen.append("zeiger=?")
            werte.append(int(zeiger))
        if zustand is not None:
            setzen.append("zustand=?")
            werte.append(str(zustand))
        if umgebung is not None:
            setzen.append("umgebung=?")
            werte.append(json.dumps(umgebung, ensure_ascii=False, default=str))
        if protokoll is not None:
            setzen.append("protokoll=?")
            werte.append(json.dumps(protokoll[-200:], ensure_ascii=False, default=str))
        if fehler is not None:
            setzen.append("fehler=?")
            werte.append(str(fehler)[:600])
        if aufgabe_id is not None:
            setzen.append("aufgabe_id=?")
            werte.append(aufgabe_id)
        if not setzen:
            return self.ablauflauf(kennung)
        setzen.append("geaendert=?")
        werte.append(time.time())
        werte.append(kennung)
        with self.einheit() as db:
            db.execute(f"UPDATE ablauflaeufe SET {', '.join(setzen)} WHERE id=?",
                       werte)
        return self.ablauflauf(kennung)

    # ------------------------------------------------------------ Tote Briefe
    def toter_brief(self, aufgabe_id: str, grund: str,
                    daten: Optional[dict] = None) -> str:
        aufgabe = self.holen(aufgabe_id)
        kennung = uuid.uuid4().hex[:16]
        with self.einheit() as db:
            db.execute("INSERT INTO tote_briefe (id,aufgabe_id,titel,grund,versuche,"
                       "daten,gesichtet,zeit) VALUES (?,?,?,?,?,?,0,?)",
                       (kennung, aufgabe_id, (aufgabe.titel if aufgabe else "")[:300],
                        str(grund)[:600], aufgabe.versuche if aufgabe else 0,
                        json.dumps(dict(daten or {}), ensure_ascii=False, default=str),
                        time.time()))
        return kennung

    def tote_briefe(self, nur_ungesichtet: bool = False) -> List[dict]:
        frage = "SELECT * FROM tote_briefe"
        if nur_ungesichtet:
            frage += " WHERE gesichtet=0"
        frage += " ORDER BY zeit DESC LIMIT 100"
        with self._sperre:
            zeilen = self._db.execute(frage).fetchall()
        return [dict(z) for z in zeilen]

    def brief_sichten(self, kennung: str) -> None:
        with self.einheit() as db:
            db.execute("UPDATE tote_briefe SET gesichtet=1 WHERE id=?", (kennung,))

    # ---------------------------------------------------------------- intern
    @staticmethod
    def _zu_aufgabe(zeile) -> Aufgabe:
        werte = dict(zeile)
        try:
            werte["daten"] = json.loads(werte.get("daten") or "{}")
        except ValueError:
            werte["daten"] = {}
        # Felder, die der Datensatz kennt und die Klasse nicht, duerfen nicht
        # werfen - genau das passiert beim Schemawechsel im laufenden Betrieb.
        bekannt = {f.name for f in fields(Aufgabe)}
        werte = {k: v for k, v in werte.items() if k in bekannt}
        werte["art"] = werte.get("art") or ART_ALLGEMEIN
        werte["arbeiter"] = werte.get("arbeiter") or ""
        werte["belegt_bis"] = float(werte.get("belegt_bis") or 0.0)
        werte["faellig_ab"] = float(werte.get("faellig_ab") or 0.0)
        return Aufgabe(**werte)

    @staticmethod
    def _zu_ziel(zeile) -> "Ziel":
        werte = dict(zeile)
        try:
            werte["bedingungen"] = json.loads(werte.get("bedingungen") or "[]")
        except ValueError:
            werte["bedingungen"] = []
        try:
            werte["daten"] = json.loads(werte.get("daten") or "{}")
        except ValueError:
            werte["daten"] = {}
        bekannt = {f.name for f in fields(Ziel)}
        return Ziel(**{k: v for k, v in werte.items() if k in bekannt})

    @staticmethod
    def _zu_zeitplan(zeile) -> dict:
        eintrag = dict(zeile)
        try:
            eintrag["daten"] = json.loads(eintrag.get("daten") or "{}")
        except ValueError:
            eintrag["daten"] = {}
        eintrag["aktiv"] = bool(eintrag.get("aktiv"))
        return eintrag

    @staticmethod
    def _zu_ablauf(zeile) -> dict:
        eintrag = dict(zeile)
        try:
            eintrag["ausloeser"] = json.loads(eintrag.get("ausloeser") or "{}")
        except ValueError:
            eintrag["ausloeser"] = {}
        try:
            eintrag["schritte"] = json.loads(eintrag.get("schritte") or "[]")
        except ValueError:
            eintrag["schritte"] = []
        eintrag["aktiv"] = bool(eintrag.get("aktiv"))
        return eintrag

    @staticmethod
    def _zu_ablauflauf(zeile) -> dict:
        eintrag = dict(zeile)
        try:
            eintrag["umgebung"] = json.loads(eintrag.get("umgebung") or "{}")
        except ValueError:
            eintrag["umgebung"] = {}
        try:
            eintrag["protokoll"] = json.loads(eintrag.get("protokoll") or "[]")
        except ValueError:
            eintrag["protokoll"] = []
        return eintrag
