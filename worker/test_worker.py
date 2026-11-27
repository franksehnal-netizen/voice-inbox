"""Testy workera (F1 + F2) v izolovaných složkách v %TEMP%\\voice_inbox_test. Výstup: řádky PASS/FAIL + souhrn.
Potřebuje GPU whisper (testy 1, 5, 6, 9) a přihlášený claude CLI (test 14 – 3 krátká volání). Vault jen čte (seznam stránek).
Spuštění: python test_worker.py   (~1 min)"""
import json, os, shutil, subprocess, sys, tempfile, time, threading
from pathlib import Path

WORKER = str(Path(__file__).with_name("voice_inbox_worker.py"))
ROOT = Path(tempfile.gettempdir()) / "voice_inbox_test"
INC, PROC, VAULT, STATE = (ROOT / n for n in ("incoming", "processed", "vault", "state"))
OLD = time.time() - 3600
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"), flush=True)


WIKI = Path(r"G:\My Drive\Obsidian\Obsidian_Fanda\wiki")


def worker(*extra, env=None, incoming=None):
    e = dict(os.environ, **{"VOICE_INBOX_FAKE_SUMMARY": "FAIL", **(env or {})})
    if e["VOICE_INBOX_FAKE_SUMMARY"] == "REAL":
        del e["VOICE_INBOX_FAKE_SUMMARY"]
    return subprocess.run([sys.executable, WORKER, "--incoming", str(incoming or INC), "--processed", str(PROC),
                           "--vault", str(VAULT), "--wiki", str(WIKI), "--state-dir", str(STATE), *extra], env=e,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def state():
    p = STATE / "state.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def notes():
    return sorted(VAULT.rglob("*.md"))


def put(src, name, old=True):
    dst = INC / name
    shutil.copy2(src, dst)
    t = OLD if old else time.time()
    os.utime(dst, (t, t))
    return dst


def edit_state(fn):
    st = state(); fn(st)
    (STATE / "state.json").write_text(json.dumps(st), "utf-8")


def make_due():  # přeskočit backoff shrnutí
    edit_state(lambda st: [it.update(summary_next=None) for it in st.values()])


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], check=True)


# ---------- příprava ----------
shutil.rmtree(ROOT, ignore_errors=True)
for d in (INC, PROC, VAULT, STATE):
    d.mkdir(parents=True)
wav = ROOT / "speech.wav"
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('Call the supplier tomorrow about the absorber panels. "
                "Then send the invoice to Ivan.'); $s.Dispose()"], check=True)
speech = ROOT / "speech.m4a"; ff("-i", str(wav), "-c:a", "aac", "-b:a", "64k", str(speech))
silence = ROOT / "silence.m4a"; ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "10", "-c:a", "aac", str(silence))
webm = ROOT / "speech.webm"; ff("-i", str(wav), "-c:a", "libopus", str(webm))
corrupt = ROOT / "corrupt.m4a"; corrupt.write_bytes(os.urandom(50_000))

# 1) normální průchod (skutečný whisper na GPU)
put(speech, "20261127-101500.m4a")
t = time.time(); r = worker(); el = time.time() - t
s = state().get("20261127-101500", {})
n = notes()
check("1 normal: archived", s.get("status") == "archived", (s, r.stderr[-500:]))
check("1 normal: audio v processed/2026-11", (PROC / "2026-11" / "20261127-101500.m4a").exists())
check("1 normal: incoming prázdné", not (INC / "20261127-101500.m4a").exists())
check("1 normal: 1 poznámka v nezarazeno", len(n) == 1 and n[0].parent.name == "nezarazeno", n)
txt = n[0].read_text("utf-8") if n else ""
check("1 normal: přepis neprázdný", s.get("chars", 0) > 10 and "status: needs-summary" in txt, txt[-300:])
print(f"     (běh {el:.1f} s, přepis {s.get('transcribe_s')} s)\n" + txt)

# 2) duplikát: opakovaný běh + znovu nahraný týž soubor
before = (STATE / "state.json").read_text("utf-8")
worker()
check("2 dup: opakovaný běh nic nezmění (backoff shrnutí)", (STATE / "state.json").read_text("utf-8") == before
      and len(notes()) == 1 and state()["20261127-101500"]["summary_attempts"] == 1)
put(speech, "20261127-101500.m4a"); worker()
check("2 dup: re-upload → processed/…-dup1, žádná nová poznámka",
      (PROC / "2026-11" / "20261127-101500-dup1.m4a").exists() and len(notes()) == 1 and not any(INC.iterdir()))

