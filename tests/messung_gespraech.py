"""Messgeschirr fuer den Gespraechspfad: Welchen Weg nimmt eine Eingabe - und
endet die Runde mit einer Antwort?

    python tests/messung_gespraech.py              gegen das sture Skriptmodell
    python tests/messung_gespraech.py --ollama     gegen den echten Server
    python tests/messung_gespraech.py --json X     Datensaetze zusaetzlich als JSON

Gemessen wird am LAUFENDEN Agenten (`JarvisAgent.ask`), nicht an einer
Attrappe. Ersetzt wird nur das Sprachmodell - und zwar durch eines, das sich
so verhaelt wie im Protokoll vom Anlass (claude/PROMPT_Antwortpflicht_und_
Triage.md, §1): Es ruft Werkzeuge, solange es welche angeboten bekommt, und
liest dieselbe Notiz mehrfach. Das ist der UNGUENSTIGE Fall, und genau der
muss halten: Eine Antwortpflicht, die nur bei einem braven Modell greift, ist
keine.

Ohne Werkzeugdefinitionen antwortet das Skriptmodell in Prosa und fasst
zusammen, was in seinem Kontext an Werkzeugergebnissen steht. Damit misst
das Geschirr die Leitung (kommt das Geholte in der Schlussrunde an?), nicht
die Schreibkunst eines Modells.

Die Zaehler sind duenne Huellen von aussen - um `index.search`, um
`_run_tool` (VOR dem Tor, abgewiesene Aufrufe getrennt), um die Modellaufrufe
und um `_schlussbericht`. Damit laeuft dasselbe Geschirr am unveraenderten
Code (Stufe 0) und an jeder spaeteren Stufe. Was erst spaeter entsteht (Weg,
Triage-Quelle, Rundenende), liest es aus `agent.letzte_runde`, wenn es das
gibt.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_messung_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

ECHT = "--ollama" in sys.argv

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.gespraech import werkzeuglauf  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402


# =============================================================== Testsatz
# (eingabe, gruppe, erwarteter Weg, Sonderfall)
# Die Reihenfolge ist Absicht: "und?" steht direkt hinter einer Vault-Antwort.
TESTSATZ = [
    ("Hallo", "meta", "direkt", ""),
    ("Was kannst du eigentlich?", "meta", "direkt", ""),
    ("wa steht an?", "vault", "vault", ""),
    ("und?", "rueckbezug", "direkt", ""),
    ("reflektiere deine Aktionen", "meta", "direkt", ""),
    ("warum hast du mir nicht geantwortet?", "meta", "direkt", ""),
    ("Erledigt ist nicht die Antwort auf meine Frage", "meta", "direkt", ""),
    ("wie sollte der Anfangsprompt aussehen?", "meta", "direkt", ""),
    ("Finde meine Notizen über Sandkisten", "vault", "vault", ""),
    ("Wie viele Notizen habe ich?", "werkzeug", "werkzeug", ""),
    ("Erstelle eine Notiz zum Thema Kettenregel", "schreiben", "schreiben", ""),
    ("Leg mir eine Notiz über Gradientenabstieg an", "schreiben", "schreiben", ""),
    ("Was sind die Systemvoraussetzungen von Allplan 2026?", "recherche",
     "recherche", ""),
    ("Was sind die Systemvoraussetzungen von Allplan 2026?", "recherche",
     "recherche", "kein_netz"),
    ("Hallo, was steht an?", "gemischt", "vault", ""),
    ("reflektiere deine Aktionen und notiere das", "kombiniert",
     "direkt+schreiben", ""),
    # "fasse ... zusammen" waere ein Schreibauftrag (looks_like_write_request)
    # - der Budgetfall braucht eine reine Lesefrage.
    ("Gib mir einen Überblick über alle offenen Punkte in meinen Notizen",
     "budget", "vault", ""),
]

META_EINGABEN = {e for e, g, _, _ in TESTSATZ if g == "meta"}


# =============================================================== die Vault
def vault_bauen() -> None:
    (VAULT / "00_Inbox").mkdir(parents=True)
    (VAULT / "01_Wissen").mkdir(parents=True)
    (VAULT / "01_Wissen" / "Aufgabenliste_Autonomie_NN_Gehirn.md").write_text(
        "# Aufgabenliste Autonomie\n\n## Offen\n"
        "- [ ] Triage an den Gespraechspfad anschliessen\n"
        "- [ ] Schlussrunde ohne Werkzeuge einbauen\n"
        "- [x] Modellregister pruefen\n", encoding="utf-8")
    (VAULT / "01_Wissen" / "Aufgabenliste_&_Offene_Punkte.md").write_text(
        "# Offene Punkte\n\n- [ ] Sandkisten-Konzept dokumentieren\n"
        "- [ ] Wochenbericht schreiben\n", encoding="utf-8")
    (VAULT / "01_Wissen" / "Sandkisten.md").write_text(
        "# Sandkisten\n\nEine Sandkiste isoliert Code vom Rest des Systems.\n",
        encoding="utf-8")
    (VAULT / "01_Wissen" / "Kettenregel.md").write_text(
        "# Kettenregel\n\nAbleitung verketteter Funktionen.\n", encoding="utf-8")
    (VAULT / "00_Inbox" / "Einkauf.md").write_text(
        "# Einkauf\n\n- Milch\n", encoding="utf-8")


# ============================================================ Skriptmodell
def _vault_skript(frage: str) -> List[tuple]:
    """Was das sture Modell der Reihe nach ruft - nach dem Protokoll vom Anlass.

    Schritt 5 und 7 sind dieselbe Notiz wie Schritt 1, einmal buchstaben-
    gleich, einmal in anderer Schreibweise. Nur die zweite rutscht an einer
    Sperre vorbei, die auf den rohen JSON-Text schaut.
    """
    return [
        ("notizen_suchen", {"frage": frage}),
        ("notiz_lesen", {"name": "Aufgabenliste_Autonomie_NN_Gehirn"}),
        ("notizen_suchen", {"frage": f"offene Aufgaben To-do {frage}"}),
        ("notiz_lesen", {"name": "Aufgabenliste_&_Offene_Punkte"}),
        ("notiz_lesen", {"name": "Aufgabenliste_Autonomie_NN_Gehirn"}),
        ("notizen_suchen", {"frage": f"offene Aufgaben To-do {frage} bitte"}),
        ("notiz_lesen", {"name": " aufgabenliste_autonomie_nn_gehirn "}),
        ("notiz_lesen", {"name": "Sandkisten"}),
        ("notiz_lesen", {"name": "Aufgabenliste_&_Offene_Punkte"}),
        ("notiz_lesen", {"name": "Kettenregel"}),
        ("notizen_suchen", {"frage": frage}),
        ("notiz_lesen", {"name": "Einkauf"}),
    ]


def _netz_skript(frage: str) -> List[tuple]:
    return [
        ("web_suchen", {"frage": frage}),
        ("web_lesen", {"url": "https://example.org/allplan"}),
        ("web_suchen", {"frage": f"{frage} Hersteller"}),
        ("web_suchen", {"frage": frage}),
    ] * 3


def _werkzeugergebnisse(messages) -> List[str]:
    raus = []
    for m in messages:
        inhalt = str(m.get("content") or "")
        if m.get("role") == "tool" or inhalt.startswith("WERKZEUG-ERGEBNIS"):
            # Abweisungen und Fehlschlaege sind kein "Geholtes".
            if '"ok": false' not in inhalt:
                raus.append(inhalt)
    return raus


class SturesModell:
    """Ruft Werkzeuge, solange es welche bekommt. Ohne Werkzeuge: Prosa."""

    def __init__(self, messung: "Messung") -> None:
        self.messung = messung
        self.frage = ""
        self.schritt = 0

    def neue_runde(self, frage: str) -> None:
        self.frage, self.schritt = frage, 0

    def _prosa(self, messages) -> str:
        ergebnisse = _werkzeugergebnisse(messages)
        if ergebnisse:
            offen = []
            for text in ergebnisse:
                offen += re.findall(r"- \[ \] ([^\n\\\"]+)", text)
            if offen:
                punkte = list(dict.fromkeys(p.strip() for p in offen))[:6]
                return ("Offen sind: " + "; ".join(punkte)
                        + ". Quellen stehen in den gelesenen Notizen.")
            return "Aus dem Geholten: " + ergebnisse[-1][:160].replace("\n", " ")
        return f"Zur Frage „{self.frage}“: Antwort aus dem Gespraech."

    def chat_message(self, messages, tools=None, **kw) -> dict:
        self.messung.modellaufruf()
        namen = {t.get("function", {}).get("name") for t in (tools or [])}
        if not namen:
            return {"role": "assistant", "content": self._prosa(messages)}
        skript = (_vault_skript(self.frage) if "notizen_suchen" in namen or
                  "notiz_lesen" in namen else _netz_skript(self.frage))
        passend = [s for s in skript if s[0] in namen]
        if not passend:
            # Andere Werkzeuge (zaehlen, Struktur): das erste angebotene.
            name = sorted(namen)[0]
            if "struktur_anzeigen" in namen:
                name = "struktur_anzeigen"
            passend = [(name, {})]
        name, args = passend[self.schritt % len(passend)]
        self.schritt += 1
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": name, "arguments": dict(args)}}]}

    def chat(self, messages, stream_cb=None, format_json=False, **kw) -> str:
        self.messung.modellaufruf()
        if format_json:
            return self._plan(messages)
        return self._prosa(messages)

    def _plan(self, messages) -> str:
        """Ein gueltiger Schreibplan, wenn der Planer fragt - sonst leeres JSON.

        Ohne ihn fiele jeder Schreibauftrag vom Planer in die Werkzeugschleife,
        und das Geschirr maesse einen Weg, den es im Betrieb nicht gibt.
        """
        prompt = str((messages[-1] if messages else {}).get("content") or "")
        if "notiz_erstellen" not in prompt and "operation" not in prompt.lower():
            return "{}"
        thema = re.search(r"(?:Thema|über|ueber)\s+(\w+)", self.frage)
        titel = thema.group(1) if thema else "Reflexion Gespraech"
        return json.dumps({"absicht": "Notiz anlegen", "operationen": [
            {"aktion": "notiz_erstellen",
             "parameter": {"titel": titel, "ordner": "01_Wissen",
                           "inhalt": f"# {titel}\n\nAngelegt im Messlauf."}}]},
            ensure_ascii=False)


# ================================================================= Messung
@dataclass
class Datensatz:
    eingabe: str
    gruppe: str
    erwartet: str
    weg: str = "-"
    quelle: str = "-"
    vektorabrufe: int = 0
    werkzeugaufrufe: int = 0
    abgewiesen: int = 0
    identische_aufrufe: int = 0
    modellaufrufe: int = 0
    modell_phasen: Dict[str, int] = field(default_factory=dict)
    notbremse: int = 0
    endete_mit: str = "-"
    antwort_anfang: str = ""


def aufrufschluessel(name: str, args: dict) -> str:
    """Der kanonische Schluessel - aus dem Produktcode, sobald es ihn gibt."""
    try:
        from jarvis.gespraech.schleifenschutz import aufrufschluessel as echt
        return echt(name, args)
    except ImportError:
        pass
    norm = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            v = re.sub(r"\s+", " ", v.replace("\\", "/")).strip().lower()
            if k in ("name", "titel") and v.endswith(".md"):
                v = v[:-3]
        norm[k] = v
    return f"{name}|{json.dumps(norm, sort_keys=True, ensure_ascii=False)}"


class Messung:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.satz: Optional[Datensatz] = None
        self._gesehen: set = set()
        self._huellen()

    def _huellen(self) -> None:
        agent, messung = self.agent, self

        suche = agent.index.search

        def gezaehlte_suche(*a, **k):
            if messung.satz:
                messung.satz.vektorabrufe += 1
            return suche(*a, **k)
        agent.index.search = gezaehlte_suche

        lauf = agent._run_tool

        def gezaehlter_lauf(name, args, on_tool):
            record = lauf(name, args, on_tool)
            if messung.satz:
                daten = record.ergebnis.daten or {}
                if daten.get("wiederholung") or daten.get("abgewiesen"):
                    messung.satz.abgewiesen += 1
                else:
                    messung.satz.werkzeugaufrufe += 1
                    schluessel = aufrufschluessel(name, args)
                    if schluessel in messung._gesehen:
                        messung.satz.identische_aufrufe += 1
                    messung._gesehen.add(schluessel)
            return record
        agent._run_tool = gezaehlter_lauf

        # Abweisungen, die NICHT durch _run_tool gehen (Stufe 4: vor der
        # Ausfuehrung), meldet der Produktcode ueber diesen Haken.
        agent._abweisung_gemeldet = lambda *a: messung._abgewiesen()

        bericht = werkzeuglauf._schlussbericht

        def gezaehlter_bericht(calls):
            if messung.satz:
                messung.satz.notbremse += 1
            return bericht(calls)
        werkzeuglauf._schlussbericht = gezaehlter_bericht

    def _abgewiesen(self) -> None:
        if self.satz:
            self.satz.abgewiesen += 1

    def modellaufruf(self) -> None:
        if not self.satz:
            return
        self.satz.modellaufrufe += 1
        phase = getattr(getattr(self.agent, "letzte_runde", None), "phase",
                        None) or "schleife"
        self.satz.modell_phasen[phase] = self.satz.modell_phasen.get(phase, 0) + 1

    def echtes_modell_zaehlen(self) -> None:
        llm, messung = self.agent.llm, self
        for name in ("chat", "chat_message"):
            original = getattr(llm, name)

            def huelle(*a, _o=original, **k):
                messung.modellaufruf()
                return _o(*a, **k)
            setattr(llm, name, huelle)

    def beginnen(self, eingabe, gruppe, erwartet) -> None:
        self.satz = Datensatz(eingabe, gruppe, erwartet)
        self._gesehen = set()

    def beenden(self, antwort) -> Datensatz:
        s = self.satz
        text = (getattr(antwort, "text", "") or "").strip()
        s.antwort_anfang = text[:120].replace("\n", " ")
        runde = getattr(self.agent, "letzte_runde", None)
        if runde is not None:
            s.weg = getattr(runde, "weg_text", "-") or "-"
            s.quelle = getattr(runde, "quelle", "-") or "-"
            s.endete_mit = getattr(runde, "endete_mit", "-") or "-"
        if s.endete_mit in ("-", ""):
            s.endete_mit = ("notbremse" if text.startswith("Erledigt:")
                            else "prosa" if text else "leer")
        self.satz = None
        return s


# ================================================================ Ablauf
def agent_bauen():
    vault_bauen()
    agent = JarvisAgent(index=KnowledgeIndex(embeddings=stubs.StubEmbedding()))
    agent.sync()
    return agent


def netz_attrappe(agent) -> None:
    """Das Netz wird nicht wirklich befragt - ein Testlauf darf nichts senden."""
    tools = agent.tools

    def web_suchen(frage="", anzahl=5, **k):
        if not getattr(tools.cfg, "web_enabled", True):
            return ToolResult(False, "Der Internetzugriff ist abgeschaltet.")
        return ToolResult(True, f"1 Treffer für '{frage}'",
                          {"treffer": [{"titel": "Allplan",
                                        "auszug": "Allplan 2026 braucht 16 GB RAM "
                                                  "und Windows 11."}]})

    def web_lesen(url="", **k):
        if not getattr(tools.cfg, "web_enabled", True):
            return ToolResult(False, "Der Internetzugriff ist abgeschaltet.")
        return ToolResult(True, f"Inhalt von {url}",
                          {"text": "Systemvoraussetzungen: 16 GB RAM, Windows 11."})
    tools.web_suchen = web_suchen
    tools.web_lesen = web_lesen
    # Ab Stufe 3 fragt die Triage vorher, ob Netz da ist - ohne Netzverkehr.
    agent.netz_pruefen = lambda: bool(getattr(agent.cfg, "web_enabled", True))


def messen(echt: bool = False) -> List[Datensatz]:
    agent = agent_bauen()
    messung = Messung(agent)
    modell = None
    # Auch gegen das echte Modell: kein echter Netzverkehr aus einem Messlauf.
    netz_attrappe(agent)
    if echt:
        messung.echtes_modell_zaehlen()
    else:
        modell = SturesModell(messung)
        agent.llm.chat = modell.chat
        agent.llm.chat_message = modell.chat_message
        agent._native_tools = True
        agent._capabilities = ["tools"]

    saetze = []
    for eingabe, gruppe, erwartet, sonder in TESTSATZ:
        if modell:
            modell.neue_runde(eingabe)
        web_vorher = agent.cfg.web_enabled
        if sonder == "kein_netz":
            object.__setattr__(agent.cfg, "web_enabled", False)
        messung.beginnen(eingabe + (" [kein Netz]" if sonder else ""),
                         gruppe, erwartet)
        try:
            antwort = agent.ask(eingabe)
        finally:
            object.__setattr__(agent.cfg, "web_enabled", web_vorher)
        saetze.append(messung.beenden(antwort))
    return saetze


def zusammenfassen(saetze: List[Datensatz]) -> Dict[str, object]:
    meta = [s for s in saetze if s.gruppe == "meta"]
    wege: Dict[str, int] = {}
    quellen: Dict[str, int] = {}
    for s in saetze:
        wege[s.weg] = wege.get(s.weg, 0) + 1
        quellen[s.quelle] = quellen.get(s.quelle, 0) + 1
    return {
        "runden_mit_erledigt": sum(s.antwort_anfang.startswith("Erledigt:")
                                   for s in saetze),
        "notbremse": sum(s.notbremse for s in saetze),
        "vektorabrufe_meta": sum(s.vektorabrufe for s in meta),
        "werkzeugaufrufe_meta": sum(s.werkzeugaufrufe + s.abgewiesen for s in meta),
        "identische_ausgefuehrt": sum(s.identische_aufrufe for s in saetze),
        "abgewiesen": sum(s.abgewiesen for s in saetze),
        "modellaufrufe": sum(s.modellaufrufe for s in saetze),
        "wege": wege,
        "quellen": quellen,
    }


def tabelle(saetze: List[Datensatz]) -> str:
    kopf = (f"{'Eingabe':<44} {'erw.':<16} {'Weg':<16} {'Quelle':<9} "
            f"{'Vek':>3} {'Wz':>3} {'Abw':>3} {'Id':>3} {'Mod':>3} "
            f"{'Ende':<12} Antwortanfang")
    zeilen = [kopf, "-" * len(kopf)]
    for s in saetze:
        zeilen.append(
            f"{s.eingabe[:44]:<44} {s.erwartet:<16} {s.weg:<16} {s.quelle:<9} "
            f"{s.vektorabrufe:>3} {s.werkzeugaufrufe:>3} {s.abgewiesen:>3} "
            f"{s.identische_aufrufe:>3} {s.modellaufrufe:>3} {s.endete_mit:<12} "
            f"{s.antwort_anfang[:60]}")
    z = zusammenfassen(saetze)
    zeilen += ["", "Kennzahlen:"]
    zeilen += [f"  {k:<24} {v}" for k, v in z.items()]
    return "\n".join(zeilen)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        saetze = messen(echt=ECHT)
        print(("ECHTES MODELL" if ECHT else "Skriptmodell (stur)") + "\n")
        print(tabelle(saetze))
        if "--json" in sys.argv:
            ziel = Path(sys.argv[sys.argv.index("--json") + 1])
            ziel.write_text(json.dumps(
                {"datensaetze": [asdict(s) for s in saetze],
                 "kennzahlen": zusammenfassen(saetze)},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nJSON: {ziel}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
