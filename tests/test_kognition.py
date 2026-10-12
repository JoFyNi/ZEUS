"""Tests für Arbeits- und Episodengedächtnis, Eingangsstufe und Antwortprüfung.

Anlass ist das Audit vom 12.10.2026 (claude/BERICHT_Kognitive_Architektur.md).
Gefunden und hier abgesichert:

* Was aus dem Verlauf (HISTORY_TURNS) fiel und noch nicht verdichtet war,
  war für das Modell nirgends mehr erreichbar - auch keine Vorgabe aus Zug 1.
* "die Notiz", "die Datei" musste das Modell raten.
* Eine neue Angabe, die einer früheren widersprach, merkte niemand; im
  Sitzungsgedächtnis verschwand dafür eine RICHTIGE ("Rückfallmodell ist ...").
* `fold()` löschte jede nicht-lateinische Schrift: similarity("Москва",
  "Москва") war 0.
* Rechnungen, Grenzen und [[Quellen]] in der Antwort prüfte niemand.

Jede Gruppe entspricht einem Prüfszenario des Auftrags (A bis I). Gruppe J
ist die übrige Testsuite (`python tests/alle.py`). Am Ende stehen Messwerte -
Abrufgenauigkeit, Latenz, Promptanteil -, nicht nur Ja/Nein.

Das Modell ist ein Skript: Es antwortet, was der Test vorgibt, und zeichnet
auf, was es zu sehen bekam. Geprüft wird also, was der CODE dem Modell
vorlegt und was er aus dessen Antwort macht - nicht, ob ein echtes Modell
daraus das Richtige liest. Das misst `--evaluieren` am echten Server.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_kognition_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["KERN_AKTIV"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.aussagen import (ERGAENZUNG, FREMD, GLEICH, WIDERSPRUCH,  # noqa: E402
                             vergleichen)
from jarvis.config import settings as cfg  # noqa: E402
from jarvis.episoden import (Episodengedaechtnis, schwaerzen,  # noqa: E402
                             vorgaben_finden)
from jarvis.gespraech.pruefung import (HERKUNFT_HINWEIS, grenzen_pruefen,  # noqa: E402
                                       rechnungen_pruefen)
from jarvis.index import KnowledgeIndex, tokens  # noqa: E402
from jarvis.sitzung import Sitzungsfakt, Sitzungsgedaechtnis, _widerspruchsfrei  # noqa: E402
from jarvis.sprache import aufbereiten, auf_last_kuerzen, textlast  # noqa: E402
from jarvis.vault import fold, similarity  # noqa: E402

FAILURES = []
MESSWERTE = {}


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# =================================================================== Gerüst
class Skriptmodell:
    """Antwortet aus einer Warteschlange, sonst mit einem festen Satz.

    Ein Eintrag ist entweder Text (Prosa) oder eine Liste von
    (werkzeug, parameter) - dann kommen Werkzeugaufrufe, und die nächste
    Runde der Schleife holt den nächsten Eintrag.
    """

    def __init__(self, agent: JarvisAgent) -> None:
        self.agent = agent
        self.warteschlange: list = []
        self.standard = "Verstanden."
        self.gesehen: list = []
        agent.llm.chat = self._chat
        agent.llm.chat_message = self._chat_message
        agent._native_tools = True
        agent._capabilities = ["tools"]

    def _naechste(self):
        return self.warteschlange.pop(0) if self.warteschlange else self.standard

    def _chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kw):
        if format_json:
            return "{}"
        self.gesehen.append(list(messages))
        antwort = self._naechste()
        return antwort if isinstance(antwort, str) else self.standard

    def _chat_message(self, messages, tools=None, stream_cb=None, **kw):
        self.gesehen.append(list(messages))
        antwort = self._naechste()
        if isinstance(antwort, str):
            return {"role": "assistant", "content": antwort}
        return {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": name, "arguments": parameter}} for name, parameter in antwort]}

    @property
    def systemprompt(self) -> str:
        return self.gesehen[-1][0]["content"] if self.gesehen else ""

    @property
    def letzte_nachrichten(self) -> list:
        return self.gesehen[-1] if self.gesehen else []


def notiz(name: str, inhalt: str) -> None:
    (VAULT / f"{name}.md").write_text(f"# {name}\n\n{inhalt}\n", encoding="utf-8")


def neuer_agent(episoden_datei: Path = None):
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()
    if episoden_datei is not None:
        agent._episoden_obj = Episodengedaechtnis(agent.cfg, pfad=episoden_datei)
    return agent, Skriptmodell(agent)


FUELLER = [
    "Was ist der Unterschied zwischen Espresso und Filterkaffee?",
    "Wie wird das Wetter am Wochenende in Hamburg?",
    "Welche Musik hört man beim Joggen am besten?",
    "Erzähl mir etwas über die Geschichte von Rom.",
    "Wie lange kocht man Spaghetti al dente?",
    "Welche Pflanzen eignen sich für einen schattigen Balkon?",
    "Was macht einen guten Rotwein aus?",
    "Wie pflegt man ein Fahrrad im Winter?",
]


def episoden_datei(name: str) -> Path:
    pfad = TMP / "episoden" / f"{name}.jsonl"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    return pfad


# ===================================================== A: Gesprächskontinuität
def pruefung_a() -> None:
    print("\n--- A: Vorgabe vom Anfang gilt noch nach dem Verlauf ---")
    agent, modell = neuer_agent(episoden_datei("a"))
    vorgabe = ("Wichtig für das Projekt Falke: Alle Skripte müssen mit Python 3.9 "
               "laufen, ohne externe Pakete.")
    agent.ask(vorgabe)
    for frage in FUELLER:
        agent.ask(frage)
    im_verlauf = any("Python 3.9" in (m.get("content") or "") for m in agent.history)
    check("A: Die Vorgabe ist aus dem Verlauf gefallen (Voraussetzung)",
          not im_verlauf, f"{len(agent.history)} Nachrichten im Verlauf")

    modell.warteschlange.append("Ich nutze nur das Modul csv aus der Standardbibliothek.")
    agent.ask("Wie würdest du für Falke zwei CSV-Dateien zusammenführen?")
    prompt = modell.systemprompt
    check("A: Die Vorgabe steht trotzdem im Prompt",
          "Python 3.9" in prompt and "ohne externe Pakete" in prompt)
    check("A: ... als geltende Vorgabe des Nutzers, mit Zugnummer",
          "GELTENDE VORGABEN" in prompt and "(Zug 1," in prompt)
    geplant = []
    agent.planner.plan_erstellen = lambda auftrag, kontext="", verlauf=None: (
        geplant.append(kontext), None)[1]
    agent.ask("Leg mir für Falke eine Notiz mit einem Skript an, das CSV-Dateien "
              "zusammenführt.")
    check("A: Der Schreibplaner bekommt sie auch (vor dem Notizkontext)",
          bool(geplant) and "Python 3.9" in geplant[0][:900], str(geplant[:1])[:200])
    MESSWERTE["A_arbeitsblock_zeichen"] = len(agent._arbeitsblock()) if agent._arbeit else \
        len(agent.letztes_arbeitsgedaechtnis.block(agent._arbeitsbudget()))


# ================================================== B: Bezüge auflösen
def pruefung_b() -> None:
    print("\n--- B: 'die Datei', 'die Notiz' ohne Namen ---")
    notiz("Bremsregler", "Die Schwelle steht auf 3. Umgesetzt in regler.py.")
    notiz("Einkaufsliste", "- Milch\n- Brot")
    agent, modell = neuer_agent(episoden_datei("b"))

    modell.warteschlange += [[("notiz_lesen", {"name": "Bremsregler"})],
                             "Die Schwelle steht in `regler.py` auf 3."]
    agent.ask("Lies bitte den Bremsregler und sag mir, wo regler.py die Schwelle setzt.")
    modell.warteschlange += [[("notiz_lesen", {"name": "Einkaufsliste"})],
                             "Auf der Liste stehen Milch und Brot."]
    agent.ask("Was steht auf meiner Einkaufsliste?")

    gesucht = []
    original = agent.index.search

    def mitschreiben(frage, *a, **k):
        gesucht.append(frage)
        return original(frage, *a, **k)

    agent.index.search = mitschreiben
    agent.ask("Was genau steht in der Datei zur Schwelle?")
    arbeit = agent.letztes_arbeitsgedaechtnis
    ziele = [b.ziele[0].name for b in arbeit.bezuege if b.ziele]
    check("B: 'der Datei' -> regler.py (Datei vor Notiz)", ziele[:1] == ["regler.py"],
          str(ziele))
    check("B: Der Bezug steht als Vermutung im Prompt",
          "vermutlich `regler.py`" in modell.systemprompt)
    check("B: Die Suche bekommt den Namen mit", any("regler.py" in g for g in gesucht),
          str(gesucht[-1:]))

    agent.ask("Ergänze die Notiz um einen Abschnitt Quellen.")
    arbeit = agent.letztes_arbeitsgedaechtnis
    ziele = [b.ziele[0].name for b in arbeit.bezuege if b.ziele]
    check("B: 'die Notiz' -> die zuletzt GELESENE Notiz", ziele[:1] == ["Einkaufsliste"],
          str(ziele))

    modell.warteschlange += [[("notiz_lesen", {"name": "Bremsregler"}),
                              ("notiz_lesen", {"name": "Einkaufsliste"})],
                             "Beide gelesen."]
    agent.ask("Lies beide Notizen noch einmal.")
    agent.ask("Was steht in der Notiz ganz oben?")
    arbeit = agent.letztes_arbeitsgedaechtnis
    check("B: Zwei Kandidaten im selben Zug -> unklar statt geraten",
          bool(arbeit.bezuege) and not arbeit.bezuege[0].eindeutig
          and "unklar" in modell.systemprompt,
          str([[z.name for z in b.ziele] for b in arbeit.bezuege]))

    for frage in FUELLER:
        agent.ask(frage)
    agent.ask("Öffne nochmal die Datei mit der Schwelle.")
    arbeit = agent.letztes_arbeitsgedaechtnis
    ziele = [b.ziele[0].name for b in arbeit.bezuege if b.ziele]
    check("B: Auch nach dem Verlauf noch aufgelöst (aus dem Episodengedächtnis)",
          ziele[:1] == ["regler.py"], str(ziele))

    modell.warteschlange.append("Mein Vorschlag: die Schwelle auf 5 setzen.")
    agent.ask("Was schlägst du für die Schwelle vor?")
    agent.ask("Setz die vorige Lösung bitte um.")
    arbeit = agent.letztes_arbeitsgedaechtnis
    check("B: 'die vorige Lösung' -> die letzte eigene Antwort",
          any(a.antwort_bezug and "Schwelle auf 5" in a.zug.antwort for a in arbeit.bezuege))
    MESSWERTE["B_bezuege_richtig"] = "5 von 5 Fällen" if not [
        f for f in FAILURES if f.startswith("B:")] else "Fehler, siehe oben"


# ================================================== C: Langzeitabruf
def pruefung_c() -> None:
    print("\n--- C: Information jenseits des Verlaufs, auch nach Neustart ---")
    datei = episoden_datei("c")
    agent, modell = neuer_agent(datei)
    agent.ask("Der Server Odin hat die IP 10.0.0.42 und läuft mit Debian 12.")
    for frage in FUELLER:
        agent.ask(frage)
    agent.ask("Welche IP hatte der Server Odin nochmal?")
    prompt = modell.systemprompt
    nachrichten = modell.letzte_nachrichten
    verlauf = " ".join(m.get("content") or "" for m in nachrichten[1:])
    check("C: Die IP steht nicht mehr im Verlauf (Voraussetzung)", "10.0.0.42" not in verlauf)
    check("C: ... aber als frühere Gesprächsstelle im Prompt",
          "FRÜHERE GESPRÄCHSSTELLEN" in prompt and "10.0.0.42" in prompt)
    abschnitt = prompt.split("FRÜHERE GESPRÄCHSSTELLEN", 1)[-1].split("KONTEXT AUS DEN", 1)[0]
    unbeteiligt = [f for f in FUELLER if f[:30] in abschnitt]
    check("C: Nicht der ganze Verlauf - keine der unbeteiligten Fragen",
          not unbeteiligt, str(unbeteiligt))
    MESSWERTE["C_erinnerte_zuege"] = len(agent.letztes_arbeitsgedaechtnis.erinnerungen)

    # Neustart: neuer Agent, neue Sitzung, dieselbe Datei.
    agent2, modell2 = neuer_agent(datei)
    agent2.ask("Welche IP hat Odin?")
    check("C: Nach dem Neustart aus der Datei abgerufen",
          "10.0.0.42" in modell2.systemprompt)
    check("C: ... und als frühere Sitzung gekennzeichnet (mit Datum)",
          time.strftime("%Y-%m-%d") in modell2.systemprompt.split("FRÜHERE", 1)[-1])

    agent3, modell3 = neuer_agent(datei)
    agent3.ask("Lass uns da weitermachen, wo wir aufgehört haben.")
    arbeit = agent3.letztes_arbeitsgedaechtnis
    check("C: 'weitermachen' knüpft an die letzte Sitzung an",
          bool(arbeit.fortsetzung) and "ANKNÜPFUNG" in modell3.systemprompt
          and "Odin" in modell3.systemprompt.split("ANKNÜPFUNG", 1)[-1])

    agent4, modell4 = neuer_agent(episoden_datei("c_leer"))
    agent4.ask("Lass uns da weitermachen, wo wir aufgehört haben.")
    check("C: Ohne frühere Sitzung wird DAS gesagt, nicht erfunden",
          "kein früheres Gespräch" in modell4.systemprompt)


# ================================================== D: Widersprüche
def pruefung_d() -> None:
    print("\n--- D: Neue Angabe gegen frühere ---")
    agent, modell = neuer_agent(episoden_datei("d"))
    agent.ask("Unsere Datenbank ist PostgreSQL 16.")
    agent.ask(FUELLER[0])
    agent.ask("Unsere Datenbank ist jetzt MySQL 8.")
    arbeit = agent.letztes_arbeitsgedaechtnis
    check("D: Widerspruch erkannt", len(arbeit.widersprueche) == 1,
          str([(w.neu, w.alt) for w in arbeit.widersprueche]))
    prompt = modell.systemprompt
    check("D: Beide Angaben mit Herkunft im Prompt",
          "MÖGLICHER WIDERSPRUCH" in prompt and "PostgreSQL 16" in prompt
          and "Nutzer, Zug 1" in prompt)
    check("D: Anweisung: nicht beide als gültig behandeln", "nicht beide" in prompt)
    aktuell = [s for s, _ in agent.episoden.nutzeraussagen()]
    check("D: Die alte Angabe gilt danach als ersetzt (nicht gelöscht)",
          "Unsere Datenbank ist PostgreSQL 16." not in aktuell
          and "Unsere Datenbank ist jetzt MySQL 8." in aktuell, str(aktuell))
    roh = agent.episoden.pfad.read_text(encoding="utf-8")
    check("D: Die Ersetzung steht als eigener Datensatz in der Datei",
          '"typ": "ersetzt"' in roh and "PostgreSQL 16" in roh)

    for frage in FUELLER:
        agent.ask(frage)
    agent.ask("Welche Datenbank nutzen wir für das Projekt? Die Datenbank war wichtig.")
    prompt = modell.systemprompt
    if "PostgreSQL 16" in prompt:
        check("D: Wird die alte Stelle erinnert, trägt sie die Ersetzung",
              "später ersetzt in Zug 3" in prompt)
    check("D: Die neue Angabe ist abrufbar (als frühere Stelle, nicht als Widerspruch)",
          "MySQL 8" in prompt.split("FRÜHERE GESPRÄCHSSTELLEN", 1)[-1])
    check("D: 'Die Datenbank war wichtig' ist kein Widerspruch zu 'ist MySQL 8'",
          not agent.letztes_arbeitsgedaechtnis.widersprueche)

    agent.ask("Wir nutzen Redis für den Cache.")
    agent.ask("Wir nutzen außerdem Kafka für Ereignisse.")
    check("D: Ergänzung ist kein Widerspruch",
          not agent.letztes_arbeitsgedaechtnis.widersprueche)

    # Gegen einen verdichteten Sitzungsfakt.
    sitzung = Sitzungsgedaechtnis(agent=agent, cfg=agent.cfg)
    sitzung.beginn = "08:00"
    import datetime as dt
    sitzung.schreiben(fakten=["Das Hauptmodell ist qwen3:32b auf dem KI-Rechner."],
                      zeitpunkt=dt.datetime(2026, 9, 5, 9, 0))
    agent.ask("Das Hauptmodell ist jetzt llama3:70b auf dem KI-Rechner.")
    arbeit = agent.letztes_arbeitsgedaechtnis
    check("D: Widerspruch zu einem Fakt aus einer früheren Sitzung",
          any("Sitzung vom 2026-09-05" in w.herkunft for w in arbeit.widersprueche),
          str([w.herkunft for w in arbeit.widersprueche]))

    # Das Sitzungsgedächtnis selbst: Haupt- und Rückfallmodell sind zwei Dinge.
    beide = _widerspruchsfrei([
        Sitzungsfakt(text="Hauptmodell ist qwen3:32b auf dem KI-Rechner",
                     zeit="2026-09-05 18:30"),
        Sitzungsfakt(text="Rückfallmodell ist qwen3:32b auf dem KI-Rechner",
                     zeit="2026-09-05 18:00")])
    check("D: Rückfallmodell verschwindet nicht mehr neben dem Hauptmodell",
          len(beide) == 2, str([b.text for b in beide]))
    neuer = Sitzungsfakt(text="Das Hauptmodell ist jetzt qwen3:32b.", zeit="2026-09-06 10:00",
                         tag="2026-09-06")
    alter = Sitzungsfakt(text="Das Hauptmodell ist qwen3.8:27b.", zeit="2026-09-05 10:00",
                         tag="2026-09-05")
    gewaehlt = _widerspruchsfrei([neuer, alter])
    check("D: Von zwei widersprechenden Fakten bleibt der jüngere ...",
          [g.text for g in gewaehlt] == [neuer.text])
    check("D: ... und nennt, was er ersetzt", "qwen3.8:27b" in gewaehlt[0].ersetzt)

    faelle = [
        ("Unsere Datenbank ist jetzt MySQL 8.", "Unsere Datenbank ist PostgreSQL 16.",
         WIDERSPRUCH),
        ("Wir nutzen Redis für den Cache.", "Wir nutzen PostgreSQL.", ERGAENZUNG),
        ("Ab sofort nutzen wir MySQL statt PostgreSQL.", "Wir nutzen PostgreSQL.",
         WIDERSPRUCH),
        ("Wir nutzen kein Docker mehr.", "Wir nutzen Docker.", WIDERSPRUCH),
        ("Antworte ab jetzt auf Deutsch.", "Antworte immer auf Englisch.", WIDERSPRUCH),
        ("Antworte kurz.", "Antworte immer auf Englisch.", ERGAENZUNG),
        ("Erstelle jetzt eine Notiz über Hunde.", "Erstelle eine Notiz über Katzen.",
         ERGAENZUNG),
        ("Python 3.12 für das Projekt Falke", "Python 3.9 für das Projekt Falke",
         WIDERSPRUCH),
        ("Hauptmodell ist qwen3:32b", "Rückfallmodell ist qwen3:32b", FREMD),
        ("Наша база данных — MySQL.", "Наша база данных — PostgreSQL.", WIDERSPRUCH),
        ("Das Budget beträgt 500 Euro.", "Das Budget beträgt 500 Euro.", GLEICH),
        # Eine Eigenschaft ist kein anderes Ding (im ersten Gesamtlauf übersehen).
        ("Die Datenbank war wichtig.", "Unsere Datenbank ist jetzt MySQL 8.", ERGAENZUNG),
        ("Die Datenbank ist nicht langsam.", "Die Datenbank ist langsam.", WIDERSPRUCH),
        ("Ich bin jetzt Architekt.", "Ich bin Entwickler.", WIDERSPRUCH),
    ]
    falsch = [(a, b, erwartet, vergleichen(a, b)) for a, b, erwartet in faelle
              if vergleichen(a, b) != erwartet]
    check(f"D: Urteile der Aussagenprüfung ({len(faelle)} Paare)", not falsch, str(falsch))

    # Auf DIREKT gibt es keinen Vektorabruf - auch nicht über den
    # Widerspruchsabgleich gegen Sitzungsfakten (test_gespraech_triage).
    abrufe = []
    original = agent.index.search
    agent.index.search = lambda *a, **k: (abrufe.append(a), original(*a, **k))[1]
    agent.ask("Erledigt ist nicht die Antwort auf meine Frage")
    check("D: Auf DIREKT kein Vektorabruf durch die Widerspruchsprüfung",
          agent.letzte_runde.weg == "direkt" and not abrufe,
          f"{agent.letzte_runde.weg}, {len(abrufe)} Abrufe")
    MESSWERTE["D_urteile_richtig"] = f"{len(faelle) - len(falsch)} von {len(faelle)}"


# ================================================== E: Sprache und Unicode
def pruefung_e() -> None:
    print("\n--- E: Schriften, Normalform, Formeln, Code ---")
    for wort in ("Москва", "東京タワー", "Ελληνικά", "København", "Łódź"):
        check(f"E: fold('{wort}') ist nicht leer und gleich sich selbst",
              bool(fold(wort)) and similarity(wort, wort) == 1.0, repr(fold(wort)))
    check("E: Akzente fallen wie bisher weg", fold("Café naïve") == "cafe naive")
    check("E: Umlaute wie bisher", fold("Grüße aus Köln") == "gruesse aus koeln")
    check("E: Sonderbuchstaben werden umschrieben", fold("Łódź København") == "lodz kobenhavn")
    check("E: CJK wird in Zeichenpaare zerlegt", "東京" in tokens("東京タワーの高さ"),
          str(tokens("東京タワーの高さ")))

    notiz("Москва", "Столица России. Население около 13 миллионов человек.")
    notiz("東京タワー", "東京タワーの高さは333メートルです。")
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    index.sync()
    treffer = [h.metadata.get("stem") for h in index._lexical_hits("Москва население", None, 3)]
    check("E: Stichwortsuche findet die kyrillische Notiz", "Москва" in treffer, str(treffer))
    treffer = [h.metadata.get("stem") for h in index._lexical_hits("東京タワーの高さ", None, 3)]
    check("E: ... und die japanische", "東京タワー" in treffer, str(treffer))
    check("E: Notizname per Ähnlichkeit auflösbar",
          any(n.stem == "Москва" for n, _ in index.structure.match_note("москва")))

    agent, modell = neuer_agent(episoden_datei("e"))
    zerlegt = "Überdenke bitte deine letzte Antwort."
    agent.ask(zerlegt)
    check("E: Zerlegtes 'Ü' trifft nach NFC die Reflexionsregel (Weg DIREKT)",
          agent.letzte_runde.weg == "direkt", agent.letzte_runde.weg)

    eingabe = ("Prüfe bitte diesen Code:\n```python\ndef f(x):\n    return x**2 + 1  # ∑ ≠ ∫\n"
               "```\nUnd: ∑_{i=1}^{n} i² = n(n+1)(2n+1)/6 – gilt das für n ≥ 1?")
    agent.ask(eingabe)
    angekommen = modell.letzte_nachrichten[-1]["content"]
    check("E: Code, Einrückung und Formel kommen Zeichen für Zeichen beim Modell an",
          angekommen == eingabe)
    abgelegt = agent.episoden.zuege[-1].frage
    check("E: ... und so im Episodengedächtnis", abgelegt == eingabe)
    eingang = aufbereiten(eingabe)
    check("E: Merkmale erkannt (Code, Formel)", eingang.hat_code and eingang.hat_formel,
          str(eingang.als_dict()))
    check("E: Schriften gezählt", set(aufbereiten("Hallo Мир 世界").schriften) ==
          {"latein", "kyrillisch", "cjk"})

    check("E: Lateinischer Text: Last = Zeichenzahl", textlast("Grüße, Köln!") == 12)
    cjk = "東京タワーの高さは333メートルです。" * 20
    check("E: CJK wiegt mehr als seine Zeichenzahl", textlast(cjk) > 2 * len(cjk) * 0.9,
          f"{textlast(cjk)} bei {len(cjk)} Zeichen")
    check("E: Kürzen auf Last hält die Grenze",
          textlast(auf_last_kuerzen(cjk, 100)) <= 100 < textlast(cjk))

    stapel = [{"role": "system", "content": "System " * 50}]
    for _ in range(8):
        stapel += [{"role": "user", "content": "Frage " + "東京" * 600},
                   {"role": "assistant", "content": "Antwort " + "タワー" * 400}]
    stapel.append({"role": "user", "content": "Und jetzt?"})
    gekuerzt, _ = agent._auf_budget_kuerzen(stapel, mit_werkzeugen=False)
    last = sum(textlast(m["content"]) for m in gekuerzt)
    check("E: Der Budgetwächter rechnet CJK als Last, nicht als Zeichen",
          last <= agent.cfg.prompt_char_budget, f"{last} / {agent.cfg.prompt_char_budget}")


# ================================================== F: Mehrschritt, Rechnung
def pruefung_f() -> None:
    print("\n--- F: Zwischenergebnisse und Grenzen ---")
    agent, modell = neuer_agent(episoden_datei("f"))
    agent.ask("Das Budget für die Feier beträgt höchstens 500 Euro.")
    modell.warteschlange.append(
        "Tische: 3 × 120 = 380 Euro.\nDeko: 140 Euro.\nInsgesamt kommen wir auf 520 Euro.")
    antwort = agent.ask("Wir brauchen 3 Tische zu je 120 Euro und Deko für 140 Euro. "
                        "Was kostet das insgesamt?")
    check("F: Die Vorgabe steht beim Rechnen im Prompt", "höchstens 500 Euro" in modell.systemprompt)
    check("F: Falsches Zwischenergebnis wird nachgerechnet",
          "Rechenprüfung" in antwort.text and "= 360" in antwort.text, antwort.text[-300:])
    check("F: Die Grenze aus der Vorgabe wird geprüft",
          "Grenzprüfung" in antwort.text and "520 Euro" in antwort.text)
    check("F: Der Modelltext bleibt unverändert darüber",
          antwort.text.startswith("Tische: 3 × 120 = 380 Euro."))
    check("F: Laufbericht zählt die Befunde", agent.letzte_runde.befunde == 2,
          str(agent.letzte_runde.befunde))

    modell.warteschlange.append(
        "Tische: 3 × 120 = 360 Euro.\nDeko: 140 Euro.\nInsgesamt kommen wir auf 500 Euro.")
    antwort = agent.ask("Und wenn ich richtig rechne - was kostet das insgesamt?")
    check("F: Richtige Rechnung an der Grenze: kein Befund", "Prüfung" not in antwort.text,
          antwort.text[-200:])

    richtig = ["2,5 × 4 = 10", "1.200 + 300 = 1.500", "1,234.5 + 0.5 = 1,235",
               "3 × 28 = 84 + 2 = 86", "x2 + 3 = 5", "100 + 10 % = 110",
               "22 / 7 ≈ 3,14", "Pi ist etwa 22 / 7 = 3,14", "(3 + 4) × 2 = 14",
               "2^10 = 1024", "Datum: 12.10.2026 = Montag", "a == b + 1", "x := 5 + 3"]
    falsch_erkannt = [r for r in richtig if rechnungen_pruefen(r)]
    check("F: Keine Fehlalarme bei 13 korrekten/uneindeutigen Schreibweisen",
          not falsch_erkannt, str(falsch_erkannt))
    falsch = ["12 / 4 = 4", "7 · 8 = 54", "1.500 + 500 = 2.500", "3,5 × 2 = 6"]
    erkannt = [r for r in falsch if rechnungen_pruefen(r)]
    check("F: Alle 4 falschen Rechnungen erkannt", len(erkannt) == 4, str(erkannt))
    check("F: Untergrenze wird geprüft",
          bool(grenzen_pruefen("Insgesamt sind es 2 Stunden.", ["Mindestens 3 Stunden üben."])))
    check("F: Andere Einheit ist kein Verstoß",
          not grenzen_pruefen("Insgesamt 600 Minuten.", ["höchstens 500 Euro"]))
    MESSWERTE["F_rechenpruefung"] = (f"{len(erkannt)}/4 Fehler erkannt, "
                                     f"{len(falsch_erkannt)}/13 Fehlalarme")


# ================================================== G: Unbelegtes
def pruefung_g() -> None:
    print("\n--- G: Was die Notizen nicht hergeben ---")
    agent, modell = neuer_agent(episoden_datei("g"))
    modell.warteschlange.append("Die Seriennummer deines Laptops ist XK-4471.")
    antwort = agent.ask("Welche Seriennummer hat mein Laptop?")
    check("G: Vorher steht die Beleglage im Prompt",
          "BELEGLAGE" in modell.systemprompt and "Seriennummer" in modell.systemprompt)
    check("G: Eine erfundene Antwort bekommt den Herkunftshinweis",
          HERKUNFT_HINWEIS in antwort.text, antwort.text[-200:])

    modell.warteschlange.append("Dazu steht nichts in deinen Notizen – das weiß ich nicht.")
    antwort = agent.ask("Welche Seriennummer hat mein Drucker?")
    check("G: Eine ehrliche Antwort bleibt ohne Zusatz", "Prüfung" not in antwort.text)

    modell.warteschlange.append("Laut [[Laptop-Inventar]] ist es XK-4471.")
    antwort = agent.ask("Welche Seriennummer hat mein Tablet?")
    check("G: Erfundene Quellenangabe wird benannt",
          "Quellenprüfung" in antwort.text and "Laptop-Inventar" in antwort.text)

    modell.warteschlange.append("Die Schwelle steht laut [[Bremsregler]] auf 3.")
    antwort = agent.ask("Wo steht die Schwelle des Bremsreglers?")
    check("G: Eine echte Quelle wird nicht beanstandet", "Prüfung" not in antwort.text,
          antwort.text[-200:])

    check("G: Kein Befund benutzt das Wort 'nicht' (Messstand prüft darauf)",
          "nicht" not in HERKUNFT_HINWEIS.lower()
          and "nicht" not in rechnungen_pruefen("2 + 2 = 5")[0].text.lower())


# ================================================== H: Relevanz
def pruefung_h() -> None:
    print("\n--- H: Relevantes kommt, Unbeteiligtes nicht ---")
    agent, modell = neuer_agent(episoden_datei("h"))
    agent.ask("Für das Projekt Falke nutzen wir die Bibliothek polars statt pandas.")
    agent.ask("Das Projekt Garten braucht im Frühjahr neue Erde.")
    for frage in FUELLER:
        agent.ask(frage)
    agent.ask("Welche Bibliothek nutzen wir für Falke?")
    arbeit = agent.letztes_arbeitsgedaechtnis
    erinnert = [t.zug.frage for t in arbeit.erinnerungen]
    check("H: Die relevante Stelle wird erinnert",
          any("polars" in f for f in erinnert), str(erinnert))
    check("H: Keine der unbeteiligten Fragen", not [f for f in erinnert if f in FUELLER],
          str(erinnert))
    check("H: Ein gemeinsames Allerweltswort ('Projekt') reicht nicht",
          not any("Garten" in f for f in erinnert), str(erinnert))
    agent.ask("Wie spät ist es gerade in Tokio?")
    check("H: Zu einer neuen Frage wird nichts erinnert",
          not agent.letztes_arbeitsgedaechtnis.erinnerungen)


# ================================================== I: Gedächtnis gestört
def pruefung_i() -> None:
    print("\n--- I: Gestörtes Gedächtnis ---")
    kaputt = episoden_datei("i_ordner")
    kaputt.mkdir(parents=True, exist_ok=True)           # Datei ist ein Ordner
    agent, modell = neuer_agent(kaputt)
    try:
        antwort = agent.ask("Welche IP hatte der Server Odin nochmal?")
        ok = True
    except Exception as exc:                             # noqa: BLE001
        antwort, ok = None, False
        print("   ", type(exc).__name__, exc)
    check("I: Unlesbares Gedächtnis hält die Frage nicht auf", ok and bool(antwort.text))
    check("I: Der Speicher weiß, dass er gestört ist", agent.episoden.gestoert)
    check("I: Der Prompt sagt es dem Modell",
          "HINWEIS ZUM GEDÄCHTNIS" in modell.systemprompt)
    check("I: Und behauptet keine Erinnerung",
          "FRÜHERE GESPRÄCHSSTELLEN" not in modell.systemprompt)
    check("I: Die Antwort sagt es dem Nutzer", "*Gedächtnis:" in (antwort.text if antwort else ""))
    warnungen = [e["text"] for e in agent.tools.log.lesen(50, ebene="warnung")]
    check("I: Und das Protokoll", any("Arbeitsgedächtnis" in w for w in warnungen),
          str(warnungen[-2:]))

    defekt = episoden_datei("i_defekt")
    gut = {"v": 1, "typ": "zug", "nr": 1, "zeit": "2026-10-01T10:00:00",
           "sitzung": "alt", "frage": "Der Router heißt Hugin und steht im Keller.",
           "antwort": "Notiert."}
    defekt.write_text(json.dumps(gut, ensure_ascii=False) + "\n{kaputt\n"
                      + json.dumps({"v": 1, "typ": "zug", "nr": "zwei"}) + "\n",
                      encoding="utf-8")
    speicher = Episodengedaechtnis(cfg, pfad=defekt)
    check("I: Defekte Zeilen kosten nur sich selbst",
          len(speicher.zuege) == 1 and speicher.defekte == 2,
          f"{len(speicher.zuege)} Züge, {speicher.defekte} defekt")
    check("I: ... und gelten nicht als Totalausfall", not speicher.gestoert)
    check("I: Der heile Teil bleibt abrufbar",
          bool(speicher.abrufen("Wo steht der Router Hugin?")))
    check("I: Die Kennzahlen nennen die defekten Zeilen",
          speicher.kennzahlen()["defekte"] == 2)

    agent, modell = neuer_agent(episoden_datei("i_wirft"))
    agent.ask("Der Server Odin hat die IP 10.0.0.42.")

    def wirft(*a, **k):
        raise RuntimeError("Index weg")

    agent.episoden.abrufen = wirft
    antwort = agent.ask("Welche IP hatte Odin nochmal?")
    arbeit = agent.letztes_arbeitsgedaechtnis
    check("I: Ein werfender Abruf wird Fehler, nicht Absturz",
          any("RuntimeError" in f for f in arbeit.fehler) and bool(antwort.text),
          str(arbeit.fehler))
    check("I: ... ohne vorgetäuschte Erinnerung", not arbeit.erinnerungen)

    schreibgeschuetzt = episoden_datei("i_nurlesen")
    schreibgeschuetzt.write_text("", encoding="utf-8")
    os.chmod(schreibgeschuetzt, stat.S_IREAD)
    try:
        agent, modell = neuer_agent(schreibgeschuetzt)
        agent.ask("Der Drucker heißt Munin und steht im Flur.")
        check("I: Schreibfehler wird bemerkt", agent.episoden.geschrieben is False
              and agent.episoden.gestoert)
        for frage in FUELLER:
            agent.ask(frage)
        agent.ask("Wo steht der Drucker Munin nochmal?")
        check("I: Im Arbeitsspeicher geht es trotzdem weiter",
              "Munin" in modell.systemprompt.split("FRÜHERE", 1)[-1])
    finally:
        os.chmod(schreibgeschuetzt, stat.S_IREAD | stat.S_IWRITE)

    vorher = agent.cfg.episoden_gedaechtnis
    agent.cfg.episoden_gedaechtnis = False
    try:
        aus_datei = episoden_datei("i_aus")
        agent, modell = neuer_agent(aus_datei)
        agent.ask("Wichtig: Antworte immer auf Englisch.")
        agent.ask("Was ist ein Gradient?")
        check("I: Abgeschaltet wird nichts abgelegt und nichts eingesetzt",
              not aus_datei.exists() and "ARBEITSGEDÄCHTNIS" not in modell.systemprompt)
    finally:
        agent.cfg.episoden_gedaechtnis = vorher


# ================================================== Datenschutz, Nebenläufigkeit
def pruefung_rand() -> None:
    print("\n--- Datenschutz und Nebenläufigkeit ---")
    check("Geheimnis: Passwort wird geschwärzt",
          "hunter2geheim" not in schwaerzen("Mein Passwort ist hunter2geheim, merk es dir."))
    check("Geheimnis: API-Schlüssel wird geschwärzt",
          "sk-" not in schwaerzen("Nimm sk-abcdefghijklmnopqrstuvwx für den Test."))
    datei = episoden_datei("geheim")
    speicher = Episodengedaechtnis(cfg, pfad=datei)
    speicher.vermerken("Das Passwort: hunter2geheim bitte nicht vergessen.", "Gut.")
    check("Geheimnis: Steht so nicht in der Datei",
          "hunter2geheim" not in datei.read_text(encoding="utf-8"))

    check("Vorgabe: 'Ich muss gleich los' ist keine", not vorgaben_finden("Ich muss gleich los."))
    check("Vorgabe: Frage ist keine", not vorgaben_finden("Muss das immer so sein?"))
    check("Vorgabe: 'Antworte immer auf Englisch' ist eine",
          bool(vorgaben_finden("Antworte immer auf Englisch.")))

    datei = episoden_datei("parallel")
    speicher = Episodengedaechtnis(cfg, pfad=datei)

    def schreiben(kennung: str) -> None:
        for i in range(50):
            speicher.vermerken(f"Frage {kennung} Nummer {i}", "Antwort")

    faeden = [threading.Thread(target=schreiben, args=(k,)) for k in "ab"]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    nummern = [z["nr"] for z in zeilen]
    check("Nebenläufig: 100 Züge, jede Nummer einmal, jede Zeile lesbar",
          len(zeilen) == 100 and len(set(nummern)) == 100)

    import types
    klein = types.SimpleNamespace(episoden_gedaechtnis=True, episoden_behalten=50,
                                  work_dir=TMP)
    datei = episoden_datei("verdichten")
    speicher = Episodengedaechtnis(klein, pfad=datei)
    erster = speicher.vermerken("Unsere Datenbank ist PostgreSQL 16.", "Gut.")
    for i in range(78):
        speicher.vermerken(f"Füllfrage Nummer {i} über Gartenpflege", "Antwort")
    letzter = speicher.vermerken("Unsere Datenbank ist jetzt MySQL 8.", "Gut.")
    speicher.ersetzung_vermerken(erster, "Unsere Datenbank ist PostgreSQL 16.", letzter)
    zeilen = datei.read_text(encoding="utf-8").splitlines()
    neu_geladen = Episodengedaechtnis(klein, pfad=datei)
    check("Verdichten: Datei bleibt bei EPISODEN_BEHALTEN (+25 % Spielraum)",
          len(zeilen) <= 63 and len(neu_geladen.zuege) == 50,
          f"{len(zeilen)} Zeilen, {len(neu_geladen.zuege)} Züge")
    check("Verdichten: Die Nummern laufen weiter, die jüngsten bleiben",
          neu_geladen.zuege[-1].nr == letzter.nr and "MySQL" in neu_geladen.zuege[-1].frage)


# ================================================== Messung
def messung() -> None:
    print("\n--- Messung: Abrufgenauigkeit und Latenz ---")
    import random
    zufall = random.Random(7)
    datei = episoden_datei("messung")
    speicher = Episodengedaechtnis(cfg, pfad=datei)
    namen = ["Aldebaran", "Beteigeuze", "Capella", "Deneb", "Elnath", "Fomalhaut", "Gienah",
             "Hadar", "Izar", "Jabbah", "Kochab", "Lesath", "Mirach", "Nunki", "Okul",
             "Phact", "Rigel", "Sabik", "Thuban", "Unuk", "Vega", "Wezen", "Xamidimura",
             "Yildun", "Zaurak", "Alcor", "Botein", "Castor", "Diphda", "Enif"]
    raeume = ["Keller", "Dachboden", "Büro", "Werkstatt", "Garage", "Flur"]
    ablenkung = FUELLER + ["Wie backe ich ein Brot?", "Was kostet ein Zugticket nach Wien?",
                           "Welche Serie lohnt sich gerade?", "Wie repariere ich einen Platten?"]
    soll = {}
    for name in namen:
        raum = zufall.choice(raeume)
        soll[name] = raum
        speicher.vermerken(f"Das Gerät {name} steht im Raum {raum} und hat die Nummer "
                           f"{zufall.randint(100, 999)}.", "Notiert.")
        for _ in range(2):
            speicher.vermerken(zufall.choice(ablenkung), "Dazu weiß ich Folgendes …")
    treffer1 = treffer3 = 0
    for name in namen:
        ergebnis = speicher.abrufen(f"Wo steht das Gerät {name}?", anzahl=3)
        texte = [t.zug.frage for t in ergebnis]
        treffer1 += bool(texte) and name in texte[0]
        treffer3 += any(name in t for t in texte)
    MESSWERTE["Abruf_Treffer@1"] = f"{treffer1}/{len(namen)}"
    MESSWERTE["Abruf_Treffer@3"] = f"{treffer3}/{len(namen)}"
    check("Messung: Treffer@3 mindestens 90 %", treffer3 >= 0.9 * len(namen),
          f"{treffer3}/{len(namen)}")
    leer = sum(bool(speicher.abrufen(f)) for f in [
        "Wie spät ist es in Tokio?", "Was ist ein Integral?", "Schreib ein Gedicht über Herbst."])
    MESSWERTE["Abruf_Fehlgriffe_bei_neuen_Fragen"] = f"{leer}/3"
    check("Messung: Neue Fragen ziehen nichts heran", leer == 0, f"{leer}/3")

    gross = Episodengedaechtnis(cfg, pfad=episoden_datei("gross"))
    for i in range(2000):
        gross.vermerken(f"{zufall.choice(ablenkung)} Variante {i} mit Stichwort "
                        f"{zufall.choice(namen)}", "Antwort " * 20)
    beginn = time.perf_counter()
    for name in namen[:20]:
        gross.abrufen(f"Was weißt du über {name}?", anzahl=4)
    dauer = (time.perf_counter() - beginn) / 20 * 1000
    MESSWERTE["Abruf_ms_bei_2000_Zuegen"] = round(dauer, 1)
    check("Messung: Abruf über 2000 Züge unter 250 ms", dauer < 250, f"{dauer:.1f} ms")
    MESSWERTE["Dateigroesse_2000_Zuege_kB"] = round(gross.pfad.stat().st_size / 1024)

    # Was kostet das ganze Arbeitsgedächtnis je Frage (ohne Modell)?
    agent, _ = neuer_agent(gross.pfad)
    agent.history = [{"role": "user", "content": "Hallo"},
                     {"role": "assistant", "content": "Hallo."}]
    beginn = time.perf_counter()
    for name in namen[:10]:
        agent._arbeitsgedaechtnis_bauen(f"Was steht in der Datei zu {name}? "
                                        f"Das Gerät {name} ist jetzt im Raum Keller.")
    dauer = (time.perf_counter() - beginn) / 10 * 1000
    MESSWERTE["Arbeitsgedaechtnis_ms_bei_2000_Zuegen"] = round(dauer, 1)
    check("Messung: Arbeitsgedächtnis über 2000 Züge unter 500 ms", dauer < 500,
          f"{dauer:.1f} ms")


def main() -> int:
    notiz("Bremsregler", "Die Schwelle steht auf 3. Umgesetzt in regler.py.")
    for gruppe in (pruefung_a, pruefung_b, pruefung_c, pruefung_d, pruefung_e,
                   pruefung_f, pruefung_g, pruefung_h, pruefung_i, pruefung_rand, messung):
        try:
            gruppe()
        except Exception as exc:                         # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{gruppe.__name__} lief ohne Ausnahme durch", False,
                  f"{type(exc).__name__}: {exc}")

    print("\nMesswerte:")
    for name, wert in MESSWERTE.items():
        print(f"   {name}: {wert}")
    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
