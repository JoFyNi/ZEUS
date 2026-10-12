"""Tests für die grafische Oberfläche: Server, Graphdaten, Sternbilder, .env.

Die Oberfläche ist der erste Teil des Systems, der von außen erreichbar ist -
auch wenn "außen" nur der eigene Rechner ist. Deshalb prüft diese Reihe
ausdrücklich mit:

* dass ohne Schlüssel nichts geht (der Server darf über die Werkzeuge in die
  Vault schreiben),
* dass ein fehlerhafter Einstellungswert GAR NICHTS speichert statt die Hälfte,
* dass gesperrte Werte auch dann nicht durchgehen, wenn sie mitgeschickt werden.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_ui_"))
VAULT = TMP / "Zeus"
ENV = TMP / ".env"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import config as konf  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.schau import baum_daten, graph_daten, stufe  # noqa: E402
from jarvis.tools import TOOL_NAMES, TOOL_SCHEMAS  # noqa: E402
from jarvis.web import einstellungen as einst  # noqa: E402
from jarvis.web.server import starten  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Wissen").mkdir(parents=True)
    (VAULT / "Agenten").mkdir()
    (VAULT / "Wissen" / "Neuronale_Netze.md").write_text(
        "---\ntags: [dl]\n---\n\n# Neuronale Netze\n\n"
        "Siehe [[Backpropagation]] und [[Aktivierungsfunktionen]].\n", encoding="utf-8")
    (VAULT / "Wissen" / "Backpropagation.md").write_text(
        "# Backpropagation\n\nGehört zu [[Neuronale Netze]].\n", encoding="utf-8")
    (VAULT / "Wissen" / "Aktivierungsfunktionen.md").write_text(
        "# Aktivierungsfunktionen\n\nReLU und Sigmoid. [[Gibt_Es_Nicht]]\n", encoding="utf-8")
    (VAULT / "Agenten" / "Allein.md").write_text(
        "# Allein\n\nNiemand verlinkt mich.\n", encoding="utf-8")


# ================================================== 1. Graphdaten
def test_graph(tools) -> None:
    d = graph_daten(tools._notes())
    check("Alle Notizen werden zu Knoten", len(d["knoten"]) == 4,
          f"{len(d['knoten'])} Knoten")

    namen = {k["name"]: k for k in d["knoten"]}
    check("Ordner wird zur Gruppe",
          namen["Neuronale_Netze"]["gruppe"] == "Wissen",
          namen["Neuronale_Netze"]["gruppe"])

    # Neuronale Netze ↔ Backpropagation und ↔ Aktivierungsfunktionen.
    check("Beidseitiger Link zählt nur einmal", len(d["kanten"]) == 2,
          f"{d['kanten']}")
    check("Link ins Leere wird keine Kante",
          all(k["von"] != k["nach"] for k in d["kanten"])
          and len(d["kanten"]) == 2,
          "Gibt_Es_Nicht darf keinen Knoten erzeugen")
    check("Grad wird gezählt", namen["Neuronale_Netze"]["grad"] == 2,
          str(namen["Neuronale_Netze"]["grad"]))
    check("Verwaiste Notiz wird gezählt", d["verwaist"] == 1, str(d["verwaist"]))
    check("Verwaiste Notiz bleibt im Bild",
          namen["Allein"]["grad"] == 0 and "Allein" in namen)


# ================================================ 2. Fertigkeitsbaum
class _Fertigkeit:
    def __init__(self, name):
        self.name = name
        self.beschreibung = "Test"
        self.parameter = ["thema"]
        self.schritte = ["notizen_suchen(frage=\"{thema}\")"]


def test_baum() -> None:
    check("Stufe 0 bei nie benutzt", stufe(0) == 0)
    check("Stufe 1 beim ersten Aufruf", stufe(1) == 1)
    check("Stufe 5 erst bei 100 Aufrufen", stufe(100) == 5 and stufe(99) == 4,
          f"{stufe(99)}/{stufe(100)}")

    d = baum_daten(TOOL_SCHEMAS, [_Fertigkeit("meine_uebersicht")],
                   testordner=Path(__file__).resolve().parent)
    zugeordnet = {s["name"] for b in d["sternbilder"] for s in b["sterne"]}
    check("Jedes Werkzeug bekommt einen Stern",
          TOOL_NAMES <= zugeordnet,
          f"fehlt: {sorted(TOOL_NAMES - zugeordnet)}")
    check("Kein Sternbild bleibt leer",
          all(b["sterne"] for b in d["sternbilder"]))
    check("Eigene Fertigkeit bekommt ein eigenes Sternbild",
          any(b["schluessel"] == "eigene" for b in d["sternbilder"]))
    eigen = [s for b in d["sternbilder"] for s in b["sterne"] if s["eigen"]]
    check("Eigene Fertigkeit ist als solche markiert",
          len(eigen) == 1 and eigen[0]["name"] == "meine_uebersicht")
    check("Ohne Nutzung ist nichts freigeschaltet", d["freigeschaltet"] == 0,
          str(d["freigeschaltet"]))

    # Jetzt Nutzung mitschreiben und noch einmal messen.
    from jarvis import nutzung
    for _ in range(4):
        nutzung.vermerken("notiz_lesen", True)
    nutzung.vermerken("notiz_lesen", False)
    nutzung.vermerken("notiz_loeschen", False)
    nutzung.vermerken("notiz_loeschen", False)
    nutzung.vermerken("notiz_loeschen", False)
    d2 = baum_daten(TOOL_SCHEMAS, testordner=Path(__file__).resolve().parent)
    alle = {s["name"]: s for b in d2["sternbilder"] for s in b["sterne"]}
    check("Aufrufe erhöhen die Stufe", alle["notiz_lesen"]["stufe"] == 2,
          f"Stufe {alle['notiz_lesen']['stufe']} bei 5 Aufrufen")
    check("Fehlerquote wird berechnet",
          abs(alle["notiz_lesen"]["fehlerquote"] - 0.2) < 0.001,
          str(alle["notiz_lesen"]["fehlerquote"]))
    check("Dauerhaft scheiterndes Werkzeug wird gemeldet",
          "notiz_loeschen" in d2["unzuverlaessig"], str(d2["unzuverlaessig"]))
    check("Nie benutzte Werkzeuge werden aufgelistet",
          "notiz_lesen" not in d2["nie_benutzt"] and len(d2["nie_benutzt"]) > 5)


# ============================================ 3. Einstellungen prüfen
def test_einstellungen() -> None:
    check("Zahl wird geprüft", einst.pruefen("NUM_PREDICT", "abc") is not None)
    check("Gültige Zahl geht durch", einst.pruefen("NUM_PREDICT", "4096") is None)
    check("Auswahl wird geprüft", einst.pruefen("DENKEN", "vielleicht") is not None)
    check("Gültige Auswahl geht durch", einst.pruefen("DENKEN", "aus") is None)
    check("Schalter wird geprüft", einst.pruefen("DRY_RUN", "manchmal") is not None)
    check("Unbekannter Schlüssel wird abgelehnt",
          einst.pruefen("RM_RF", "1") is not None)
    check("Gesperrter Schlüssel wird abgelehnt",
          einst.pruefen("CHUNK_SIZE", "500") is not None,
          "CHUNK_SIZE darf die Oberfläche nicht ändern")

    gruppen = einst.als_liste(konf.settings)
    schluessel = {f["schluessel"] for g in gruppen for f in g["felder"]}
    check("Alle Gruppen haben Felder", all(g["felder"] for g in gruppen))
    check("Kein gesperrter Wert wird angeboten",
          not (schluessel & einst.GESPERRT), str(schluessel & einst.GESPERRT))
    check("Jedes Feld hat eine bekannte Art",
          all(f["art"] in einst.ARTEN for g in gruppen for f in g["felder"]))
    check("Werte werden vorbelegt",
          any(f["wert"] for g in gruppen for f in g["felder"]))


def test_speichern() -> None:
    ENV.write_text("NUM_PREDICT=2048\n", encoding="utf-8")
    konf.ENV_FILE = ENV

    ergebnis = einst.speichern({"NUM_PREDICT": "3072", "DENKEN": "aus"}, konf.settings)
    check("Gültige Werte werden gespeichert", ergebnis["ok"], str(ergebnis))
    text = ENV.read_text(encoding="utf-8")
    check("Bestehende Zeile wird ersetzt, nicht verdoppelt",
          text.count("NUM_PREDICT") == 1 and "NUM_PREDICT=3072" in text, text)
    check("Neue Zeile wird angehängt", "DENKEN=aus" in text, text)

    vorher = ENV.read_text(encoding="utf-8")
    schlecht = einst.speichern({"NUM_CTX": "8192", "DENKEN": "unsinn"}, konf.settings)
    check("Ein fehlerhafter Wert verhindert ALLE Schreibvorgänge",
          not schlecht["ok"] and not schlecht["gespeichert"], str(schlecht))
    check("Die Datei bleibt dabei unverändert",
          ENV.read_text(encoding="utf-8") == vorher,
          "NUM_CTX wurde trotz Fehler geschrieben!")

    neustart = einst.speichern({"NUM_CTX": "4096"}, konf.settings)
    check("Neustart-Hinweis wird gemeldet", neustart.get("neustart_noetig") is True)
    sofort = einst.speichern({"NUM_PREDICT": "2048"}, konf.settings)
    check("Sofort wirksame Werte melden keinen Neustart",
          sofort.get("neustart_noetig") is False)


# ================================================= 4. Server über HTTP
def _get(adresse: str, pfad: str, schluessel: str = ""):
    anfrage = urllib.request.Request(adresse.rstrip("/") + pfad)
    if schluessel:
        anfrage.add_header("X-Zeus-Schluessel", schluessel)
    with urllib.request.urlopen(anfrage, timeout=10) as antwort:
        return antwort.status, json.loads(antwort.read().decode("utf-8"))


def _post(adresse: str, pfad: str, koerper: dict, schluessel: str = ""):
    anfrage = urllib.request.Request(
        adresse.rstrip("/") + pfad,
        data=json.dumps(koerper).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    if schluessel:
        anfrage.add_header("X-Zeus-Schluessel", schluessel)
    with urllib.request.urlopen(anfrage, timeout=20) as antwort:
        return antwort.status, json.loads(antwort.read().decode("utf-8"))


def test_server(agent) -> None:
    schluessel = "pruefschluessel"
    server, adresse, sitzung = starten(agent=agent, port=0, schluessel=schluessel)
    try:
        check("Server hört nur auf 127.0.0.1", adresse.startswith("http://127.0.0.1:"),
              adresse)

        # Ohne Schlüssel: 403.
        try:
            _get(adresse, "/api/status")
            check("Ohne Schlüssel kein Zugang", False, "Anfrage ging durch!")
        except urllib.error.HTTPError as exc:
            check("Ohne Schlüssel kein Zugang", exc.code == 403, f"HTTP {exc.code}")

        code, status = _get(adresse, "/api/status", schluessel)
        check("Status antwortet", code == 200 and "modell" in status, str(status)[:120])

        code, g = _get(adresse, "/api/graph", schluessel)
        check("Graph über HTTP", code == 200 and len(g["knoten"]) == 4,
              f"{len(g.get('knoten', []))} Knoten")

        code, b = _get(adresse, "/api/baum", schluessel)
        check("Baum über HTTP", code == 200 and b["gesamt"] >= len(TOOL_NAMES),
              f"{b.get('gesamt')} Sterne")

        code, n = _get(adresse, "/api/notiz?name=Backpropagation", schluessel)
        check("Notiz über HTTP", code == 200 and n["ok"] and "Backprop" in n["text"])

        code, fehlt = _get(adresse, "/api/notiz?name=Gibt_Es_Nicht_Wirklich", schluessel)
        check("Fehlende Notiz meldet sauber statt zu stürzen",
              code == 200 and not fehlt["ok"], str(fehlt)[:120])

        # Die Seite selbst braucht keinen Schlüssel - aber er muss eingesetzt sein.
        with urllib.request.urlopen(adresse, timeout=10) as antwort:
            seite = antwort.read().decode("utf-8")
        check("Oberfläche wird ausgeliefert", "<title>ZEUS</title>" in seite)
        check("Schlüssel wird in die Seite eingesetzt",
              schluessel in seite and "__ZEUS_SCHLUESSEL__" not in seite)

        code, b = _get(adresse, "/api/befehle", schluessel)
        check("Befehlsliste über HTTP", code == 200 and len(b["befehle"]) > 30,
              f"{len(b.get('befehle', []))} Befehle")
        code, v = _get(adresse, "/api/vorschlaege?art=werkzeug", schluessel)
        check("Vorschläge über HTTP", code == 200 and len(v["eintraege"]) > 30,
              f"{len(v.get('eintraege', []))} Einträge")
        test_vorschlaege(sitzung)

        code, ant = _post(adresse, "/api/frage", {"frage": "Was ist ein Netz?"}, schluessel)
        check("Frage wird angenommen", code == 200 and "id" in ant, str(ant)[:120])

        # Der Strom muss auch dann enden, wenn nichts Sinnvolles zurückkommt.
        anfrage = urllib.request.Request(
            f"{adresse}api/strom?id={ant['id']}&s={schluessel}")
        arten = []
        with urllib.request.urlopen(anfrage, timeout=25) as strom:
            for rohzeile in strom:
                zeile = rohzeile.decode("utf-8").strip()
                if not zeile.startswith("data:"):
                    continue
                ereignis = json.loads(zeile[5:])
                arten.append(ereignis["art"])
                if ereignis["art"] == "ende":
                    break
        check("Strom beginnt und endet", arten[0] == "beginn" and arten[-1] == "ende",
              str(arten))
        check("Strom liefert eine fertige Antwort", "fertig" in arten, str(arten))

        # DER eigentliche Fehler: "/help" ging als Frage ans Modell.
        code, bef = _post(adresse, "/api/frage", {"frage": "/hilfe"}, schluessel)
        arten, texte = [], []
        with urllib.request.urlopen(urllib.request.Request(
                f"{adresse}api/strom?id={bef['id']}&s={schluessel}"), timeout=25) as strom:
            for rohzeile in strom:
                zeile = rohzeile.decode("utf-8").strip()
                if not zeile.startswith("data:"):
                    continue
                ereignis = json.loads(zeile[5:])
                arten.append(ereignis["art"])
                if ereignis["art"] == "fertig":
                    texte.append(ereignis.get("text", ""))
                    check("Befehl wird als Befehl gekennzeichnet",
                          ereignis.get("art_der_antwort") == "befehl",
                          str(ereignis.get("art_der_antwort")))
                if ereignis["art"] == "ende":
                    break
        check("/hilfe geht NICHT ans Modell", "token" not in arten, str(arten))
        check("/hilfe liefert die Befehlsliste",
              texte and "/werkzeuge" in texte[0], (texte[0][:90] if texte else ""))

        try:
            _post(adresse, "/api/frage", {"frage": "   "}, schluessel)
            check("Leere Frage wird abgelehnt", False, "ging durch")
        except urllib.error.HTTPError as exc:
            check("Leere Frage wird abgelehnt", exc.code == 400, f"HTTP {exc.code}")
    finally:
        server.shutdown()
        server.server_close()


# ============================ 5. Befehle: Katalog, Brücke, Vorschläge
def test_katalog() -> None:
    import ast

    from jarvis.befehle import BEFEHLE, als_liste, finden, hilfetabelle, nach_name

    check("Katalog ist nicht leer", len(BEFEHLE) > 30, f"{len(BEFEHLE)} Befehle")
    check("Kurzname findet den Befehl",
          finden("/tools") is not None and finden("/tools").name == "werkzeuge")
    check("Fragezeichen ist die Hilfe", finden("?").name == "hilfe")
    check("Jeder Befehl hat eine bekannte Argumentart",
          all(b.argument in ("frei", "keins", "fertigkeit", "notiz", "ordner",
                             "datei", "codedatei", "stimme", "sprache",
                             "an_aus", "zahl", "wahl") for b in BEFEHLE))
    check("Wahl-Befehle haben auch eine Auswahl",
          all(b.wahl for b in BEFEHLE if b.argument == "wahl"),
          str([b.name for b in BEFEHLE if b.argument == "wahl" and not b.wahl]))
    check("Kein Name doppelt vergeben",
          len(nach_name()) == sum(len(b.alle_namen) for b in BEFEHLE),
          "ein Name zeigt auf zwei Befehle")

    tabelle = hilfetabelle()
    check("Hilfe nennt jeden Befehl",
          all(f"`/{b.name}" in tabelle for b in BEFEHLE),
          str([b.name for b in BEFEHLE if f"`/{b.name}" not in tabelle]))
    check("Hilfe erklärt die Magic-Eingabe",
          "`[` beim Tippen" in tabelle and "`/` am Zeilenanfang" in tabelle)

    # Der eigentliche Zweck: Katalog und Terminal dürfen nicht auseinanderlaufen.
    quelle = (Path(__file__).resolve().parents[1] / "jarvis" / "ui.py") \
        .read_text(encoding="utf-8")
    umgesetzt = set()
    for knoten in ast.walk(ast.parse(quelle)):
        if not (isinstance(knoten, ast.FunctionDef) and knoten.name == "_command"):
            continue
        for k in ast.walk(knoten):
            if isinstance(k, ast.Compare) and isinstance(k.left, ast.Name) \
                    and k.left.id == "cmd":
                for c in k.comparators:
                    if isinstance(c, ast.Constant):
                        umgesetzt.add(c.value)
                    elif isinstance(c, (ast.Tuple, ast.List)):
                        umgesetzt.update(e.value for e in c.elts
                                         if isinstance(e, ast.Constant))
    katalog = set(nach_name())
    check("Kein Terminalbefehl fehlt im Katalog",
          not (umgesetzt - katalog), str(sorted(umgesetzt - katalog)))
    check("Kein Katalogbefehl fehlt im Terminal",
          not (katalog - umgesetzt), str(sorted(katalog - umgesetzt)))

    check("als_liste liefert reine Daten",
          all(isinstance(e, dict) and "argumenthilfe" in e for e in als_liste()))


def test_bruecke(agent) -> None:
    from jarvis.web.bruecke import Befehlsbruecke

    bruecke = Befehlsbruecke(agent)
    for befehl in ("/hilfe", "/werkzeuge", "/status", "/baum", "/fertigkeiten"):
        ergebnis = bruecke.ausfuehren(befehl)
        check(f"{befehl} liefert Text", ergebnis["ok"] and len(ergebnis["text"]) > 20,
              f"{len(ergebnis.get('text',''))} Zeichen")

    nur_terminal = bruecke.ausfuehren("/cls")
    check("Nur-Terminal-Befehl erklärt sich, statt still zu scheitern",
          nur_terminal["ok"] and nur_terminal.get("nur_terminal"),
          nur_terminal["text"][:80])

    unbekannt = bruecke.ausfuehren("/gibtsnicht")
    check("Unbekannter Befehl wird beantwortet, nicht geworfen",
          "Unbekannter Befehl" in unbekannt["text"], unbekannt["text"][:80])

    check("Ohne Schrägstrich passiert nichts",
          not bruecke.ausfuehren("hallo")["ok"])
    check("Brücke kennt dieselben Namen wie der Katalog",
          "werkzeuge" in bruecke.namen() and "tempo" in bruecke.namen())


def test_vorschlaege(sitzung) -> None:
    werkzeuge = sitzung.vorschlaege("werkzeug")["eintraege"]
    check("Werkzeugvorschläge enthalten jedes Werkzeug",
          {e["name"] for e in werkzeuge} >= TOOL_NAMES,
          str(sorted(TOOL_NAMES - {e["name"] for e in werkzeuge})))
    check("Vorschlag nennt Art und Parameter",
          all("hinweis" in e and "parameter" in e for e in werkzeuge))
    check("Schreibende Werkzeuge sind als solche markiert",
          any(e["hinweis"] == "schreibt" for e in werkzeuge)
          and any(e["hinweis"] == "liest" for e in werkzeuge))

    notizen = sitzung.vorschlaege("notiz")["eintraege"]
    check("Notizvorschläge kommen aus der Vault",
          {e["name"] for e in notizen} == {"Neuronale_Netze", "Backpropagation",
                                           "Aktivierungsfunktionen", "Allein"},
          str(sorted(e["name"] for e in notizen)))
    ordner = sitzung.vorschlaege("ordner")["eintraege"]
    check("Ordnervorschläge kommen aus der Struktur",
          {"Wissen", "Agenten"} <= {e["name"] for e in ordner},
          str([e["name"] for e in ordner]))
    check("Unbekannte Art liefert eine leere Liste, keinen Fehler",
          sitzung.vorschlaege("quatsch")["eintraege"] == [])


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Ein Netz aus Schichten.").__get__(agent.llm)
    agent.llm.chat_message = (lambda self, m, tools=None, **k: {
        "role": "assistant", "content": "Ein Netz aus Schichten."}).__get__(agent.llm)
    agent.sync()

    test_graph(agent.tools)
    test_baum()
    test_einstellungen()
    test_speichern()
    test_katalog()
    test_bruecke(agent)
    test_server(agent)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
