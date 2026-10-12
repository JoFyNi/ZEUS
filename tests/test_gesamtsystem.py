"""Der Abnahmetest: das ECHTE System, nicht seine Bausteine.

Alle anderen Reihen bauen sich ihre Teile selbst zusammen - das ist richtig
fuer Bausteine und wertlos fuer die Frage, um die es hier geht: Laeuft das,
was beim Start von `jarvis_run.py` tatsaechlich entsteht?

Deshalb wird hier der richtige `JarvisAgent` gebaut, der richtige Kern
hochgefahren (sieben Module), und die Arbeit laeuft durch die richtigen
`VaultTools` in eine richtige Vault auf der Platte. Gestubbt sind nur die
Einbettungen und das Sprachmodell - das eine braucht ein GPU-Modell, das
andere einen laufenden Server. Beides sagt nichts ueber die Integration aus.

Gefahren wird der Weg aus §48:

    Eingabe -> Ziel -> Aufgabe -> Plan -> Werkzeug -> Beobachtung ->
    Verifikation -> Neuplanung -> Gedaechtnis -> Artefakt -> Ereignis ->
    Persistenz -> Abschluss

und dazu die Faelle, an denen sich zeigt, ob die Schranken wirklich im Weg
stehen: Rechtestufe, Freigabe, Absturz, Ereignis, Terminal-Werkzeugweg.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_gesamt_")).resolve()
VAULT = TMP / "Zeus"
for ordner in ("00_Inbox", "01_Wissen", "99_Papierkorb"):
    (VAULT / ordner).mkdir(parents=True, exist_ok=True)
(VAULT / "01_Wissen" / "Sandkisten.md").write_text(
    "# Sandkisten\n\nEine Sandkiste trennt fremden Code vom eigenen System.\n"
    "Sie begrenzt Zeit, Ort und Umgebung eines fremden Prozesses.\n",
    encoding="utf-8")

os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["KERN_AKTIV"] = "true"
os.environ["AUFGABEN_AKTIV"] = "true"
# Stufe 2: unbeaufsichtigt arbeiten. Ohne sie ruht der Stapel - und genau
# das wird weiter unten auch geprueft.
os.environ["RECHTE_STUFE"] = "2"
os.environ["WARTUNG_ALLE"] = "0"
os.environ["LEARNING_ENABLED"] = "false"
os.environ["SESSION_MEMORY"] = "false"

import stubs  # noqa: E402

stubs.install()

from jarvis.agent import JarvisAgent                       # noqa: E402
from jarvis.config import reload_settings, settings        # noqa: E402
from jarvis.index import KnowledgeIndex                    # noqa: E402
from jarvis.kern.aufgabenspeicher import (                 # noqa: E402
    FERTIG, GESCHEITERT, NEUPLANUNG, WARTET_AUF_FREIGABE, ZIEL_ERREICHT,
    Aufgabenspeicher)
from jarvis.kern.regelwerk import Regel                    # noqa: E402

FAILURES = []


def notiz_da(stamm: str) -> bool:
    """Liegt eine Notiz dieses Namens in der Vault?

    Ueber `rglob` und nicht ueber einen festen Pfad: Die Vault normalisiert
    Dateinamen (aus "Vermerk Alpha" wird "Vermerk_Alpha.md") und waehlt den
    Ordner notfalls selbst. Ein Test, der den exakten Pfad vorschreibt,
    prueft die Namensregeln statt der Integration - und faellt um, sobald
    jemand sie anfasst.
    """
    marke = stamm.replace(" ", "_").lower()
    return any(p.stem.replace(" ", "_").lower() == marke
               for p in VAULT.rglob("*.md"))


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def agent_bauen():
    """Der ECHTE Agent - nur Einbettung und Modell sind Attrappen.

    `cfg` wird AUSDRUECKLICH uebergeben: `JarvisAgent.__init__` hat
    `cfg=settings` als Vorgabewert, und der ist beim Import gebunden. Ein
    `reload_settings()` taucht deshalb in einem neu gebauten Agenten nicht
    auf, wenn man ihm die neuen Einstellungen nicht in die Hand gibt - die
    Rechtestufe eines Tests waere sonst stillschweigend die alte.
    """
    reload_settings()
    import jarvis.config as konfig
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(cfg=konfig.settings, index=index)
    # Kein Ollama: Das Modell antwortet mit leerem Text. Alles, was hier
    # geprueft wird, muss ohne Modell funktionieren - der Zerleger nimmt
    # dann die Fachagenten oder die mitgegebenen Schritte.
    agent.llm.chat = (lambda self, m, **k: "").__get__(agent.llm)
    agent.llm.chat_message = (lambda self, m, **k: {"content": ""}).__get__(agent.llm)
    agent.llm.model_capabilities = (lambda self, m=None: []).__get__(agent.llm)
    return agent


# ====================================================== 1. Der volle Durchlauf
def fall_voller_durchlauf() -> None:
    agent = agent_bauen()
    kern = agent.kern_starten()
    try:
        check("Der Kern ist hochgefahren", kern.laeuft)
        check("Das Aufgabenmodul laeuft",
              kern.modul("aufgaben").gesundheit().ok
              or "warten" in kern.modul("aufgaben").gesundheit().meldung,
              kern.modul("aufgaben").gesundheit().meldung)
        check("Der Aufgabenstapel haengt am Kern",
              all(getattr(kern, teil, None) is not None for teil in
                  ("speicher", "tor", "laeufer", "stube", "regelwerk",
                   "ablaufwerk", "zeitgeber")),
              ", ".join(t for t in ("speicher", "tor", "laeufer", "stube",
                                    "regelwerk", "ablaufwerk", "zeitgeber")
                        if getattr(kern, t, None) is None) or "alle da")
        check("Das Gespraech benutzt dasselbe Werkzeugtor",
              getattr(agent, "_werkzeugtor", None) is kern.tor)

        ziel, wurzel = kern.auftrag_annehmen(
            "Erstelle eine Uebersicht zum Thema Sandkisten",
            ergebnis="Eine Notiz mit dem Stand zum Thema liegt in der Vault",
            quelle="abnahmetest")
        check("Das Ziel ist persistent", kern.speicher.ziel(ziel.id) is not None,
              ziel.id)

        bericht = kern.arbeiten(max_runden=40, frist=120)
        speicher = kern.speicher
        wurzel = speicher.holen(wurzel.id)
        kinder = speicher.kinder(wurzel.id)

        check("Ein Aufgabengraph ist entstanden", len(kinder) >= 2,
              f"{len(kinder)} Schritte")
        check("Der Plan kam von einem Fachagenten, nicht geraten",
              any(z["art"] == "plan" and z["name"].startswith("agent:")
                  for z in speicher.laeufe(wurzel.id)),
              str([(z["art"], z["name"]) for z in speicher.laeufe(wurzel.id)]))
        check("Alle Schritte sind durch",
              all(k.zustand == FERTIG for k in kinder),
              ", ".join(f"{k.titel[:24]}={k.zustand}" for k in kinder))
        check("Die Wurzel ist fertig", wurzel.zustand == FERTIG, wurzel.zustand)
        check("Das Ziel gilt als erreicht",
              speicher.ziel(ziel.id).zustand == ZIEL_ERREICHT,
              speicher.ziel(ziel.id).zustand)

        # Das ECHTE Ergebnis: eine Datei in der Vault.
        neue = [p for p in VAULT.rglob("*.md")
                if "Sandkist" in p.stem and p.stem != "Sandkisten"]
        check("Eine neue Notiz liegt wirklich auf der Platte", bool(neue),
              str([p.name for p in neue]))
        if neue:
            check("Sie hat Inhalt",
                  len(neue[0].read_text(encoding="utf-8").strip()) > 20,
                  f"{len(neue[0].read_text(encoding='utf-8'))} Zeichen")

        artefakte = []
        for kind in kinder:
            artefakte.extend(speicher.artefakte(kind.id))
        check("Das Artefakt ist verzeichnet", bool(artefakte),
              str([a["ort"] for a in artefakte]))

        telemetrie = [z for k in kinder for z in speicher.laeufe(k.id)]
        check("Jeder Schritt hat Telemetrie",
              sum(1 for z in telemetrie if z["art"] == "werkzeug") >= 2,
              f"{len(telemetrie)} Zeilen")
        check("Die Verifikation steht im Urteil",
              any(z["urteil"] in ("bestanden", "nicht_pruefbar")
                  for z in telemetrie),
              str(sorted({z["urteil"] for z in telemetrie})))
        check("Der Bus hat die Arbeit gemeldet",
              any(e.name == "ziel.erreicht" for e in kern.bus.verlauf(200)),
              str(sorted({e.name for e in kern.bus.verlauf(200)
                          if e.name.startswith(("ziel", "aufgabe"))})[:6]))
        check("Die Bilanz stimmt", bericht.get("fertig", 0) >= 3,
              str(bericht.get("fertig")))
        check("Nichts blieb offen", bericht.get("offen", 1) == 0,
              str(bericht.get("offen")))
    finally:
        agent.kern_anhalten()


# ================================================ 2. Rechte im echten System
def fall_rechte_echt() -> None:
    """Auf Lesestufe darf auch der echte Werkzeugweg nichts aendern."""
    os.environ["RECHTE_STUFE"] = "0"
    agent = agent_bauen()
    kern = agent.kern_starten()
    try:
        ergebnis = kern.tor.ausfuehren(
            "notiz_erstellen", {"titel": "Darf nicht sein", "ordner": "00_Inbox"})
        check("Der echte Werkzeugweg weist ab", not ergebnis.ok,
              ergebnis.meldung[:70])
        check("Und es liegt nichts auf der Platte",
              not (VAULT / "00_Inbox" / "Darf nicht sein.md").exists())

        # Derselbe Weg, den die Gespraechsschleife nimmt.
        satz = agent._run_tool("notiz_erstellen",
                               {"titel": "Auch nicht", "ordner": "00_Inbox"}, None)
        check("Auch die Gespraechsschleife weist ab", not satz.ergebnis.ok,
              satz.ergebnis.meldung[:70])
        check("Die Begruendung nennt die Rechtestufe",
              "Rechtestufe" in satz.ergebnis.meldung,
              satz.ergebnis.meldung[:80])
        check("Und auch hier bleibt die Vault unberuehrt",
              not (VAULT / "00_Inbox" / "Auch nicht.md").exists())

        lesen = agent._run_tool("notizen_suchen", {"frage": "Sandkiste"}, None)
        check("Lesen geht weiterhin", lesen.ergebnis.ok,
              lesen.ergebnis.meldung[:60])
    finally:
        agent.kern_anhalten()
        os.environ["RECHTE_STUFE"] = "2"


# ============================================ 3. Freigabe im echten System
def fall_freigabe_echt() -> None:
    """Eine Loeschung unbeaufsichtigt: wartet, bis ein Mensch zustimmt."""
    (VAULT / "00_Inbox" / "Loeschkandidat.md").write_text(
        "# Loeschkandidat\n\nDarf weg, aber erst nach Rueckfrage.\n",
        encoding="utf-8")
    agent = agent_bauen()
    kern = agent.kern_starten()
    try:
        ziel, wurzel = kern.auftrag_annehmen(
            "Entferne die Notiz Loeschkandidat",
            daten={"schritte": [{"beschreibung": "Notiz entfernen",
                                 "werkzeug": "notiz_loeschen",
                                 "parameter": {"name": "Loeschkandidat",
                                               "grund": "Abnahmetest"}}]},
            quelle="abnahmetest")
        kern.arbeiten(max_runden=15, frist=60)

        kinder = kern.speicher.kinder(wurzel.id)
        check("Die Aufgabe wartet auf eine Freigabe",
              any(k.zustand == WARTET_AUF_FREIGABE for k in kinder),
              ", ".join(k.zustand for k in kinder))
        check("Die Notiz liegt noch da",
              (VAULT / "00_Inbox" / "Loeschkandidat.md").exists())

        offen = kern.speicher.offene_freigaben()
        check("Die Frage steht in der Ablage", len(offen) == 1,
              offen[0]["frage"][:80] if offen else "keine")
        kern.speicher.freigabe_beantworten(offen[0]["id"], True, "ja, weg damit")
        kern.arbeiten(max_runden=15, frist=60)

        check("Nach der Freigabe ist die Notiz weg",
              not (VAULT / "00_Inbox" / "Loeschkandidat.md").exists())
        check("Und der Schritt ist fertig",
              all(k.zustand == FERTIG
                  for k in kern.speicher.kinder(wurzel.id)),
              ", ".join(k.zustand for k in kern.speicher.kinder(wurzel.id)))
        check("Das Ziel gilt als erreicht",
              kern.speicher.ziel(ziel.id).zustand == ZIEL_ERREICHT,
              kern.speicher.ziel(ziel.id).zustand)
    finally:
        agent.kern_anhalten()


# ================================================ 4. Ereignis im echten Bus
def fall_ereignis_echt() -> None:
    agent = agent_bauen()
    kern = agent.kern_starten()
    try:
        kern.regelwerk.aufnehmen(Regel(
            name="eingang", ereignis="test.eingang",
            ziel="Den Eingang '{stueck}' vermerken",
            schritte=[{"beschreibung": "Vermerk schreiben",
                       "werkzeug": "notiz_erstellen",
                       "parameter": {"titel": "Vermerk {stueck}",
                                     "ordner": "00_Inbox",
                                     "inhalt": "Kam ueber den Ereignisbus."}}],
            sperrzeit=0))
        vorher = len(kern.speicher.ziele())
        kern.bus.melden("test.eingang", quelle="abnahmetest", stueck="Alpha")
        check("Das Ereignis hat ein Ziel erzeugt",
              len(kern.speicher.ziele()) == vorher + 1,
              f"{len(kern.speicher.ziele())} Ziele")

        kern.arbeiten(max_runden=20, frist=60)
        check("Daraus wurde eine echte Datei", notiz_da("Vermerk Alpha"),
              str(sorted(p.name for p in VAULT.rglob("Vermerk*"))))
        check("Die Zuendung steht im Ereignisverlauf",
              any(e.name == "regel.gezuendet" for e in kern.bus.verlauf(200)))
    finally:
        agent.kern_anhalten()


# ===================================== 5. Absturz und Wiederaufnahme, echt
def fall_absturz_echt() -> None:
    """Prozess weg, Kern neu - der Stand steht in der Datenbank."""
    agent = agent_bauen()
    kern = agent.kern_starten()
    # Die Hintergrundarbeiter anhalten: Dieser Fall stellt einen Absturz
    # Schritt fuer Schritt nach, und das geht nur, wenn genau EINER zieht.
    # Mit laufenden Arbeitern waere das Ergebnis vom Zufall abhaengig - und
    # ein Test, der manchmal gruen ist, ist kein Test.
    kern.stube.anhalten()
    speicher = kern.speicher
    ziel, wurzel = kern.auftrag_annehmen(
        "Lege nacheinander zwei Merkzettel an",
        daten={"schritte": [
            {"beschreibung": "Ersten Merkzettel anlegen",
             "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "MerkzettelEins", "ordner": "00_Inbox",
                           "inhalt": "Der erste Merkzettel des Abnahmetests."}},
            {"beschreibung": "Zweiten Merkzettel anlegen",
             "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "MerkzettelZwei", "ordner": "00_Inbox",
                           "inhalt": "Der zweite Merkzettel, nach dem ersten."},
             "braucht": [1]}]},
        quelle="abnahmetest")
    kern.stube.runde("abnahme")      # zerlegen
    kern.stube.runde("abnahme")      # erster Schritt
    speicher.blockaden_loesen()
    # Den Absturz nachstellen: Der zweite Schritt gilt als uebernommen,
    # danach faellt der Prozess aus.
    uebernommen = speicher.beanspruchen("sterbender_arbeiter", dauer=1)
    check("Ein Schritt war in Arbeit", uebernommen is not None,
          uebernommen.titel[:40] if uebernommen else "keiner")
    check("Der erste Merkzettel liegt schon da", notiz_da("MerkzettelEins"),
          str(sorted(p.name for p in VAULT.rglob("Merkzettel*"))))
    agent.kern_anhalten()
    del agent, kern

    # --- Neustart ---
    agent = agent_bauen()
    kern = agent.kern_starten()
    kern.stube.anhalten()
    try:
        speicher = kern.speicher
        check("Das Ziel hat den Neustart ueberlebt",
              speicher.ziel(ziel.id) is not None)
        # Am VERLAUF geprueft, nicht am Momentzustand: Ein Arbeiter kann
        # den Schritt bereits weitergefuehrt haben, bevor der Test hinsieht.
        # Der Eintrag im Verlauf bleibt - er ist der Beleg.
        erkannt = [a for a in speicher.aufgaben_zum_ziel(ziel.id)
                   if any(e["nach"] == NEUPLANUNG and "Neustart" in (e["grund"] or "")
                          for e in speicher.verlauf(a.id))]
        check("Der unterbrochene Schritt wurde als solcher erkannt",
              bool(erkannt), ", ".join(a.zustand for a in
                                       speicher.aufgaben_zum_ziel(ziel.id)))
        check("Der erledigte Schritt blieb erledigt",
              any(a.zustand == FERTIG
                  for a in speicher.aufgaben_zum_ziel(ziel.id)))
        check("Die Telemetrie von vorher ist noch da",
              any(speicher.laeufe(a.id)
                  for a in speicher.aufgaben_zum_ziel(ziel.id)))
        check("Der erste Merkzettel liegt unveraendert da",
              notiz_da("MerkzettelEins"))

        kern.arbeiten(max_runden=30, frist=90)
        check("Die Arbeit lief zu Ende", notiz_da("MerkzettelZwei"),
              speicher.ziel(ziel.id).zustand)
        check("Der erste Merkzettel wurde NICHT doppelt geschrieben",
              len([p for p in VAULT.rglob("MerkzettelEins*.md")]) == 1,
              str([p.name for p in VAULT.rglob("MerkzettelEins*.md")]))
    finally:
        agent.kern_anhalten()


# ===================================== 6. Ohne Rechte ruht der Stapel sichtbar
def fall_ruht_sichtbar() -> None:
    os.environ["RECHTE_STUFE"] = "1"
    agent = agent_bauen()
    kern = agent.kern_starten()
    try:
        modul = kern.modul("aufgaben")
        check("Der Stapel steht trotzdem bereit", kern.speicher is not None)
        check("Er sagt, dass er ruht - und warum",
              "ruht" in modul.gesundheit().meldung.lower()
              or "Rechtestufe" in modul.gesundheit().meldung,
              modul.gesundheit().meldung[:90])
        check("Keine Hintergrundarbeiter", not kern.stube.laeuft)
        ziel, _ = kern.auftrag_annehmen("Etwas im Vordergrund tun",
                                        daten={"schritte": [
                                            {"beschreibung": "Suchen",
                                             "werkzeug": "notizen_suchen",
                                             "parameter": {"frage": "Sandkiste"}}]})
        kern.arbeiten(max_runden=10, frist=30)
        check("Im Vordergrund laesst sich trotzdem arbeiten",
              kern.speicher.ziel(ziel.id).zustand == ZIEL_ERREICHT,
              kern.speicher.ziel(ziel.id).zustand)
    finally:
        agent.kern_anhalten()
        os.environ["RECHTE_STUFE"] = "2"


def main() -> int:
    print("=== Abnahmetest: das echte Gesamtsystem ===\n")
    print(f"Vault: {VAULT}\n")
    for name, fall in (
        ("Der volle Durchlauf", fall_voller_durchlauf),
        ("Rechte halten im echten System auf", fall_rechte_echt),
        ("Freigabe im echten System", fall_freigabe_echt),
        ("Ereignis im echten Bus", fall_ereignis_echt),
        ("Absturz und Wiederaufnahme", fall_absturz_echt),
        ("Ohne Rechte ruht der Stapel sichtbar", fall_ruht_sichtbar),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Das Gesamtsystem laeuft: Ziel, Plan, Werkzeug, Pruefung, "
          "Freigabe, Absturz, Ereignis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