# 3) pád uprostřed (po transcribed, pak po noted)
fake = {"VOICE_INBOX_FAKE_TRANSCRIPT": "testovací přepis"}
put(speech, "20261127-110000.m4a")
r = worker(env=dict(fake, VOICE_INBOX_CRASH_AFTER="transcribed"))
check("3 crash@transcribed: exit 3, stav transcribed", r.returncode == 3 and state()["20261127-110000"]["status"] == "transcribed")
worker(env=fake)
check("3 crash@transcribed: doběhne, 1 poznámka", state()["20261127-110000"]["status"] == "archived"
      and len([p for p in notes() if "1100" in p.name]) == 1)
put(speech, "20261127-120000.m4a")
r = worker(env=dict(fake, VOICE_INBOX_CRASH_AFTER="noted"))
check("3 crash@noted: exit 3, poznámka už existuje", r.returncode == 3 and state()["20261127-120000"]["status"] == "noted")
worker(env=fake)
check("3 crash@noted: doběhne, pořád 1 poznámka", state()["20261127-120000"]["status"] == "archived"
      and len([p for p in notes() if "1200" in p.name]) == 1)
# pád mezi zápisem poznámky a uložením stavu = stav transcribed, poznámka existuje → nesmí vzniknout druhá
st = state(); st["20261127-120000"]["status"] = "transcribed"
(STATE / "state.json").write_text(json.dumps(st), "utf-8")
shutil.move(str(PROC / "2026-11" / "20261127-120000.m4a"), str(INC / "20261127-120000.m4a"))
worker(env=fake)
check("3 crash mezi note a stavem: bez duplikátu poznámky", len([p for p in notes() if "1200" in p.name]) == 1
      and state()["20261127-120000"]["status"] == "archived")

# 4) poškozené audio → 3 pokusy → failed, soubor zůstává
put(corrupt, "20261127-130000.m4a")
for _ in range(3):
    worker(env=fake)
s = state()["20261127-130000"]
check("4 corrupt: failed po 3 pokusech, soubor zůstal", s["status"] == "failed" and s["attempts"] == 3
      and (INC / "20261127-130000.m4a").exists(), s)
worker(env=fake)
check("4 corrupt: failed se dál nezkouší", state()["20261127-130000"]["attempts"] == 3)

# 5) ticho (skutečný whisper)
put(silence, "20261127-140000.m4a"); worker()
n5 = [p for p in notes() if "1400" in p.name]
check("5 ticho: poznámka s označením ticha", state()["20261127-140000"]["status"] == "archived" and n5
      and "ticho" in n5[0].read_text("utf-8"), state()["20261127-140000"])

# 6) webm z MediaRecorderu (Chrome/Android) – skutečný whisper
put(webm, "20261127-150000.webm"); worker()
check("6 webm: archived", state()["20261127-150000"]["status"] == "archived", state()["20261127-150000"])

# 7) G: nepřipojený
r = worker(incoming=ROOT / "neexistuje")
check("7 G: chybí: exit 0, varování v logu", r.returncode == 0 and "není dostupné" in (STATE / "worker.log").read_text("utf-8"))

# 8) čerstvý soubor (ještě se synchronizuje) se přeskočí
put(speech, "20261127-160000.m4a", old=False); worker(env=fake)
check("8 čerstvý: zatím nezpracován", "20261127-160000" not in state() and (INC / "20261127-160000.m4a").exists())

# 9) mutex: dva souběžné běhy
os.utime(INC / "20261127-160000.m4a", (OLD, OLD))
out = []
th = [threading.Thread(target=lambda: out.append(worker())) for _ in range(2)]  # skutečný whisper: načtení modelu trvá
th[0].start(); time.sleep(1.5); th[1].start()
for x in th: x.join()
logtxt = (STATE / "worker.log").read_text("utf-8")
check("9 mutex: druhý běh skončil, 1 poznámka", "jiná instance běží" in logtxt
      and len([p for p in notes() if "1600" in p.name]) == 1)


# ---------- F2 ----------
def note_of(rid):
    return Path(state()[rid]["note"])


check("10 F2 příprava: 101500 a 110000 needs-summary",
      all(state()[r].get("note_status") == "needs-summary" for r in ("20261127-101500", "20261127-110000")),
      {r: state()[r].get("note_status") for r in state()})
