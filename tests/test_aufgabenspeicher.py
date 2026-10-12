"""Aufgabenspeicher und Verifikation - die zwei tragenden Luecken.

Geprueft werden die Punkte aus dem Master-Prompt, die bisher gar nicht
existierten:

    §6/§7   Universal Task Model, kontrollierter Zustandsautomat
    §8      Abhaengigkeiten zwischen Aufgaben
    §23     Human-in-the-loop, im Task-State abgebildet
    §28     Audit Trail ueber jeden Zustandswechsel
    §36     Schutz vor Endlosschleifen (Versuchsgrenzen)
    §38/§39 Persistenz und Wiederaufnahme nach Neustart
    §60     Verifikationsschicht

Der Massstab aus §65: Eine Funktion gilt erst als fertig, wenn sie
implementiert, integriert, fehlerbehandelt, protokolliert und GETESTET ist.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TMP = Path(tempfile.mkdtemp(prefix="jarvis_speicher_")).resolve()
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.aufgabenspeicher import (  # noqa: E402
    ABGEBROCHEN, BEREIT, FERTIG, GESCHEITERT, LAEUFT, NEU, NEUPLANUNG,
    PLANUNG, PRUEFUNG, WARTET_AUF_ABHAENGIGKEIT, WARTET_AUF_FREIGABE,
    Aufgabenspeicher, Zustandsfehler,
)

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def main() -> int:
    db = TMP / "aufgaben.sqlite3"
    speicher = Aufgabenspeicher(db)

    # ======================================== 1. Anlegen und Zustandsautomat
    a = speicher.anlegen("Zwei Notizen zusammenführen",
                         auftrag="füge A und B zusammen", prioritaet=2)
    check("Eine Aufgabe entsteht mit Kennung", bool(a.id) and len(a.id) == 16, a.id)
    check("Sie startet im Zustand 'neu'", a.zustand == NEU, a.zustand)
    check("Und gilt als offen", a.offen)

    a = speicher.wechseln(a.id, PLANUNG, "Auftrag wird zerlegt")
    check("Wechsel neu → planung ist erlaubt", a.zustand == PLANUNG, a.zustand)

    try:
        speicher.wechseln(a.id, FERTIG, "einfach so")
        check("Ein verbotener Wechsel wird abgewiesen", False, "kein Fehler geworfen")
    except Zustandsfehler as exc:
        check("Ein verbotener Wechsel wird abgewiesen", True, str(exc)[:58])

    speicher.wechseln(a.id, BEREIT)
    speicher.wechseln(a.id, LAEUFT)
    speicher.wechseln(a.id, PRUEFUNG, "Ergebnis wird geprüft")
    a = speicher.wechseln(a.id, FERTIG, "Prüfung bestanden")
    check("Der volle Weg bis 'fertig' geht durch", a.zustand == FERTIG)
    check("Ein Endzustand gilt nicht mehr als offen", not a.offen)

    try:
        speicher.wechseln(a.id, LAEUFT, "wiederbeleben")
        check("Ein Endzustand hat keine Ausgänge", False, "Wiederbelebung ging durch")
    except Zustandsfehler:
        check("Ein Endzustand hat keine Ausgänge", True)

    # =========================================================== 2. Audit Trail
    verlauf = speicher.verlauf(a.id)
    check("Jeder Wechsel steht im Verlauf", len(verlauf) == 6, f"{len(verlauf)} Einträge")
    check("Der Verlauf nennt Von und Nach",
          verlauf[1]["von"] == NEU and verlauf[1]["nach"] == PLANUNG,
          f"{verlauf[1]['von']} → {verlauf[1]['nach']}")
    check("Und den Grund", verlauf[1]["grund"] == "Auftrag wird zerlegt",
          verlauf[1]["grund"])
    check("Und den Urheber", verlauf[1]["akteur"] == "zeus")

    # ======================================================= 3. Abhängigkeiten
    erst = speicher.anlegen("Notiz A lesen")
    dann = speicher.anlegen("Ergebnis schreiben", benoetigt=[erst.id])
    check("Eine abhängige Aufgabe startet blockiert",
          dann.zustand == WARTET_AUF_ABHAENGIGKEIT, dann.zustand)
    check("Der Blocker wird benannt", speicher.blockiert_von(dann.id) == [erst.id])
    check("Und sie wird NICHT als nächste gezogen",
          (speicher.naechste() or erst).id != dann.id)

    speicher.wechseln(erst.id, BEREIT)
    speicher.wechseln(erst.id, LAEUFT)
    speicher.wechseln(erst.id, PRUEFUNG)
    speicher.wechseln(erst.id, FERTIG)
    check("Nach Erfüllen ist nichts mehr blockiert",
          speicher.blockiert_von(dann.id) == [])
    naechste = speicher.naechste()
    check("Jetzt wird sie freigegeben und gezogen",
          naechste is not None and naechste.id == dann.id,
          naechste.zustand if naechste else "keine")

    # ============================================= 4. Human-in-the-loop (§23)
    heikel = speicher.anlegen("Alle Notizen im Ordner löschen", risiko="hoch")
    speicher.wechseln(heikel.id, PLANUNG)
    freigabe = speicher.freigabe_erbitten(
        heikel.id, "Soll ich wirklich 42 Notizen löschen?", risiko="hoch",
        moeglichkeiten=["ja, löschen", "nein, abbrechen"])
    heikel = speicher.holen(heikel.id)
    check("Eine heikle Aufgabe wartet auf Freigabe",
          heikel.zustand == WARTET_AUF_FREIGABE, heikel.zustand)
    check("Und gilt als 'wartet auf Mensch'", heikel.wartet_auf_mensch)
    offen = speicher.offene_freigaben()
    check("Die Frage steht in der Liste offener Freigaben", len(offen) == 1)
    check("Mit Risiko und Auswahlmöglichkeiten",
          offen[0]["risiko"] == "hoch" and len(offen[0]["moeglichkeiten"]) == 2)

    speicher.freigabe_beantworten(freigabe, False, "zu riskant")
    heikel = speicher.holen(heikel.id)
    check("Eine Ablehnung bricht die Aufgabe ab", heikel.zustand == ABGEBROCHEN,
          heikel.zustand)
    check("Danach steht keine Freigabe mehr offen", not speicher.offene_freigaben())

    zweite = speicher.anlegen("Etwas Genehmigungspflichtiges")
    speicher.wechseln(zweite.id, PLANUNG)
    f2 = speicher.freigabe_erbitten(zweite.id, "Darf ich?")
    speicher.freigabe_beantworten(f2, True, "ja, mach")
    check("Eine Zustimmung macht die Aufgabe wieder bereit",
          speicher.holen(zweite.id).zustand == BEREIT)

    # ===================================== 5. Endlosschleifenschutz (§36)
    zaeh = speicher.anlegen("Etwas, das nie klappt", prioritaet=9)
    speicher.wechseln(zaeh.id, BEREIT)
    speicher.wechseln(zaeh.id, LAEUFT)
    nach_erstem = speicher.fehlschlag(zaeh.id, "Server weg")
    check("Der erste Fehlschlag führt zur Neuplanung",
          nach_erstem.zustand == NEUPLANUNG, nach_erstem.zustand)
    check("Und wird gezählt", nach_erstem.versuche == 1)

    speicher.wechseln(zaeh.id, BEREIT)
    speicher.wechseln(zaeh.id, LAEUFT)
    speicher.aktualisieren(zaeh.id, neuplanungen=2)      # Grenze erreicht
    endstand = speicher.fehlschlag(zaeh.id, "Server immer noch weg")
    check("Nach Erreichen der Grenze wird aufgegeben",
          endstand.zustand == GESCHEITERT, endstand.zustand)
    check("Der Grund steht in der Aufgabe",
          "Server" in endstand.fehler, endstand.fehler[:40])

    # ==================================== 6. Persistenz ueber Neustart (§38)
    offene_vorher = len(speicher.alle(nur_offene=True))
    laufend = speicher.anlegen("Mitten in der Arbeit")
    speicher.wechseln(laufend.id, BEREIT)
    speicher.wechseln(laufend.id, LAEUFT)
    speicher.artefakt_ablegen(laufend.id, "notiz", "00_Inbox/Ergebnis.md",
                              "das Zwischenergebnis")
    speicher.schliessen()

    # --- „Neustart": neue Instanz auf derselben Datei -----------------------
    neu_speicher = Aufgabenspeicher(db)
    wieder = neu_speicher.holen(laufend.id)
    check("Die Aufgabe überlebt den Neustart", wieder is not None)
    check("Mit Titel und Zustand",
          wieder.titel == "Mitten in der Arbeit" and wieder.zustand == LAEUFT,
          f"{wieder.zustand}" if wieder else "")
    check("Artefakte überleben ebenfalls",
          len(neu_speicher.artefakte(laufend.id)) == 1)
    check("Der Verlauf ist vollständig erhalten",
          len(neu_speicher.verlauf(laufend.id)) == 3)

    # ================================== 7. Wiederaufnahme nach Absturz (§39)
    wieder_aufgenommen = neu_speicher.wiederaufnehmen()
    check("Eine beim Absturz laufende Aufgabe wird nicht blind fortgesetzt",
          all(a.zustand == NEUPLANUNG for a in wieder_aufgenommen),
          str([a.zustand for a in wieder_aufgenommen]))
    check("Der Grund steht im Verlauf",
          any("Neustart" in e["grund"] for e in neu_speicher.verlauf(laufend.id)))
    check("Und die Zahl offener Aufgaben stimmt",
          len(neu_speicher.alle(nur_offene=True)) == offene_vorher + 1,
          str(len(neu_speicher.alle(nur_offene=True))))

    # ================================================ 8. Kennzahlen
    zahlen = neu_speicher.kennzahlen()
    check("Kennzahlen nennen Gesamtzahl und Offene",
          zahlen["gesamt"] >= 6 and zahlen["offen"] >= 1, str(zahlen["gesamt"]))
    check("Und zählen nach Zustand auf", isinstance(zahlen["nach_zustand"], dict))
    neu_speicher.schliessen()

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
