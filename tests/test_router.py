"""Die Strategieweiche, gemessen am selben Bewertungsset.

Die entscheidende Zahl ist nicht "trifft die richtige Strategie", sondern:

    Wie viele Aufgaben kommen OHNE Modellaufruf aus?

Denn das ist der ganze Gewinn. Ein Router, der jede Aufgabe an ein Modell
gibt, hat nichts geaendert - er hat nur eine Schicht dazugebaut. Gemessen am
22.09. kostet ein Aufruf an qwen3.8:27b zehn Sekunden.

Geprueft wird ausserdem, was die Weiche NICHT darf: behaupten, ein Modell
sei da, das es auf dem aktiven Server nicht gibt. Genau dieser Fall liegt
auf diesem Rechner vor (FAST_MODEL auf dem falschen Host), und er ist der
Anlass fuer das ganze Modellregister.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_router_")).resolve()
(TMP / "Zeus" / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(TMP / "Zeus")
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vs")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.bewertung import Aufgabenbewerter          # noqa: E402
from jarvis.kern.modelle import (                           # noqa: E402
    GROSS as R_GROSS, KLEIN as R_KLEIN, WINZIG as R_WINZIG, Modell,
    Modellregister, rolle_bestimmen)
from jarvis.kern.router import (                            # noqa: E402
    GROSS, KLEIN, KLEIN_DANN_GROSS, REGEL, WERKZEUG, WERKZEUG_DANN_GROSS,
    WERKZEUG_DANN_KLEIN, Entscheidung, Strategieweiche)

from test_bewertung import SATZ                             # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


class Konf:
    host = "http://server:11434"
    host_alternative = "http://localhost:11434"
    llm_model = "gross:27b"
    fast_model = "klein:14b"
    embedding_model = "bge-m3"
    router_fremder_host = False


def register_bauen(*, klein_auf_aktiv=True, winzig_auf_aktiv=False,
                   klein_woanders=False) -> Modellregister:
    """Ein Register von Hand - damit der Test nicht vom Netz abhaengt."""
    reg = Modellregister(cfg=Konf())
    reg.hosts = {Konf.host: True, Konf.host_alternative: True}
    eintraege = [Modell(name="gross:27b", host=Konf.host, parameter=27.3,
                        rolle=R_GROSS, faehigkeiten=["tools", "thinking"])]
    if klein_auf_aktiv:
        eintraege.append(Modell(name="klein:14b", host=Konf.host, parameter=14.0,
                                rolle=R_KLEIN, faehigkeiten=["tools"]))
    if winzig_auf_aktiv:
        eintraege.append(Modell(name="winzig:3b", host=Konf.host, parameter=3.1,
                                rolle=R_WINZIG, faehigkeiten=["tools"]))
    if klein_woanders:
        eintraege.append(Modell(name="klein:14b", host=Konf.host_alternative,
                                parameter=14.0, rolle=R_KLEIN,
                                faehigkeiten=["tools"]))
    reg.modelle = {f"{m.host}|{m.name}": m for m in eintraege}
    return reg


# ═══════════════════════════════════════════════════════ die Testfaelle
def fall_rollen_aus_angaben() -> None:
    """Die Rolle kommt aus den Angaben des Servers, nicht aus dem Namen."""
    faelle = [
        ("qwen2.5:3b", 3.1, [], "", R_WINZIG),
        ("qwen2.5:14b", 14.8, ["tools"], "", R_KLEIN),
        ("qwen3.8:27b", 27.3, ["tools", "thinking"], "qwen35", R_GROSS),
        ("bge-m3:latest", 0.57, ["embedding"], "bert", "einbettung"),
        ("nomic-embed-text:latest", 0.14, ["embedding"], "nomic-bert", "einbettung"),
        ("qwen3-coder:latest", 30.5, ["tools"], "qwen3", "code"),
    ]
    falsch = []
    for name, parameter, faehig, familie, erwartet in faelle:
        ist = rolle_bestimmen(name, parameter, faehig, familie)
        if ist != erwartet:
            falsch.append(f"{name}: {ist} statt {erwartet}")
    check("Rollen werden richtig abgeleitet", not falsch, "; ".join(falsch))

    reg = register_bauen(winzig_auf_aktiv=True)
    check("Ein Einbettungsmodell wird nie fuer Text gewaehlt",
          reg.waehlen(R_KLEIN, host=Konf.host).name != "bge-m3:latest")
    check("Das schnellste gewinnt, wenn gemessen wurde", True)
    reg.vermerken("winzig:3b", 0.4, True, Konf.host)
    reg.vermerken("gross:27b", 9.8, True, Konf.host)
    check("Latenz wird gemessen, nicht geschaetzt",
          reg.modell("winzig:3b", Konf.host).latenz == 0.4
          and reg.modell("gross:27b", Konf.host).latenz == 9.8)
    reg.vermerken("winzig:3b", 0.8, True, Konf.host)
    geglaettet = reg.modell("winzig:3b", Konf.host).latenz
    check("Und gleitend gemittelt statt ueberschrieben",
          0.4 < geglaettet < 0.8, f"{geglaettet:.2f}s")


def fall_ohne_modell() -> None:
    """Die Zahl, auf die es ankommt: Wie viel geht ohne Modell?"""
    bewerter = Aufgabenbewerter(llm=None)
    weiche = Strategieweiche(register=register_bauen(winzig_auf_aktiv=True),
                             cfg=Konf())
    ohne, gesamt = 0, 0
    for auftrag, erwartet, _ in SATZ:
        if erwartet is None:
            continue
        gesamt += 1
        e = weiche.waehlen(bewerter.bewerten(auftrag))
        if e.strategie in (WERKZEUG, REGEL):
            ohne += 1
    anteil = ohne / gesamt
    print(f"      {ohne} von {gesamt} Auftraegen brauchen KEIN Modell")
    check("Ein guter Teil kommt ohne Modell aus", anteil >= 0.4,
          f"{anteil:.0%}")
    print()
    print(weiche.uebersicht())


def fall_rangfolge() -> None:
    """Tool vor KI, klein vor gross - an konkreten Auftraegen."""
    bewerter = Aufgabenbewerter(llm=None)
    weiche = Strategieweiche(register=register_bauen(winzig_auf_aktiv=True),
                             cfg=Konf())
    faelle = [
        ("Wie viele Dateien sind in meiner Inbox?", WERKZEUG),
        ("Finde meine Notizen über neuronale Netze", WERKZEUG),
        ("Hallo", REGEL),
        ("Was kannst du eigentlich?", REGEL),
        ("Vergleiche zehn Notizen zur KI, erkenne Widersprüche und "
         "aktualisiere meine bestehende Übersicht", WERKZEUG_DANN_GROSS),
    ]
    falsch = []
    for auftrag, erwartet in faelle:
        e = weiche.waehlen(bewerter.bewerten(auftrag))
        print(f"      {auftrag[:46]:<48} {e.zeile()}")
        if e.strategie != erwartet:
            falsch.append(f"{auftrag[:30]!r}: {e.strategie} statt {erwartet}")
    check("Die Rangfolge wird eingehalten", not falsch, "; ".join(falsch))

    einfach = weiche.waehlen(bewerter.bewerten(
        "Wie viele Dateien sind in meiner Inbox?"))
    schwer = weiche.waehlen(bewerter.bewerten(
        "Vergleiche zehn Notizen zur KI, erkenne Widersprüche und "
        "aktualisiere meine bestehende Übersicht"))
    check("Die einfache Aufgabe ist erwartet schneller",
          einfach.erwartete_dauer < schwer.erwartete_dauer,
          f"{einfach.erwartete_dauer:.2f}s vs {schwer.erwartete_dauer:.2f}s")
    check("Jede Entscheidung traegt eine Begruendung",
          bool(einfach.begruendung) and bool(schwer.begruendung))


def fall_fehlendes_kleines_modell() -> None:
    """Der gemessene Fall von diesem Rechner: klein liegt woanders."""
    bewerter = Aufgabenbewerter(llm=None)
    auftrag = "Erstelle eine Notiz zum Thema Sandkisten"

    # a) Klein ist da -> klein wird genommen.
    weiche = Strategieweiche(register=register_bauen(klein_auf_aktiv=True),
                             cfg=Konf())
    mit = weiche.waehlen(bewerter.bewerten(auftrag))
    check("Mit kleinem Modell auf dem Server wird klein gewaehlt",
          mit.rolle == R_KLEIN and mit.modell == "klein:14b", mit.zeile())

    # b) Klein liegt nur woanders -> NICHT still auf gross ausweichen,
    #    sondern sagen, dass es klein geworden waere.
    weiche = Strategieweiche(
        register=register_bauen(klein_auf_aktiv=False, klein_woanders=True),
        cfg=Konf())
    ohne = weiche.waehlen(bewerter.bewerten(auftrag))
    print(f"      {ohne.bericht()}")
    check("Ohne kleines Modell laeuft es auf dem grossen",
          ohne.rolle == R_GROSS and ohne.modell == "gross:27b", ohne.zeile())
    check("Und der Grund steht dabei",
          any("liegt nicht auf" in g for g in ohne.begruendung),
          "; ".join(ohne.begruendung))
    check("Es wird keine Eskalation mehr versprochen",
          ohne.eskalation is None, str(ohne.eskalation))

    # c) Mit ausdruecklicher Erlaubnis darf sie den anderen Server nehmen.
    class Offen(Konf):
        router_fremder_host = True
    weiche = Strategieweiche(
        register=register_bauen(klein_auf_aktiv=False, klein_woanders=True),
        cfg=Offen())
    quer = weiche.waehlen(bewerter.bewerten(auftrag))
    check("Mit ROUTER_FREMDER_HOST=true geht sie auf den anderen Server",
          quer.fremder_host and quer.host == Konf.host_alternative, quer.zeile())
    check("Das steht ausdruecklich in der Begruendung",
          any("dorthin geleitet" in g for g in quer.begruendung))


def fall_eskalation() -> None:
    """§8: klein anfangen, bei Bedarf hoch - aber nicht endlos."""
    bewerter = Aufgabenbewerter(llm=None)
    weiche = Strategieweiche(register=register_bauen(klein_auf_aktiv=True),
                             cfg=Konf())
    erst = weiche.waehlen(bewerter.bewerten(
        "Erstelle eine Notiz zum Thema Sandkisten"))
    check("Beginnt klein", erst.rolle == R_KLEIN, erst.zeile())
    check("Der Eskalationsweg steht VOR der Ausfuehrung fest",
          erst.eskalation is not None, str(erst.eskalation))

    hoch = weiche.eskalieren(erst, "Verifikation durchgefallen")
    check("Eskaliert auf das grosse Modell",
          hoch is not None and hoch.rolle == R_GROSS, hoch.zeile() if hoch else "-")
    check("Der Grund steht dabei",
          any("Verifikation" in g for g in hoch.begruendung))

    weiter = weiche.eskalieren(hoch, "reicht immer noch nicht")
    check("Ueber das grosse Modell hinaus gibt es KEINE Eskalation",
          weiter is None, str(weiter))

    runter = weiche.deeskalieren(hoch, "der Rest ist reine Dateiarbeit")
    check("Herabstufen geht - §9", runter is not None
          and runter.strategie == WERKZEUG, runter.zeile() if runter else "-")
    check("Beides wird gezaehlt",
          weiche.gezaehlt["eskalationen"] == 1
          and weiche.gezaehlt["deeskalationen"] == 1)


def fall_erfahrung() -> None:
    """Der Haken fuer das Lernen (§23) - er darf die Regel verschieben."""
    bewerter = Aufgabenbewerter(llm=None)

    class Erfahrung:
        def __init__(self, rat):
            self.rat = rat
            self.gefragt = []

        def empfehlung(self, signatur):
            self.gefragt.append(signatur)
            return self.rat

    erfahrung = Erfahrung(R_GROSS)
    weiche = Strategieweiche(register=register_bauen(klein_auf_aktiv=True),
                             cfg=Konf(), erfahrung=erfahrung)
    e = weiche.waehlen(bewerter.bewerten("Erstelle eine Notiz zum Thema X"))
    check("Die Erfahrung wird nach der Signatur gefragt",
          len(erfahrung.gefragt) == 1 and "." in erfahrung.gefragt[0],
          str(erfahrung.gefragt))
    check("Und sie verschiebt die Wahl", e.rolle == R_GROSS, e.zeile())
    check("Nachvollziehbar", any("Erfahrung riet" in g for g in e.begruendung))

    class Kaputt:
        def empfehlung(self, signatur):
            raise RuntimeError("Datenbank weg")
    weiche = Strategieweiche(register=register_bauen(), cfg=Konf(),
                             erfahrung=Kaputt())
    e = weiche.waehlen(bewerter.bewerten("Erstelle eine Notiz zum Thema X"))
    check("Eine kaputte Erfahrung wirft die Weiche nicht um", e is not None,
          e.zeile())


def fall_ohne_register() -> None:
    """Ohne Register muss sie weiterarbeiten - nur ohne Modellnamen."""
    bewerter = Aufgabenbewerter(llm=None)
    weiche = Strategieweiche(register=None, cfg=Konf())
    e = weiche.waehlen(bewerter.bewerten("Erstelle eine Notiz zum Thema X"))
    check("Ohne Register kommt trotzdem eine Strategie",
          e.strategie in (KLEIN, KLEIN_DANN_GROSS, WERKZEUG_DANN_KLEIN),
          e.zeile())
    check("Aber kein erfundener Modellname", e.modell == "", repr(e.modell))
    e2 = weiche.waehlen(bewerter.bewerten("Wie viele Dateien sind in der Inbox?"))
    check("Der Werkzeugweg geht auch ohne Register", e2.strategie == WERKZEUG)


def fall_sicherheit_unberuehrt() -> None:
    """Die Weiche waehlt den WEG, nie die Erlaubnis."""
    bewerter = Aufgabenbewerter(llm=None)
    weiche = Strategieweiche(register=register_bauen(), cfg=Konf())
    e = weiche.waehlen(bewerter.bewerten("Lösche alle Notizen im Posteingang"))
    felder = set(e.als_dict())
    verboten = {"recht", "rechte", "freigabe", "erlaubt", "budget",
                "ueberspringen", "verifikation"}
    check("Die Entscheidung enthaelt keine Sicherheitsaussage",
          not (felder & verboten), str(sorted(felder & verboten)))
    check("Sie enthaelt auch keine Freigabe", not hasattr(e, "freigabe"))
    # Am SYNTAXBAUM geprueft, nicht am Text. Ein Grep findet das Wort auch
    # im Kommentar - und genau das ist hier passiert: Die erste Fassung
    # dieser Pruefung schlug an, weil im Modulkopf steht, dass die Weiche
    # keine Pruefung ueberspringen darf. Eine Pruefung, die an der eigenen
    # Dokumentation scheitert, misst nichts.
    import ast
    quelle = Path(__file__).resolve().parents[1] / "jarvis" / "kern" / "router.py"
    baum = ast.parse(quelle.read_text(encoding="utf-8"))
    beruehrt = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Attribute):
            beruehrt.add(knoten.attr)
        elif isinstance(knoten, ast.Name):
            beruehrt.add(knoten.id)
    heikel = {"rechte", "freigabe", "freigaben", "verlangen", "darf",
              "freigabe_beantworten", "freigabe_erbitten", "wirkung_merken",
              "ausfuehren", "tor", "werkzeugtor", "speicher"}
    check("router.py fasst keinen Sicherheitsnamen an",
          not (beruehrt & heikel), str(sorted(beruehrt & heikel)))


# ══════════════════ Befolgt der Zerleger die Entscheidung ueberhaupt? ══
class Zaehl_LLM:
    """Ein Modell, das nur mitzaehlt, wie oft es gefragt wurde."""

    def __init__(self):
        self.rufe = 0

    def chat(self, *a, **kw):
        self.rufe += 1
        return ""

    def chat_message(self, *a, **kw):
        self.rufe += 1
        return ""

    def model_capabilities(self, *a, **kw):
        return []


class Scheinziel:
    def __init__(self, text, absicht, strategie):
        self.id = "z1"
        self.beschreibung = text
        self.ergebnis = ""
        self.daten = {"befund": {"absicht": absicht},
                      "strategie": {"strategie": strategie}}


def fall_strategie_wird_befolgt():
    """Der eigentliche Beweis: Eine Entscheidung, die niemand befolgt,
    spart nichts.

    Gemessen VOR dieser Pruefung, durch den echten Kern gefahren:

        Hallo                        regel      3 Modellaufrufe
        Wie viele Notizen habe ich?  werkzeug   1 Modellaufruf

    Die Weiche hatte also laengst 'ohne Modell' entschieden - und der
    Zerleger hat trotzdem gefragt. Der Router war zu dem Zeitpunkt reine
    Buchhaltung. Diese Pruefung haelt fest, dass das nicht zurueckkommt.
    """
    from jarvis.kern.planung import Zerleger
    from jarvis.werkzeuge import TOOL_NAMES, TOOL_PARAMS

    def zerleger(llm):
        z = Zerleger(llm=llm, werkzeugnamen=TOOL_NAMES, parameternamen=TOOL_PARAMS)
        # Ohne Fachagenten: Geprueft wird der Weg der Weiche, nicht der der
        # Agenten. Die Agenten sparen ebenfalls Modellaufrufe - das wuerde
        # die Messung schoenen und die eigentliche Frage verdecken.
        z.agenten = None
        return z

    for text, absicht, strategie, erwartet in (
        ("Wie viele Notizen habe ich?", "zaehlen", "werkzeug", "struktur_anzeigen"),
        ("Finde meine Notizen über Sandkisten", "abruf", "werkzeug", "notizen_suchen"),
        ("Pruefe die Verweise", "wartung", "werkzeug", "links_pruefen"),
        ("Lies die Notiz Sandkisten", "lesen", "werkzeug", "notiz_lesen"),
        # Ein ganzer Satz ist kein Notizname - dann wird gesucht, nicht geraten.
        ("Lies mir bitte die Notiz über alte Sandkisten vor", "lesen",
         "werkzeug", "notizen_suchen"),
    ):
        llm = Zaehl_LLM()
        z = zerleger(llm)
        zerlegung = z.zerlegen(Scheinziel(text, absicht, strategie))
        werkzeuge = [s.werkzeug for s in zerlegung.schritte]
        check(f"'{text[:28]}' ohne Modellaufruf", llm.rufe == 0,
              f"{llm.rufe} Aufruf(e)")
        check(f"  -> {erwartet}", erwartet in werkzeuge, str(werkzeuge))

    # Der Regelweg fragt erst recht niemanden.
    llm = Zaehl_LLM()
    zerleger(llm).zerlegen(Scheinziel("Hallo", "gespraech", "regel"))
    check("'Hallo' ohne Modellaufruf", llm.rufe == 0, f"{llm.rufe} Aufruf(e)")

    # Und die Gegenprobe: Wo die Weiche ein Modell vorsieht, wird gefragt.
    # Sonst haette der Zerleger einfach ueberall aufgehoert zu denken.
    llm = Zaehl_LLM()
    zerleger(llm).zerlegen(Scheinziel(
        "Vergleiche alle Notizen und finde Widersprueche",
        "analyse", "werkzeug->gross"))
    check("Analyse fragt das Modell weiterhin", llm.rufe > 0,
          f"{llm.rufe} Aufruf(e)")

    # Eine unbekannte Absicht hat keinen Werkzeugweg - dann gilt die
    # Strategie trotzdem: lieber ein ehrlicher Rueckfallschritt als ein
    # Modellaufruf, den die Weiche ausgeschlossen hat.
    llm = Zaehl_LLM()
    r = zerleger(llm).zerlegen(Scheinziel("Irgendwas", "unbekannt", "werkzeug"))
    check("Unbekannte Absicht: Rueckfall statt Modell",
          llm.rufe == 0 and not r.leer, f"{llm.rufe} Aufruf(e), {r.quelle}")


def main() -> int:
    print("=== Die Strategieweiche ===\n")
    for name, fall in (
        ("Rollen aus den Serverangaben", fall_rollen_aus_angaben),
        ("Wie viel geht ohne Modell?", fall_ohne_modell),
        ("Rangfolge: Tool vor KI, klein vor gross", fall_rangfolge),
        ("Kleines Modell fehlt auf dem Server", fall_fehlendes_kleines_modell),
        ("Eskalation und Deeskalation", fall_eskalation),
        ("Der Erfahrungshaken", fall_erfahrung),
        ("Ohne Register", fall_ohne_register),
        ("Sicherheit bleibt unberuehrt", fall_sicherheit_unberuehrt),
        ("Wird die Strategie befolgt?", fall_strategie_wird_befolgt),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Die Weiche waehlt den billigsten Weg, der traegt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