# 11) simulace pádu v resummarize: nová poznámka 110000 už v ae/ existuje, stará zůstala
old110 = note_of("20261127-110000")
pre = (VAULT / "ae" / "2026-11-27-1100-predchozi-beh.md")
pre.parent.mkdir(exist_ok=True)
pre.write_text(old110.read_text("utf-8").replace("_Čeká na shrnutí._", "shrnuto"), "utf-8")
# 12) 120000 ručně upravená poznámka (značka pryč) → nesahat
n120 = note_of("20261127-120000")
n120.write_text(n120.read_text("utf-8").replace("_Čeká na shrnutí._", "Moje ruční poznámka."), "utf-8")
n120_before = n120.read_text("utf-8")
fake_sm = json.dumps({"domain": "ae", "title": "Dodavatel absorbérů \"test\"", "slug": "Špatný Slug!",
                      "summary": "Zavolat dodavateli.", "tasks": ["Zavolat dodavateli"], "tags": ["Absorbéry", "voice-note", "ae"],
                      "related": ["neexistujici-stranka", "acoustic-empire", "[[boommount]]"]})
make_due()
worker(env={"VOICE_INBOX_FAKE_SUMMARY": fake_sm})
s = state()["20261127-101500"]
nn = Path(s["note"]); t = nn.read_text("utf-8") if nn.exists() else ""
check("10 resummary: přesun do ae/, stará smazána", s["note_status"] == "summarized" and nn.parent.name == "ae"
      and nn.exists() and len([p for p in notes() if "1015" in p.name]) == 1, (s, notes()))
check("10 resummary: slug ze title, jen existující related + hub, tagy", "dodavatel-absorberu-test" in nn.name
      and "[[neexistujici-stranka]]" not in t and "[[acoustic-empire]]" in t
      and "tags: [voice-note, ae, absorbery]" in t and "- [ ] Zavolat dodavateli" in t, t[:900])
print(t)
check("11 resummary po pádu: převezme existující, bez nové", note_of("20261127-110000") == pre
      and not old110.exists() and len([p for p in notes() if "1100" in p.name]) == 1, notes())
check("12 ručně upravená: nezměněna, manual", state()["20261127-120000"]["note_status"] == "manual"
      and n120.read_text("utf-8") == n120_before)
before = (STATE / "state.json").read_text("utf-8")
worker(env={"VOICE_INBOX_FAKE_SUMMARY": fake_sm})
check("10 resummary: další běh nic nemění", (STATE / "state.json").read_text("utf-8") == before)

# 13) strop pokusů o shrnutí
R = "20261127-170000"
put(speech, R + ".m4a")
worker(env=fake); worker(env=fake)
s = state()[R]
check("13 FAIL: 1 pokus, další až po backoffu (~10 min)", s["summary_attempts"] == 1 and s["summary_next"] > time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 540)), s)
edit_state(lambda st: st[R].update(summary_attempts=5, summary_next=None))
worker(env=fake); make_due(); worker(env=fake)
s = state()[R]
check("13 FAIL: strop 6 pokusů, poznámka needs-summary zůstává", s["summary_attempts"] == 6
      and s["note_status"] == "needs-summary" and note_of(R).exists(), s)

# 14) skutečný Claude: 3 domény
pages = {p.stem for p in WIKI.rglob("*.md")}
cases = {
    "20261128-080000": ("osobni", "Ráno nezapomenout objednat Aničku k zubaři na příští týden a v sobotu vyměnit "
                        "baterky v termostatu v obýváku, ten Home Assistant pořád hlásí slabou baterii."),
    "20261128-090000": ("zlin-city", "Nápad do Zlín City, pro Sound Set tramvaje udělat jemnější brzdění, "
                        "Plop Low je moc tvrdý. Napsat Slávimu, ať v MetaSoundu přidá random pitch."),
    "20261128-100000": ("ae", "Ivan říkal, že ten zákazník ze studia chce osm absorbérů a dva difuzory, "
                        "do pátku mu poslat kalkulaci a zjistit u dodavatele cenu tkaniny."),
}
for rid, (dom, text) in cases.items():
    put(speech, rid + ".m4a")
    t0 = time.time(); worker(env={"VOICE_INBOX_FAKE_TRANSCRIPT": text, "VOICE_INBOX_FAKE_SUMMARY": "REAL"})
    s = state()[rid]; nn = Path(s["note"]); t = nn.read_text("utf-8")
    links = __import__("re").findall(r"\[\[([^\]]+)\]\]", t)
    check(f"14 claude {dom}: složka a status", nn.parent.name == dom and s["note_status"] == "summarized", (s, nn))
    check(f"14 claude {dom}: všechny odkazy existují", links and all(l in pages for l in links) or dom == "osobni"
          and all(l in pages for l in links), links)
    print(f"     ({time.time() - t0:.1f} s) {nn.name}\n" + t.split("## Přepis")[0])

print(f"\nSOUHRN: {sum(results)}/{len(results)} PASS")
