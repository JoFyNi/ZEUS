"""Tests für das Sitzungsgedächtnis (`jarvis/sitzung.py`).

Alle acht Fälle aus dem Auftrag:

  1. Die Notiz entsteht im richtigen Format; die Bewährungsmarke wird von
     `_ZAEHLER_RE` aus `learning.py` gelesen.
  2. Eine zweite Sitzung am selben Tag HÄNGT AN und überschreibt nichts.
  4. Leere oder unbrauchbare Eingaben legen KEINE Datei an.
  5. Ein `OSError` beim Schreiben hält nichts an, setzt aber `letzter_fehler`.
  8. Eine von Hand editierte Notiz (Zeile ohne Marke, Punkt abgehakt) wird
     korrekt gelesen und beim nächsten Schreiben nicht beschädigt.

  3. Widerspruch: beide Zeilen bleiben, nur die jüngere kommt in den Prompt.
  6. Bewährung: 3 Einsätze mit >= 60 % -> bestaetigt, 5 mit < 30 % -> verworfen.
  7. SESSION_MEMORY=false: kein Dateizugriff, kein Anteil am Systemprompt.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_sitzung_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.config import settings  # noqa: E402
from jarvis.learning import (BESTAETIGT, VERWORFEN, VORLAEUFIG,  # noqa: E402
                             Lektion, _ZAEHLER_RE)
from jarvis.sitzung import (ENTSCHEIDUNG, FAKT, OFFEN,  # noqa: E402
                            Sitzungsfakt, Sitzungsgedaechtnis, _widerspruchsfrei,
                            brauchbar)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


HEUTE = dt.date(2026, 9, 2)


def werk(**abweichung) -> Sitzungsgedaechtnis:
    for schluessel, wert in abweichung.items():
        setattr(settings, schluessel, wert)
    return Sitzungsgedaechtnis(cfg=settings)


def main() -> int:
    settings.session_memory = True
    settings.session_folder = "_Sitzungen"

    # ============================================ 1. Format und Bewährungsmarke
    gedaechtnis = werk()
    gedaechtnis.beginn = "14:32"
    pfad = gedaechtnis.schreiben(
        fakten=["Hauptmodell ist qwen3.8:27b, Rückfall qwen2.5:14b."],
        entscheidungen=["mem0 wird nicht eingebaut; das Sitzungsgedächtnis "
                        "wird selbst gebaut."],
        offen=["Prüfaufgaben mit 20 bis 30 echten Aufgaben füllen"],
        notizen=["Deep Learning/Backpropagation"],
        zeitpunkt=dt.datetime(2026, 9, 2, 15, 10))

    check("Die Tagesnotiz entsteht", pfad is not None and pfad.exists(), str(pfad))
    text = pfad.read_text(encoding="utf-8")
    check("Pfad ist <Vault>/_Sitzungen/JJJJ-MM-TT.md",
          pfad == VAULT / "_Sitzungen" / "2026-09-02.md", str(pfad))
    check("Frontmatter trägt beide Schlagworte",
          "- sitzung" in text and "- gedaechtnis" in text, text[:90].replace("\n", "|"))
    check("Frontmatter trägt das Datum", "datum: 2026-09-02" in text)
    check("Abschnittsüberschrift mit Zeitspanne", "## 14:32 – 15:10" in text,
          [z for z in text.splitlines() if z.startswith("## ")][:1])
    for ueberschrift in ("### Fakten", "### Entscheidungen", "### Offen",
                         "### Berührte Notizen"):
        check(f"Rubrik '{ueberschrift[4:]}' steht drin", ueberschrift in text)
    check("Der Zeitstempel steht VOR dem Text",
          "- [2026-09-02 15:10] Hauptmodell" in text,
          [z for z in text.splitlines() if "Hauptmodell" in z][:1])
    check("Offene Punkte nutzen Checkbox-Syntax",
          "- [ ] Prüfaufgaben" in text)
    check("Berührte Notizen sind echte Wikilinks",
          "- [[Deep Learning/Backpropagation]]" in text)

    faktzeile = next(z for z in text.splitlines() if "Hauptmodell" in z)
    treffer = _ZAEHLER_RE.search(faktzeile)
    check("_ZAEHLER_RE liest die Marke der Sitzungsnotiz", treffer is not None,
          faktzeile)
    check("Die Marke startet auf vorlaeufig / 0 / 0",
          bool(treffer) and treffer.groups() == (VORLAEUFIG, "0", "0"),
          str(treffer.groups() if treffer else None))
    check("Die Marke ist byte-identisch mit der einer Lektion",
          Lektion(text="x").als_zeile().split("<!--")[1]
          == faktzeile.split("<!--")[1],
          faktzeile.split("<!--")[-1])

    gelesen = gedaechtnis.lesen(HEUTE)
    check("Zurückgelesen: ein Abschnitt", len(gelesen.abschnitte) == 1,
          str(len(gelesen.abschnitte)))
    check("Zurückgelesen: je ein Fakt, eine Entscheidung, ein offener Punkt",
          [len(gelesen.abschnitte[0].fakten),
           len(gelesen.abschnitte[0].entscheidungen),
           len(gelesen.abschnitte[0].offen)] == [1, 1, 1],
          str([len(gelesen.abschnitte[0].fakten),
               len(gelesen.abschnitte[0].entscheidungen),
               len(gelesen.abschnitte[0].offen)]))
    check("Zeitstempel und Text sind getrennt geparst",
          gelesen.abschnitte[0].fakten[0].zeit == "2026-09-02 15:10"
          and gelesen.abschnitte[0].fakten[0].text.startswith("Hauptmodell"),
          gelesen.abschnitte[0].fakten[0].zeit)
    check("Die berührte Notiz ist erfasst",
          gelesen.notizen() == ["Deep Learning/Backpropagation"],
          str(gelesen.notizen()))

    # ================================================ 2. Zweite Sitzung hängt an
    vorher = text
    zweites = werk()
    zweites.beginn = "16:00"
    pfad2 = zweites.schreiben(
        fakten=["Der LAN-Server läuft unter 192.168.173.176 auf Port 11434."],
        zeitpunkt=dt.datetime(2026, 9, 2, 16, 40))
    nachher = pfad.read_text(encoding="utf-8")

    check("Zweite Sitzung schreibt in dieselbe Tagesdatei", pfad2 == pfad,
          str(pfad2))
    check("Der alte Inhalt steht unverändert am Anfang",
          nachher.startswith(vorher.rstrip("\n")),
          nachher[:60].replace("\n", "|"))
    check("Genau ein Frontmatter-Block", nachher.count("\n---") == 1,
          str(nachher.count("\n---")))
    check("Zwei Abschnitte", nachher.count("\n## ") == 2,
          str(nachher.count("\n## ")))
    check("Der zweite Abschnitt trägt seine eigene Zeitspanne",
          "## 16:00 – 16:40" in nachher)
    check("Der erste Fakt steht immer noch drin", "Hauptmodell" in nachher)

    gelesen = zweites.lesen(HEUTE)
    check("Beide Abschnitte werden gelesen", len(gelesen.abschnitte) == 2,
          str(len(gelesen.abschnitte)))
    check("Beide Fakten sind erreichbar", len(gelesen.alle()) == 4,
          str([e.text[:24] for e in gelesen.alle()]))

    # Dublette: derselbe Fakt ein zweites Mal wird nicht wieder geschrieben.
    drittes = werk()
    drittes.beginn = "17:00"
    ergebnis = drittes.schreiben(
        fakten=["Hauptmodell ist qwen3.8:27b, Rückfall qwen2.5:14b."],
        zeitpunkt=dt.datetime(2026, 9, 2, 17, 5))
    check("Eine Dublette erzeugt keinen neuen Abschnitt", ergebnis is None,
          str(ergebnis))
    check("Die Datei ist dadurch unverändert",
          pfad.read_text(encoding="utf-8") == nachher)

    # ============================================ 4. Nichts Brauchbares, nichts da
    leerer_tag = dt.datetime(2026, 9, 3, 10, 0)
    leer = werk()
    check("Zu kurze Angaben gelten als unbrauchbar", not brauchbar("qwen3"))
    check("Zu lange Angaben gelten als unbrauchbar", not brauchbar("x" * 301))
    check("Ein Satz gilt als brauchbar",
          brauchbar("Der Nutzer arbeitet unter Windows mit Obsidian."))
    check("Leere Listen legen keine Datei an",
          leer.schreiben(zeitpunkt=leerer_tag) is None)
    check("Unbrauchbare Angaben legen keine Datei an",
          leer.schreiben(fakten=["kurz", "  ", "x" * 400],
                         zeitpunkt=leerer_tag) is None)
    check("Für den 3.9. existiert keine Notiz",
          not (VAULT / "_Sitzungen" / "2026-09-03.md").exists())
    check("Und der Ordner enthält weiterhin nur die eine Tagesnotiz",
          len(list((VAULT / "_Sitzungen").glob("*.md"))) == 1,
          str(sorted(p.name for p in (VAULT / "_Sitzungen").glob("*.md"))))

    # ================================= 5. Schreibfehler hält den Agenten nicht an
    # Eine DATEI dort, wo der Ordner sein müsste - das blockiert auch als root,
    # anders als ein bloß nicht existierender Pfad.
    sperre = TMP / "gesperrt"
    (sperre / "Zeus").mkdir(parents=True)
    (sperre / "Zeus" / "_Sitzungen").write_text("keine Ordner hier", encoding="utf-8")
    alt_vault = settings.vault_path
    settings.vault_path = sperre / "Zeus"
    kaputt = werk()
    ergebnis = kaputt.schreiben(fakten=["Der Nutzer nennt sein System Zeus."],
                                zeitpunkt=dt.datetime(2026, 9, 2, 18, 0))
    settings.vault_path = alt_vault

    check("Ein Schreibfehler wirft keine Ausnahme", ergebnis is None)
    check("Der Grund landet in letzter_fehler",
          "nicht schreibbar" in kaputt.letzter_fehler, kaputt.letzter_fehler)
    check("Und der Fehler taucht in den Kennzahlen auf",
          bool(werk().kennzahlen()) and "letzter_fehler" in werk().kennzahlen())

    # ============================================== 8. Handarbeit übersteht alles
    handarbeit = VAULT / "_Sitzungen" / "2026-08-30.md"
    handarbeit.write_text(
        "---\ntags:\n  - sitzung\n  - gedaechtnis\ndatum: 2026-08-30\n---\n\n"
        "# Sitzung 2026-08-30\n\n"
        "## 09:00 – 09:40\n\n"
        "### Fakten\n"
        "- [2026-08-30 09:40] Der Vault liegt unter Documents/Zeus.  "
        "<!-- status: bestaetigt · verwendet: 4 · bewaehrt: 3 -->\n"
        "- Von Hand ergänzt: der Nutzer arbeitet unter Windows.\n\n"
        "### Offen\n"
        "- [x] Piper-Stimmen austauschen\n"
        "- [ ] Messstand um eine Variante erweitern\n\n"
        "### Berührte Notizen\n"
        "- [[Werkzeugschleife]]\n",
        encoding="utf-8")

    hand = werk()
    gelesen = hand.lesen(dt.date(2026, 8, 30))
    fakten = gelesen.abschnitte[0].fakten
    check("Die Zeile MIT Marke wird als bestaetigt gelesen",
          fakten[0].status == BESTAETIGT and fakten[0].verwendet == 4
          and fakten[0].bewaehrt == 3,
          f"{fakten[0].status}/{fakten[0].verwendet}/{fakten[0].bewaehrt}")
    check("Die Zeile OHNE Marke gilt als vorlaeufig",
          fakten[1].status == VORLAEUFIG and fakten[1].verwendet == 0,
          fakten[1].status)
    check("Ihr Text bleibt vollständig",
          fakten[1].text == "Von Hand ergänzt: der Nutzer arbeitet unter Windows.",
          fakten[1].text)
    check("Der abgehakte Punkt ist als erledigt erkannt",
          gelesen.abschnitte[0].offen[0].erledigt is True)
    check("Der offene Punkt ist als nicht erledigt erkannt",
          gelesen.abschnitte[0].offen[1].erledigt is False)
    check("Die Zählung nennt genau einen offenen Punkt",
          gelesen.zahlen()["offen"] == 1, str(gelesen.zahlen()))

    vorher = handarbeit.read_text(encoding="utf-8")
    hand.beginn = "11:00"
    hand.schreiben(fakten=["Der Agent soll Notizen niemals stillschweigend "
                           "überschreiben."],
                   zeitpunkt=dt.datetime(2026, 8, 30, 11, 20))
    nachher = handarbeit.read_text(encoding="utf-8")
    check("Die handgeschriebene Notiz wird nur ergänzt",
          nachher.startswith(vorher.rstrip("\n")))
    check("Die Zeile ohne Marke steht unverändert da",
          "- Von Hand ergänzt: der Nutzer arbeitet unter Windows." in nachher)
    check("Der abgehakte Punkt bleibt abgehakt",
          "- [x] Piper-Stimmen austauschen" in nachher)
    check("Der neue Abschnitt kam dazu", "## 11:00 – 11:20" in nachher)
    check("Und wird zusammen mit dem alten gelesen",
          len(hand.lesen(dt.date(2026, 8, 30)).abschnitte) == 2)

    # ==================== 2. Extraktion: EIN Modellaufruf, streng validiert
    ruf = {"anzahl": 0, "letzte": {}}

    def attrappe(antwort):
        def chat(nachrichten, **kw):
            ruf["anzahl"] += 1
            ruf["letzte"] = dict(kw)
            ruf["prompt"] = nachrichten[0]["content"]
            return antwort
        return type("LLM", (), {"chat": staticmethod(chat)})()

    class Agentenattrappe:
        def __init__(self, antwort):
            self.llm = attrappe(antwort)
            self.index = None

    gutes_json = ('{"fakten": ["Josh entwickelt unter Windows mit Obsidian als '
                  'zweitem Gehirn."], "entscheidungen": ["mem0 wurde geprüft und '
                  'verworfen; gebaut wird selbst."], "offen": ["Den Messstand um '
                  'eine Variante erweitern"], "notizen": ["Werkzeugschleife"]}')
    extrakt = Sitzungsgedaechtnis(agent=Agentenattrappe(gutes_json), cfg=settings)
    extrakt.beginn = "20:00"
    check("Ohne neue Züge wird nicht verdichtet", extrakt.verdichten() is None)
    check("Und es gab dafür keinen Modellaufruf", ruf["anzahl"] == 0)

    for i in range(3):
        extrakt.zug_vermerken(f"Frage {i}", f"Antwort {i}")
    check("Züge werden gezählt", extrakt.offene_zuege == 3)
    check("Noch nicht fällig (SESSION_SUMMARY_EVERY=10)", not extrakt.faellig())
    settings.session_summary_every = 3
    check("Ab der eingestellten Zahl ist es fällig", extrakt.faellig())
    settings.session_summary_every = 10

    pfad3 = extrakt.verdichten("Test")
    check("Genau EIN Modellaufruf je Verdichtung", ruf["anzahl"] == 1,
          str(ruf["anzahl"]))
    check("temperature=0.0", ruf["letzte"].get("temperature") == 0.0)
    check("format_json=True", ruf["letzte"].get("format_json") is True)
    check("schnell=True", ruf["letzte"].get("schnell") is True)
    check("Der Prompt verbietet Interpretation ausdrücklich",
          "Keine Interpretation" in ruf["prompt"])
    check("Der Prompt fordert das erwartete JSON",
          '"fakten"' in ruf["prompt"] and '"notizen"' in ruf["prompt"])
    check("Der Puffer ist danach leer", extrakt.offene_zuege == 0)
    check("Die Verdichtung hat geschrieben", pfad3 is not None and pfad3.exists())
    inhalt = pfad3.read_text(encoding="utf-8")
    check("Der Fakt steht in der Notiz", "Josh entwickelt unter Windows" in inhalt)
    check("Die Entscheidung auch", "mem0 wurde geprüft" in inhalt)
    check("Der offene Punkt als Kästchen",
          "- [ ] Den Messstand um eine Variante erweitern" in inhalt)
    check("Die Notiz als Wikilink", "- [[Werkzeugschleife]]" in inhalt)

    # Unbrauchbare Modellantwort: keine Datei, kein Absturz (Fall 4, mit LLM)
    for datei in (VAULT / "_Sitzungen").glob("2026-09-04.md"):
        datei.unlink()
    schrott = Sitzungsgedaechtnis(agent=Agentenattrappe("Ich habe leider keine Idee."),
                                  cfg=settings)
    schrott.zug_vermerken("Frage", "Antwort")
    check("Unbrauchbares JSON legt keine Datei an", schrott.verdichten() is None)
    check("Und sagt im Zustand, was war",
          "JSON" in schrott.letzter_fehler, schrott.letzter_fehler)

    leere = Sitzungsgedaechtnis(
        agent=Agentenattrappe('{"fakten": ["zu kurz"], "entscheidungen": [], '
                              '"offen": [], "notizen": []}'), cfg=settings)
    leere.zug_vermerken("Frage", "Antwort")
    check("Nur unbrauchbare Einträge legen keine Datei an",
          leere.verdichten() is None)

    class Kaputt:
        class llm:
            @staticmethod
            def chat(*a, **k):
                raise RuntimeError("Server tot")
        index = None

    tot = Sitzungsgedaechtnis(agent=Kaputt(), cfg=settings)
    tot.zug_vermerken("Frage", "Antwort")
    check("Ein toter Server wirft nicht durch", tot.verdichten() is None)
    check("Der Grund steht im Zustand",
          "fehlgeschlagen" in tot.letzter_fehler, tot.letzter_fehler)
    check("Und der Verlauf bleibt für den nächsten Versuch erhalten",
          tot.offene_zuege == 1, str(tot.offene_zuege))

    # ============ 3. Widerspruch: beide Zeilen bleiben, nur die jüngere zählt
    wider = werk()
    wider.beginn = "08:00"
    wider.schreiben(fakten=["Das Hauptmodell für den Agenten ist qwen3.8:27b."],
                    zeitpunkt=dt.datetime(2026, 9, 5, 9, 0))
    wider.beginn = "18:00"
    wider.schreiben(fakten=["Das Hauptmodell für den Agenten ist jetzt qwen3:32b."],
                    zeitpunkt=dt.datetime(2026, 9, 5, 18, 30))
    datei = VAULT / "_Sitzungen" / "2026-09-05.md"
    inhalt = datei.read_text(encoding="utf-8")
    check("Beide Zeilen stehen in der Datei",
          "qwen3.8:27b" in inhalt and "qwen3:32b" in inhalt)
    gewaehlt = _widerspruchsfrei(sorted(wider.lesen(dt.date(2026, 9, 5)).alle(),
                                        key=lambda f: f.sortierschluessel))
    check("Aber nur eine kommt in den Prompt", len(gewaehlt) == 1,
          str([g.text[:40] for g in gewaehlt]))
    check("Und zwar die jüngere", "qwen3:32b" in gewaehlt[0].text,
          gewaehlt[0].text)
    check("Verschiedene Gegenstände bleiben beide",
          len(_widerspruchsfrei([
              Sitzungsfakt(text="Das Hauptmodell ist qwen3:32b.", zeit="2026-09-05 18:30"),
              Sitzungsfakt(text="Die Einbettungen laufen mit bge-m3 über den LAN-Server.",
                           zeit="2026-09-05 18:30")])) == 2)

    # ======================================= 6. Bewährung mit Tiefpass
    bewaehr = werk()
    bewaehr.beginn = "07:00"
    bewaehr.schreiben(
        fakten=["Der Agent soll Ordnernamen niemals raten, sondern nachfragen.",
                "Die Sicherungen liegen im Ordner _Papierkorb der Vault."],
        zeitpunkt=dt.datetime(2026, 9, 6, 7, 30))
    tag6 = dt.date(2026, 9, 6)

    guter = [f for f in bewaehr.lesen(tag6).alle() if "raten" in f.text][0]
    for _ in range(3):                       # 3 Einsätze, alle geglückt
        treffer = [f for f in bewaehr.lesen(tag6).alle() if "raten" in f.text]
        bewaehr.fakt_verwendet(treffer)
        bewaehr.bewerten(True)
    guter = [f for f in bewaehr.lesen(tag6).alle() if "raten" in f.text][0]
    check("Nach 3 Einsätzen mit 100 % ist der Fakt bestätigt",
          guter.status == BESTAETIGT, f"{guter.status} {guter.bewaehrt}/{guter.verwendet}")

    for i in range(5):                       # 5 Einsätze, einer geglückt = 20 %
        treffer = [f for f in bewaehr.lesen(tag6).alle() if "Papierkorb" in f.text]
        bewaehr.fakt_verwendet(treffer)
        bewaehr.bewerten(i == 0)
    schlechter = [f for f in bewaehr.lesen(tag6).alle() if "Papierkorb" in f.text][0]
    check("Nach 5 Einsätzen mit 20 % ist der Fakt verworfen",
          schlechter.status == VERWORFEN,
          f"{schlechter.status} {schlechter.bewaehrt}/{schlechter.verwendet}")
    check("Der verworfene Fakt steht weiterhin in der Datei",
          "_Papierkorb" in (VAULT / "_Sitzungen" / "2026-09-06.md").read_text("utf-8"))
    check("Aber nicht mehr im Abruf",
          all("Papierkorb" not in f.text
              for f in bewaehr.passende_fakten("Wo liegen die Sicherungen?")))
    check("Die Zahlen der Datei stimmen",
          bewaehr.lesen(tag6).zahlen()[BESTAETIGT] == 1
          and bewaehr.lesen(tag6).zahlen()[VERWORFEN] == 1,
          str(bewaehr.lesen(tag6).zahlen()))
    check("Der Text der bewerteten Zeile ist unverändert",
          "Der Agent soll Ordnernamen niemals raten, sondern nachfragen."
          in (VAULT / "_Sitzungen" / "2026-09-06.md").read_text("utf-8"))

    # ------------------------------------------ Startauszug und Abruf
    auszug = werk().startauszug()
    check("Der Startauszug nennt die jüngste Sitzung",
          auszug.startswith("Letzte Sitzung (2026-09-06)"), auszug[:60])
    check("Er hält SESSION_RECALL_CHARS ein",
          len(werk(session_recall_chars=120).startauszug()) <= 140,
          str(len(werk(session_recall_chars=120).startauszug())))
    settings.session_recall_chars = 1500
    check("Der Abruf liefert höchstens SESSION_FACTS_IN_PROMPT Fakten",
          len(werk().passende_fakten("Womit arbeitet Josh?")) <= 5)

    # ================== 7. SESSION_MEMORY=false legt wirklich alles still
    aus = werk(session_memory=False)
    check("Abgeschaltet wird nicht geschrieben",
          aus.schreiben(fakten=["Ein völlig unverfänglicher, langer Satz hier."],
                        zeitpunkt=dt.datetime(2026, 9, 2, 19, 0)) is None)
    check("Abgeschaltet wird nichts gelesen", not aus.lesen(HEUTE).abschnitte)
    check("Abgeschaltet gibt es keine jüngste Notiz", aus.juengste() is None)
    check("Abgeschaltet kein Startauszug", aus.startauszug() == "")
    check("Abgeschaltet keine Fakten für den Prompt",
          aus.passende_fakten("Womit arbeitet Josh?") == [])
    check("Abgeschaltet werden keine Züge gemerkt",
          (aus.zug_vermerken("Frage", "Antwort") or aus.offene_zuege) == 0)
    check("Abgeschaltet wird nicht verdichtet", aus.verdichten() is None)
    check("Abgeschaltet melden die Kennzahlen nur den Schalter",
          aus.kennzahlen() == {"aktiv": False}, str(aus.kennzahlen()))

    # Und im Systemprompt taucht nichts davon auf.
    from jarvis.agent import JarvisAgent
    from jarvis.index import KnowledgeIndex
    settings.session_memory = False
    agent = JarvisAgent(cfg=settings,
                        index=KnowledgeIndex(settings, embeddings=stubs.StubEmbedding()))
    ohne = agent.system_prompt(frage="Womit arbeitet Josh?")
    settings.session_memory = True
    agent.sitzung.cfg = settings
    mit = agent.system_prompt(frage="Womit arbeitet Josh?")
    check("Ohne Schalter steht keine Anknüpfung im Systemprompt",
          "ANKNÜPFUNG" not in ohne and "FRÜHEREN SITZUNGEN" not in ohne)
    check("Mit Schalter schon", "FRÜHEREN SITZUNGEN" in mit or "ANKNÜPFUNG" in mit,
          mit[-200:].replace("\n", "|"))

    # ----------------------------------- Das gemeinsame Budget greift
    from jarvis.agent import GEDAECHTNIS_ANTEIL
    budget = int(settings.prompt_char_budget * GEDAECHTNIS_ANTEIL)
    agent.letzter_kontext = {}
    agent.system_prompt(frage="Womit arbeitet Josh?")
    verbraucht = agent.letzter_kontext.get("gedaechtnis_zeichen", 0)
    check("Das Gedächtnis bleibt im gemeinsamen Budget",
          verbraucht <= budget, f"{verbraucht} von {budget}")
    check("Ausgelassenes wird gezählt, nicht verschwiegen",
          isinstance(agent.letzter_kontext.get("gedaechtnis_ausgelassen"), dict),
          str(agent.letzter_kontext.get("gedaechtnis_ausgelassen")))

    settings.session_memory = True

    # ------------------------------------------ Kleinkram am Datenmodell
    fakt = Sitzungsfakt(text="Ein Satz.", zeit="2026-09-02 15:10", art=FAKT)
    check("Ein Fakt rendert mit Marke", fakt.als_zeile().endswith("bewaehrt: 0 -->"))
    check("Ein offener Punkt rendert ohne Marke",
          Sitzungsfakt(text="Etwas tun", art=OFFEN).als_zeile()
          == "- [ ] Etwas tun")
    check("Entscheidungen rendern wie Fakten",
          "<!--" in Sitzungsfakt(text="Festgelegt.", zeit="2026-09-02 15:10",
                                 art=ENTSCHEIDUNG).als_zeile())
    check("Die jüngste Notiz ist die zuletzt geschriebene",
          (werk().juengste() or object()).tag == "2026-09-06",
          str(getattr(werk().juengste(), "tag", None)))
    zahlen = werk().kennzahlen()
    check("Kennzahlen zählen alle Tagesnotizen", zahlen.get("tage") == 4, str(zahlen))
    check("Kennzahlen nennen den Ordner", zahlen.get("ordner") == "_Sitzungen")

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
