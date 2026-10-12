import os, sys, shutil
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import stubs; stubs.install()
V = Path(__file__).resolve().parent / "_v"; shutil.rmtree(V, ignore_errors=True); V.mkdir(parents=True)
(V / "Backprop.md").write_text("# Backprop\n\nKettenregel rueckwaerts, Gradient.\n", encoding="utf-8")
os.environ["OBSIDIAN_VAULT"] = str(V)
os.environ["VECTOR_STORE_PATH"] = str(Path(__file__).resolve().parent / "_s")
os.environ["DRY_RUN"] = "false"
from jarvis.config import reload_settings; reload_settings()
from jarvis.index import KnowledgeIndex
from jarvis.tools import VaultTools
from jarvis.skills import Fertigkeiten
ix = KnowledgeIndex(embeddings=stubs.StubEmbedding()); ix.sync()
f = Fertigkeiten(VaultTools(ix))

# GENAU das Vorbild aus dem Systemprompt (agent.py:453-455)
VORBILD = ['notizen_suchen(frage="{thema}", anzahl=8)',
           'notiz_erstellen(titel="Uebersicht {thema}", inhalt="{ergebnis1}")',
           'verwandte_finden(name="Uebersicht {thema}")']
r = f.probelauf(VORBILD, ["thema"])
print("PROBELAUF ok?", r.ok, "|", r.meldung)
for z in r.daten.get("protokoll", []): print("   ", z)
print()
d = f.definieren(name="thema_aufarbeiten", beschreibung="Vorbild aus dem Systemprompt",
                 parameter=["thema"], schritte=VORBILD)
print("DEFINIEREN ok?", d.ok, "|", d.meldung[:150])
print("Datei da?", (V / "_Fertigkeiten" / "thema_aufarbeiten.md").exists())
shutil.rmtree(V, ignore_errors=True); shutil.rmtree(Path(__file__).resolve().parent / "_s", ignore_errors=True)
