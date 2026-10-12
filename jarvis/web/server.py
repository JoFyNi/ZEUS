"""Der kleine HTTP-Server hinter der Oberfläche.

Bewusst mit Bordmitteln (`http.server`) statt Flask oder FastAPI. Der Grund ist
derselbe wie überall in diesem Projekt: Jede zusätzliche Abhängigkeit ist eine
zusätzliche Stelle, an der eine frische Windows-Installation scheitert. Für
eine Oberfläche, die genau ein Mensch auf genau einem Rechner benutzt, reicht
ein Thread je Anfrage vollkommen.

Der Chat streamt über **Server-Sent Events**. Ein WebSocket wäre die
naheliegende Wahl, braucht aber ein Paket; SSE ist einfacher Text über eine
offene HTTP-Antwort und funktioniert mit dem, was schon da ist. Die Richtung
stimmt auch: Der Server schickt Token, das Fenster schickt nur die Frage.

Sicherheit: Der Server hört ausschließlich auf 127.0.0.1 und verlangt bei jeder
Anfrage ein Kennwort, das beim Start erzeugt und dem Fenster als Adresse
mitgegeben wird. Der Grund ist nicht Paranoia - der Server darf über die
Werkzeuge in die Vault schreiben, und ein anderes Programm auf demselben
Rechner soll das nicht per Zufallstreffer auslösen können.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from ..config import reload_settings, settings
from .. import schau
from . import einstellungen as einst

HIER = Path(__file__).resolve().parent
PROJEKT = HIER.parent.parent


def _quellen(hits, grenze: int = 8) -> list:
    """Treffer zu Quellenmarken - eine je Notiz, ohne Wiederholung.

    Ein Hit ist KEIN Note-Objekt: Der Notizname steht in `metadata`. Wer hier
    str(hit) nimmt, bekommt die komplette Python-Darstellung samt Text und
    Metadaten in die Oberfläche geschrieben - genau das ist beim ersten Lauf
    passiert.
    """
    ausgabe, gesehen = [], set()
    for hit in (hits or []):
        meta = getattr(hit, "metadata", None) or {}
        rel = meta.get("rel_path", "")
        if rel.startswith("__struktur__"):
            name, rel = "Vault-Struktur", ""
        else:
            name = meta.get("stem") or meta.get("filename") or ""
        if not name or name in gesehen:
            continue
        gesehen.add(name)
        ausgabe.append({
            "name": name, "pfad": rel,
            "ordner": meta.get("folder", ""),
            "guete": round(float(getattr(hit, "score", 0.0) or 0.0), 2),
        })
        if len(ausgabe) >= grenze:
            break
    return ausgabe


class Sitzung:
    """Hält den Agenten und die laufenden Chatströme.

    Ein Agent für alle Anfragen - er hat einen Gesprächsverlauf, und den soll
    das Fenster teilen, nicht je Tab neu anfangen. Ein Schloss darum, weil
    zwei gleichzeitige Fragen sich sonst den Verlauf zerschießen würden.
    """

    def __init__(self, agent=None, cfg=settings):
        self.cfg = cfg
        self._agent = agent
        self._schloss = threading.Lock()
        self.stroeme: Dict[str, "queue.Queue"] = {}
        self.beschaeftigt = False
        self.letzter_fehler = ""
        # Für /quellen und /merke: die letzte Antwort muss die Sitzung
        # überdauern, nicht nur den einen Strom.
        self.letzte_antwort = None
        self._bruecke = None

    @property
    def agent(self):
        if self._agent is None:
            from ..agent import JarvisAgent
            self._agent = JarvisAgent()
        return self._agent

    @property
    def bruecke(self):
        """Die Befehlsebene - dieselbe wie im Terminal, siehe befehle.py."""
        if self._bruecke is None:
            from .bruecke import Befehlsbruecke
            self._bruecke = Befehlsbruecke(self.agent, self.cfg)
        return self._bruecke

    def befehl(self, zeile: str) -> dict:
        with self._schloss:
            self.beschaeftigt = True
            try:
                return self.bruecke.ausfuehren(zeile)
            finally:
                self.beschaeftigt = False

    # ------------------------------------------------------------- Chat
    def frage_starten(self, frage: str) -> str:
        kennung = secrets.token_hex(8)
        strom: "queue.Queue" = queue.Queue()
        self.stroeme[kennung] = strom
        threading.Thread(target=self._arbeiten, args=(frage, strom),
                         daemon=True).start()
        return kennung

    def _arbeiten(self, frage: str, strom: "queue.Queue") -> None:
        def sende(art: str, **rest) -> None:
            strom.put({"art": art, **rest})

        abmelden = lambda: None                          # noqa: E731

        with self._schloss:
            self.beschaeftigt = True
            sende("beginn")
            try:
                # Schrägstrichbefehle gehen NIE ans Modell. Genau das war der
                # Fehler: "/help" wurde als Frage geschickt, und das Modell hat
                # sich eine Antwort ausgedacht.
                if frage.lstrip().startswith("/"):
                    ergebnis = self.bruecke.ausfuehren(frage.strip())
                    sende("fertig", text=ergebnis.get("text", ""), quellen=[],
                          art_der_antwort="befehl",
                          ok=bool(ergebnis.get("ok")))
                    # /merke und /quellen können die letzte Antwort verändern
                    self.letzte_antwort = (self.bruecke.letzte_antwort
                                           or self.letzte_antwort)
                    return
                # Innere Schritte melden, solange dieser Auftrag läuft.
                # Danach wieder abhängen - ein Melder, der ins Leere zeigt,
                # ist die Sorte Zustand, die man beim Debuggen sucht.
                self.agent.schrittmelder_setzen(
                    lambda werkzeug, schritt: sende(
                        "schritt", werkzeug=werkzeug, schritt=schritt))
                from ..protokoll import protokoll as _log
                abmelden = _log(self.cfg).zuhoeren(
                    lambda e: sende("protokoll", eintrag=e.als_dict()))
                antwort = self.agent.ask(
                    frage,
                    stream_cb=lambda t: sende("token", text=t),
                    on_tool=lambda r: sende(
                        "werkzeug", name=r.werkzeug, ok=bool(r.ergebnis.ok),
                        meldung=r.ergebnis.meldung,
                        parameter={k: str(v)[:200] for k, v in (r.parameter or {}).items()}),
                )
                # Die Befehlsebene muss die Antwort kennen, sonst sagt
                # /merke "Es gibt noch keine Antwort zum Speichern" - obwohl
                # sie im Fenster steht.
                if self._bruecke is not None:
                    self._bruecke.antwort_uebernehmen(antwort)
                self.letzte_antwort = antwort
                sende("fertig", text=antwort.text or "",
                      quellen=_quellen(antwort.hits),
                      art_der_antwort=antwort.kind)
            except Exception as exc:                    # noqa: BLE001
                # Ein Fehler hier darf die Oberfläche nicht stumm lassen -
                # genau das war im Terminal der "…"-Fall.
                self.letzter_fehler = traceback.format_exc()
                sende("fehler", text=f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    self.agent.schrittmelder_setzen(None)
                    abmelden()
                except Exception:                        # noqa: BLE001
                    pass
                self.beschaeftigt = False
                sende("ende")

    # ------------------------------------------------------------ Daten
    def graph(self) -> dict:
        return schau.graph_daten(self.agent.tools._notes())

    def baum(self) -> dict:
        from ..tools import TOOL_SCHEMAS

        fertigkeiten = []
        try:
            fertigkeiten = list(self.agent.fertigkeiten.alle.values())
        except Exception:                               # noqa: BLE001
            pass
        return schau.baum_daten(TOOL_SCHEMAS, fertigkeiten,
                                testordner=PROJEKT / "tests", cfg=self.cfg)

    def notiz(self, name: str) -> dict:
        note = self.agent.tools.notiz_exakt(name) or self.agent.tools.resolve_note(name)
        if not note:
            return {"ok": False, "meldung": f"Keine Notiz namens '{name}'."}
        return {
            "ok": True, "name": note.stem, "titel": note.title or note.stem,
            "pfad": note.rel_path, "ordner": note.folder,
            "tags": list(note.tags or []), "links": list(note.links or []),
            "text": note.body or "",
        }

    def kennzahlen(self) -> dict:
        """Die Zahlen für die Kopfleiste - ein Blick, kein Aufklappen."""
        from .. import nutzung
        from ..tools import TOOL_NAMES

        notizen = self.agent.tools._notes()
        strich = nutzung.lesen(self.cfg)
        aufrufe = sum(int(e.get("aufrufe") or 0) for e in strich.values())
        fehler = sum(int(e.get("fehler") or 0) for e in strich.values())
        ordner = {n.folder.split("/")[0] for n in notizen if n.folder}
        return {
            "notizen": len(notizen),
            "ordner": len(ordner),
            "werkzeuge": len(TOOL_NAMES),
            "benutzt": len([n for n in strich if int(strich[n].get("aufrufe") or 0)]),
            "aufrufe": aufrufe,
            "fehlerquote": round(fehler / aufrufe, 3) if aufrufe else 0.0,
        }

    def vorschlaege(self, art: str) -> dict:
        """Listen für die Magic-Eingabe: was hinter `[` und hinter `/x ` steht.

        Alles kommt aus dem laufenden System, nichts ist eingetippt. Eine
        Vorschlagsliste, die etwas anbietet, das es nicht gibt, ist schlimmer
        als gar keine.
        """
        art = (art or "").lower()
        if art in ("werkzeug", "werkzeuge", "eintrag"):
            return {"art": art, "eintraege": self._werkzeugliste()}
        if art in ("fertigkeit", "fertigkeiten"):
            return {"art": art, "eintraege": [
                {"name": f.name, "hinweis": "Fertigkeit",
                 "kurz": getattr(f, "beschreibung", "")}
                for f in self._fertigkeiten()]}
        if art in ("notiz", "notizen"):
            return {"art": art, "eintraege": [
                {"name": n.stem, "hinweis": n.folder or "Wurzel",
                 "kurz": (n.title or "")[:80]}
                for n in self.agent.tools._notes()[:600]]}
        if art in ("ordner", "ordnern"):
            return {"art": art, "eintraege": [
                {"name": p, "hinweis": "Ordner", "kurz": ""}
                for p in self.agent.structure.folder_paths]}
        if art in ("codedatei", "code"):
            ergebnis = self.agent.tools.code_dateien()
            zeilen = [z.strip("- ").strip()
                      for z in str(ergebnis.meldung).splitlines() if z.strip()]
            return {"art": art, "eintraege": [
                {"name": z.split()[0], "hinweis": "Quelldatei", "kurz": ""}
                for z in zeilen if z and not z.endswith(":")][:200]}
        return {"art": art, "eintraege": []}

    def _werkzeugliste(self) -> list:
        """Werkzeuge UND eigene Fertigkeiten - beides ist für ihn ein Werkzeug."""
        from ..tools import SCHREIBENDE_WERKZEUGE, TOOL_SCHEMAS
        from .. import nutzung

        strich = nutzung.lesen(self.cfg)
        eintraege = []
        for schema in TOOL_SCHEMAS:
            name = schema["name"]
            eintraege.append({
                "name": name,
                "hinweis": "schreibt" if name in SCHREIBENDE_WERKZEUGE else "liest",
                "kurz": str(schema.get("description", "")).split(".")[0],
                "parameter": list((schema.get("parameters") or {}).keys()),
                "aufrufe": int(strich.get(name, {}).get("aufrufe") or 0),
            })
        for f in self._fertigkeiten():
            eintraege.append({
                "name": f.name, "hinweis": "Fertigkeit",
                "kurz": getattr(f, "beschreibung", ""),
                "parameter": list(getattr(f, "parameter", []) or []),
                "aufrufe": int(strich.get(f.name, {}).get("aufrufe") or 0),
            })
        return eintraege

    def _fertigkeiten(self) -> list:
        try:
            return list(self.agent.fertigkeiten.alle.values())
        except Exception:                                # noqa: BLE001
            return []

    def status(self) -> dict:
        llm = self.agent.llm
        return {
            "name": self.cfg.assistant_name,
            "anrede": self.cfg.user_title,
            "host": getattr(llm, "host", self.cfg.host),
            "modell": getattr(llm, "modell", self.cfg.llm_model),
            "auf_ausweich": bool(getattr(llm, "auf_ausweich", False)),
            "vault": str(self.cfg.vault_path),
            "beschaeftigt": self.beschaeftigt,
            "env": str(self.cfg.env_file) if self.cfg.env_file else "",
            "werkzeuge": self.cfg.tools_enabled,
            "trockenlauf": self.cfg.dry_run,
            # Für die Vervollständigung im Eingabefeld. Aus derselben
            # Hilfetabelle wie im Terminal - keine zweite Liste.
            "befehle": self._befehlsnamen(),
        }

    @staticmethod
    def _befehlsnamen() -> list:
        try:
            from .bruecke import Befehlsbruecke
            return Befehlsbruecke.namen()
        except Exception:                               # noqa: BLE001
            return []


def _json_antwort(handler: BaseHTTPRequestHandler, daten: Any, code: int = 200) -> None:
    roh = json.dumps(daten, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(roh)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(roh)


class Griff(BaseHTTPRequestHandler):
    """Ein Anfragebehandler. sitzung und schluessel werden von starten() gesetzt."""

    sitzung: Optional[Sitzung] = None
    schluessel: str = ""
    server_version = "Zeus"
    sys_version = ""

    # Das Protokoll der Standardbibliothek schreibt jede Anfrage nach stderr.
    # Bei einem Chatstrom sind das hunderte Zeilen - unbrauchbar.
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    # ------------------------------------------------------------ Zugang
    def _erlaubt(self, pfad: str, abfrage: dict) -> bool:
        if not self.schluessel:
            return True
        if (abfrage.get("s") or [""])[0] == self.schluessel:
            return True
        return self.headers.get("X-Zeus-Schluessel", "") == self.schluessel

    # -------------------------------------------------------------- GET
    def do_GET(self) -> None:                            # noqa: N802
        teile = urlparse(self.path)
        abfrage = parse_qs(teile.query)
        pfad = teile.path

        if pfad in ("/", "/index.html"):
            return self._datei(HIER / "oberflaeche.html", "text/html; charset=utf-8")
        if not self._erlaubt(pfad, abfrage):
            return _json_antwort(self, {"fehler": "Kein Zugang."}, 403)

        try:
            if pfad == "/api/status":
                return _json_antwort(self, self.sitzung.status())
            if pfad == "/api/kennzahlen":
                return _json_antwort(self, self.sitzung.kennzahlen())
            if pfad == "/api/graph":
                return _json_antwort(self, self.sitzung.graph())
            if pfad == "/api/baum":
                return _json_antwort(self, self.sitzung.baum())
            if pfad == "/api/notiz":
                return _json_antwort(self, self.sitzung.notiz(
                    (abfrage.get("name") or [""])[0]))
            if pfad == "/api/protokoll":
                from ..protokoll import protokoll
                log = protokoll(self.sitzung.cfg)
                return _json_antwort(self, {
                    "eintraege": log.lesen(
                        anzahl=int((abfrage.get("anzahl") or ["300"])[0]),
                        ebene=(abfrage.get("ebene") or [""])[0],
                        ab_nummer=int((abfrage.get("ab") or ["0"])[0])),
                    "zahlen": log.zahlen(),
                    "datei": str(log.pfad or ""),
                })
            if pfad == "/api/befehle":
                from ..befehle import als_liste
                return _json_antwort(self, {"befehle": als_liste()})
            if pfad == "/api/vorschlaege":
                return _json_antwort(self, self.sitzung.vorschlaege(
                    (abfrage.get("art") or ["werkzeug"])[0]))
            if pfad == "/api/einstellungen":
                return _json_antwort(self, {
                    "gruppen": einst.als_liste(self.sitzung.cfg),
                    "datei": str(self.sitzung.cfg.env_file or ""),
                })
            if pfad == "/api/strom":
                return self._strom((abfrage.get("id") or [""])[0])
        except Exception as exc:                         # noqa: BLE001
            return _json_antwort(
                self, {"fehler": f"{type(exc).__name__}: {exc}",
                       "spur": traceback.format_exc()[-1500:]}, 500)
        _json_antwort(self, {"fehler": "Unbekannter Pfad."}, 404)

    # ------------------------------------------------------------- POST
    def do_POST(self) -> None:                           # noqa: N802
        teile = urlparse(self.path)
        abfrage = parse_qs(teile.query)
        if not self._erlaubt(teile.path, abfrage):
            return _json_antwort(self, {"fehler": "Kein Zugang."}, 403)
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            laenge = 0
        try:
            koerper = json.loads(self.rfile.read(laenge) or b"{}")
        except ValueError:
            return _json_antwort(self, {"fehler": "Kein gültiges JSON."}, 400)

        try:
            if teile.path == "/api/frage":
                frage = str(koerper.get("frage", "")).strip()
                if not frage:
                    return _json_antwort(self, {"fehler": "Leere Frage."}, 400)
                return _json_antwort(self, {"id": self.sitzung.frage_starten(frage)})
            if teile.path == "/api/einstellungen":
                werte = koerper.get("werte") or {}
                ergebnis = einst.speichern(werte, self.sitzung.cfg)
                if ergebnis.get("ok"):
                    reload_settings()
                return _json_antwort(self, ergebnis)
            if teile.path == "/api/befehl":
                zeile = str(koerper.get("zeile", "")).strip()
                if not zeile.startswith("/"):
                    return _json_antwort(self, {"fehler": "Kein Befehl."}, 400)
                return _json_antwort(self, self.sitzung.befehl(zeile))
            if teile.path == "/api/protokoll_leeren":
                from ..protokoll import protokoll
                log = protokoll(self.sitzung.cfg)
                anzahl = log.leeren()
                # Nur die Anzeige. Die Datei bleibt - sie ist der Nachweis.
                return _json_antwort(self, {"ok": True, "geleert": anzahl,
                                            "datei": str(log.pfad or "")})
            if teile.path == "/api/sync":
                bericht = self.sitzung.agent.sync(force=bool(koerper.get("neu")))
                return _json_antwort(self, {"ok": True, "text": bericht.summary()})
        except Exception as exc:                         # noqa: BLE001
            return _json_antwort(
                self, {"fehler": f"{type(exc).__name__}: {exc}",
                       "spur": traceback.format_exc()[-1500:]}, 500)
        _json_antwort(self, {"fehler": "Unbekannter Pfad."}, 404)

    # ------------------------------------------------------------ Helfer
    def _datei(self, pfad: Path, typ: str) -> None:
        try:
            roh = pfad.read_bytes()
        except OSError:
            return _json_antwort(self, {"fehler": f"{pfad.name} fehlt."}, 404)
        # Der Schlüssel wird in die Seite eingesetzt, damit das Fenster ihn
        # nicht in der Adresszeile tragen muss.
        if typ.startswith("text/html"):
            roh = roh.replace(b"__ZEUS_SCHLUESSEL__",
                              self.schluessel.encode("ascii", "ignore"))
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(roh)

    def _strom(self, kennung: str) -> None:
        """Server-Sent Events: eine offene Antwort, in die Ereignisse tropfen."""
        strom = self.sitzung.stroeme.get(kennung)
        if strom is None:
            return _json_antwort(self, {"fehler": "Unbekannter Strom."}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                try:
                    ereignis = strom.get(timeout=20)
                except queue.Empty:
                    # Ein Doppelpunkt-Kommentar hält die Verbindung offen, ohne
                    # dass der Browser etwas davon sieht.
                    self.wfile.write(b": still\n\n")
                    self.wfile.flush()
                    continue
                roh = json.dumps(ereignis, ensure_ascii=False, default=str)
                self.wfile.write(f"data: {roh}\n\n".encode("utf-8"))
                self.wfile.flush()
                if ereignis.get("art") == "ende":
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass                                    # Fenster zu - völlig normal
        finally:
            self.sitzung.stroeme.pop(kennung, None)


def starten(agent=None, port: int = 0, cfg=settings,
            schluessel: Optional[str] = None):
    """Server im Hintergrund starten. Gibt (server, adresse) zurück.

    port=0 heißt: Das Betriebssystem sucht einen freien Port. Damit gibt es
    keinen "Adresse bereits in Benutzung"-Fall, wenn Zeus zweimal läuft.
    """
    mimetypes.init()
    sitzung = Sitzung(agent, cfg)
    schluessel = schluessel if schluessel is not None else secrets.token_urlsafe(16)

    griff = type("ZeusGriff", (Griff,),
                 {"sitzung": sitzung, "schluessel": schluessel})
    server = ThreadingHTTPServer(("127.0.0.1", port), griff)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    adresse = f"http://127.0.0.1:{server.server_address[1]}/"
    return server, adresse, sitzung
