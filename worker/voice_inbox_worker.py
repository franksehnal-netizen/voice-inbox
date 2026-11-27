"""Voice Inbox – PC worker (F1 přepis + stavový automat, F2 shrnutí a zařazení přes claude -p).

Jeden běh = projde My Drive/voice_inbox/incoming, každou nahrávku:
  new → (kopie do temp + ffprobe) → transcribed → (claude -p) noted (poznámka ve vaultu) → archived
Shrnutí selže → poznámka v nezarazeno se status needs-summary; další běhy ji dosumarizují a přesunou
(6 pokusů s odstupem 10 min … 24 h; --retry-failed vynuluje).
Stav lokálně v %LOCALAPPDATA%\\voice_inbox\\state.json (atomic replace). Audio se nikdy nemaže.
Spouští Task Scheduler (pythonw, bez okna). Plán: ../voice_inbox_plan.md.

Testovací přepínače: --incoming/--processed/--vault/--wiki/--state-dir, env VOICE_INBOX_CRASH_AFTER=<stav>
(simulace pádu hned po zápisu daného stavu), VOICE_INBOX_FAKE_TRANSCRIPT=<text> (bez GPU),
VOICE_INBOX_FAKE_SUMMARY=<json>|FAIL (bez Clauda).
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import glob
import json
import logging
import logging.handlers
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "voice_inbox"
DEFAULTS = {
    "incoming": r"G:\My Drive\voice_inbox\incoming",
    "processed": r"G:\My Drive\voice_inbox\processed",
    "vault": r"G:\My Drive\Obsidian\Obsidian_Fanda\wiki\sources\voice",
    "wiki": r"G:\My Drive\Obsidian\Obsidian_Fanda\wiki",
    "state_dir": str(LOCAL),
}
AUDIO_RE = re.compile(r"^(\d{8}-\d{6})(-[a-z0-9]{1,8})?\.(m4a|mp4|aac|wav|mp3|webm)$")
MIN_AGE_S = 20          # soubor musí být aspoň tak dlouho beze změny (dosync z Drive)
MAX_ATTEMPTS = 3
SUMMARY_BACKOFF_MIN = [10, 60, 360, 1440, 1440]   # čekání po n-tém neúspěšném shrnutí (výpadek, limit předplatného)
MAX_SUMMARY_ATTEMPTS = len(SUMMARY_BACKOFF_MIN) + 1
PENDING_MARK = "_Čeká na shrnutí._"   # dokud je v poznámce, worker ji smí přepsat/přesunout (Frank ji needitoval)
DOMAINS = {  # složka → hub stránka domény (None = hub neexistuje)
    "ae": "acoustic-empire",
    "zlin-city": "zlin-city-arch-moderna",
    "osobni": None,
    "nezarazeno": None,
}
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["domain", "title", "slug", "summary", "tasks", "tags", "related"],
    "properties": {
        "domain": {"type": "string", "enum": list(DOMAINS)},
        "title": {"type": "string", "maxLength": 90},
        "slug": {"type": "string", "maxLength": 50},
        "summary": {"type": "string"},
        "tasks": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "related": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
}
SYSTEM_PROMPT = """Zpracováváš přepisy hlasových poznámek Franka Sehnala (Praha) do jeho Obsidian vaultu.
Přepis je automatický (Whisper), může obsahovat chyby v rozpoznání – vykládej ho podle smyslu, nic nevymýšlej.

Domény (pole domain):
- ae – Acoustic Empire s.r.o. a další pracovní věci: akustické produkty (absorbéry, difuzory, BoomMount, Feel Fader),
  akustické poradenství a měření, zakázky, zákazníci, výroba, 3D tisk, Fusion 360, Max for Live, web, spoluzakladatel Ivan, SilentLab.
- zlin-city – herní projekt Zlín City: Arch Moderna (Unreal Engine 5): zvukový design, Sound Sety, MetaSounds, hudba do hry, tým hry.
- osobni – osobní život: rodina, zdraví, domácnost, chytrá domácnost (Home Assistant), vlastní hudba, koníčky, nápady mimo práci.
- nezarazeno – jen když z obsahu opravdu nejde rozhodnout.

Výstup:
- title: výstižný český název poznámky (bez data), max ~70 znaků.
- slug: krátký anglický nebo český kebab-case bez diakritiky, 2–5 slov (např. absorber-supplier-call).
- summary: 1–4 věty česky, věcně, co je podstatné.
- tasks: konkrétní úkoly/akce, které z poznámky plynou (česky, rozkazovací způsob); prázdné pole, když žádné nejsou.
- tags: 0–5 tagů v kebab-case (bez voice-note a bez názvu domény).
- related: 0–6 názvů stránek vaultu, které s obsahem přímo souvisí – POUZE přesné názvy ze seznamu, nic jiného."""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
log = logging.getLogger("voice_inbox")


# ---------- infrastruktura ----------

def setup_logging(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(state_dir / "worker.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    if sys.stderr is not None and sys.stderr.isatty():
        log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


def acquire_mutex():
    """Named mutex – druhá instance hned skončí. Handle se drží do konce procesu."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, "Local\\voice_inbox_worker")
    if not h or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return h


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, creationflags=NO_WINDOW, **kw)


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    os.replace(tmp, path)


class State:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, dict] = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def get(self, rid: str) -> dict:
        return self.items.setdefault(rid, {"status": "new", "attempts": 0})

    def save(self) -> None:
        atomic_write(self.path, json.dumps(self.items, ensure_ascii=False, indent=1))

    def set(self, rid: str, **kw) -> None:
        it = self.get(rid)
        it.update(kw, updated=dt.datetime.now().isoformat(timespec="seconds"))
        self.save()
        crash = os.environ.get("VOICE_INBOX_CRASH_AFTER")
        if crash and kw.get("status") == crash:
            log.warning("%s: simulovaný pád po stavu %s", rid, crash)
            os._exit(3)


# ---------- kroky ----------

def probe(path: Path) -> float:
    """Vrátí délku v s; výjimka, pokud soubor není čitelné audio."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "format=duration:stream=codec_name",
             "-of", "json", str(path)], timeout=60)
    if r.returncode != 0:
        raise RuntimeError("ffprobe: " + r.stderr.decode("utf-8", "replace").strip()[:300])
    info = json.loads(r.stdout or b"{}")
    if not info.get("streams"):
        raise RuntimeError("ffprobe: žádná audio stopa")
    dur = float(info.get("format", {}).get("duration") or 0)
    if dur <= 0:
        # webm z MediaRecorderu často nemá duration v hlavičce → dopočítat dekódováním
        r = run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-", "-stats"], timeout=600)
        m = re.findall(rb"time=(\d+):(\d+):(\d+\.\d+)", r.stderr)
        if r.returncode != 0 or not m:
            raise RuntimeError("ffmpeg: nelze dekódovat")
        h, mi, s = m[-1]
        dur = int(h) * 3600 + int(mi) * 60 + float(s)
    return dur


_model = None


def _cuda_dll_paths() -> None:
    roots = set(site.getsitepackages() + [site.getusersitepackages()])
    for root in roots:
        for b in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            os.add_dll_directory(b)
            os.environ["PATH"] = b + os.pathsep + os.environ["PATH"]


def transcribe(path: Path) -> str:
    fake = os.environ.get("VOICE_INBOX_FAKE_TRANSCRIPT")
    if fake is not None:
        return fake
    global _model
    import numpy as np
    if _model is None:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        _cuda_dll_paths()
        from faster_whisper import WhisperModel
        t = time.time()
        _model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        log.info("model large-v3 načten za %.1f s", time.time() - t)
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
            timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg dekód: " + r.stderr.decode("utf-8", "replace").strip()[:300])
    audio = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    segments, _ = _model.transcribe(audio, language="cs", vad_filter=True)
    return "\n".join(s.text.strip() for s in segments if s.text.strip())


def rid_time(rid: str) -> dt.datetime:
    return dt.datetime.strptime(rid[:15], "%Y%m%d-%H%M%S")


def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def find_note(vault: Path, rid: str, exclude: Path | None = None) -> Path | None:
    """Poznámka k nahrávce se hledá podle recording_id ve frontmatteru (slug a složka nejsou předem známé)."""
    t = rid_time(rid)
    for p in vault.glob(f"*/{t:%Y-%m-%d-%H%M}*.md"):
        if exclude and p == exclude:
            continue
        head = p.read_text("utf-8", errors="replace")[:1500]
        if re.search(rf"^recording_id: {re.escape(rid)}\s*$", head, re.M):
            return p
    return None


def claude_exe() -> str:
    if os.environ.get("VOICE_INBOX_CLAUDE"):
        return os.environ["VOICE_INBOX_CLAUDE"]
    npm = Path(os.environ.get("APPDATA", "")) / "npm"
    return str(npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")


def vault_pages(wiki: Path) -> list[str]:
    voice = wiki / "sources" / "voice"
    return sorted({p.stem for p in wiki.rglob("*.md") if voice not in p.parents and not p.name.startswith(".")})


def slugify(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", s)[:6])[:50].strip("-") or "voice-note"


def summarize(transcript: str, pages: list[str], cwd: Path) -> dict | None:
    """claude -p bez nástrojů, 1 dotaz, strukturovaný výstup. None = selhalo (poznámka zůstane needs-summary)."""
    fake = os.environ.get("VOICE_INBOX_FAKE_SUMMARY")
    if fake is not None:
        if fake == "FAIL":
            log.warning("summarize: simulované selhání")
            return None
        out = json.loads(fake)
    else:
        prompt = ("Existující stránky vaultu (pro pole related):\n" + ", ".join(pages) +
                  "\n\n=== PŘEPIS HLASOVÉ POZNÁMKY ===\n" + transcript)
        cmd = [claude_exe(), "-p", "--tools", "", "--strict-mcp-config", "--setting-sources", "project",
               "--no-session-persistence", "--model", "sonnet", "--output-format", "json",
               "--json-schema", json.dumps(SUMMARY_SCHEMA), "--system-prompt", SYSTEM_PROMPT]
        try:
            r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=300,
                               cwd=str(cwd), creationflags=NO_WINDOW)
            res = json.loads(r.stdout.decode("utf-8"))
            out = res.get("structured_output")
            if r.returncode != 0 or res.get("is_error") or not isinstance(out, dict):
                raise RuntimeError(f"rc={r.returncode} {str(res.get('result'))[:200]} {r.stderr[:200]!r}")
        except Exception as e:  # noqa: BLE001
            log.warning("summarize selhal: %s: %s", type(e).__name__, str(e)[:300])
            return None
    # validace – výstup modelu nebereme naslepo
    page_set = set(pages)
    dom = out.get("domain") if out.get("domain") in DOMAINS else "nezarazeno"
    slug = out.get("slug", "")
    slug = slug if SLUG_RE.match(slug) and len(slug) <= 50 else slugify(out.get("title") or "")
    rel = [p for p in dict.fromkeys(str(x).strip("[] ") for x in out.get("related", [])) if p in page_set]
    hub = DOMAINS[dom]
    if hub and hub in page_set and hub not in rel:
        rel.insert(0, hub)
    tags = [t for t in dict.fromkeys(slugify(str(x)) for x in out.get("tags", [])) if t not in ("voice-note", dom)][:5]
    return {
        "domain": dom, "slug": slug, "related": rel, "tags": tags,
        "title": (str(out.get("title") or "").strip().replace('"', "'") or "Hlasová poznámka")[:90],
        "summary": str(out.get("summary") or "").strip(),
        "tasks": [str(x).strip() for x in out.get("tasks", []) if str(x).strip()],
    }


def render_note(rid: str, transcript: str, duration: float, audio_rel: str, sm: dict | None, created: str) -> str:
    t = rid_time(rid)
    when = f"{t:%Y-%m-%d %H:%M}"
    body = transcript.strip() or "_(ticho – řeč nerozpoznána)_"
    if sm:
        title, dom, status = sm["title"], sm["domain"], "summarized"
        summary = sm["summary"] or "–"
        tasks = "\n".join(f"- [ ] {x}" for x in sm["tasks"])
        tags = ", ".join(["voice-note", dom] + sm["tags"])
        related = "\n".join(f"- [[{p}]]" for p in sm["related"])
    else:
        title, dom = f"Hlasová poznámka {when}", "nezarazeno"
        status = "needs-summary" if transcript.strip() else "no-speech"
        summary = PENDING_MARK if transcript.strip() else "_Bez řeči._"
        tasks, tags, related = "", "voice-note, nezarazeno", ""
    return f"""---
title: "{title}"
type: source
tags: [{tags}]
created: {created}
updated: {dt.date.today().isoformat()}
recording_id: {rid}
recorded: {t:%Y-%m-%dT%H:%M:%S}
duration: "{fmt_dur(duration)}"
audio: "{audio_rel}"
status: {status}
---

# {title}

*Hlasová poznámka {when} · {fmt_dur(duration)}*

## Shrnutí

{summary}

## Úkoly

{tasks or "–"}

## Přepis

{body}

## Related

{related}
""".replace("\n\n\n", "\n\n")


def note_path(vault: Path, rid: str, sm: dict | None) -> Path:
    t = rid_time(rid)
    dom, slug = (sm["domain"], sm["slug"]) if sm else ("nezarazeno", "voice-note")
    path = vault / dom / f"{t:%Y-%m-%d-%H%M}-{slug}.md"
    if path.exists():  # dvě nahrávky ve stejné minutě se stejným slugem
        path = path.with_name(f"{t:%Y-%m-%d-%H%M%S}-{slug}.md")
    return path


def write_note(vault: Path, rid: str, transcript: str, duration: float, audio_rel: str, sm: dict | None) -> Path:
    existing = find_note(vault, rid)
    if existing:
        return existing
    path = note_path(vault, rid, sm)
    atomic_write(path, render_note(rid, transcript, duration, audio_rel, sm, dt.date.today().isoformat()))
    return path


def summary_failed(st: State, rid: str, it: dict) -> None:
    n = it.get("summary_attempts", 0) + 1
    wait = SUMMARY_BACKOFF_MIN[min(n, len(SUMMARY_BACKOFF_MIN)) - 1]
    nxt = (dt.datetime.now() + dt.timedelta(minutes=wait)).isoformat(timespec="seconds")
    st.set(rid, summary_attempts=n, summary_next=nxt)
    log.info("%s: shrnutí pokus %d/%d nevyšel, další po %s", rid, n, MAX_SUMMARY_ATTEMPTS, nxt)


def summary_due(it: dict) -> bool:
    return (it.get("status") in ("noted", "archived") and it.get("note_status") == "needs-summary"
            and it.get("summary_attempts", 0) < MAX_SUMMARY_ATTEMPTS
            and dt.datetime.now().isoformat(timespec="seconds") >= (it.get("summary_next") or ""))


def resummarize(rid: str, it: dict, st: State, a: argparse.Namespace, pages: list[str]) -> None:
    """Poznámka ve stavu needs-summary → nový pokus o shrnutí; při úspěchu ji přesune do domény."""
    old = Path(it["note"])
    if not old.exists() or PENDING_MARK not in old.read_text("utf-8"):
        st.set(rid, note_status="manual")  # smazaná nebo ručně upravená – nesahat
        log.info("%s: poznámka needs-summary chybí/upravena ručně – přeskočeno", rid)
        return
    done = find_note(Path(a.vault), rid, exclude=old)
    if done:  # pád mezi zápisem nové poznámky a smazáním staré
        old.unlink()
        st.set(rid, note=str(done), note_status="summarized")
        log.info("%s: dokončen přesun → %s", rid, done)
        return
    text = (Path(a.state_dir) / "transcripts" / f"{rid}.txt").read_text("utf-8")
    sm = summarize(text, pages, Path(a.state_dir))
    if not sm:
        summary_failed(st, rid, it)
        return
    created = (re.search(r"^created: (\S+)", old.read_text("utf-8"), re.M) or [None, dt.date.today().isoformat()])[1]
    target = Path(it["archive"])
    rel = "voice_inbox/processed/" + target.parent.name + "/" + target.name
    new = note_path(Path(a.vault), rid, sm)
    atomic_write(new, render_note(rid, text, it["duration"], rel, sm, created))
    old.unlink()
    st.set(rid, note=str(new), note_status="summarized", domain=sm["domain"])
    log.info("%s: dosumarizováno → %s", rid, new)


def archive_target(processed: Path, rid: str, name: str) -> Path:
    d = processed / rid_time(rid).strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    target, n = d / name, 1
    while target.exists():
        stem, ext = os.path.splitext(name)
        target, n = d / f"{stem}-dup{n}{ext}", n + 1
    return target


# ---------- hlavní smyčka ----------

def process(src: Path, rid: str, st: State, a: argparse.Namespace, tmpdir: Path, pages) -> None:
    it = st.get(rid)
    if it["status"] == "failed":
        return
    if it["status"] == "archived":
        # opakovaný upload téže nahrávky (appka po timeoutu poslala znovu) → jen archivovat vedle, nic nemazat
        target = archive_target(Path(a.processed), rid, src.name)
        shutil.move(str(src), str(target))
        log.warning("%s: znovu nahraný duplikát → %s (bez nové poznámky)", rid, target)
        return
    try:
        if it["status"] == "new":
            age = time.time() - src.stat().st_mtime
            if age < MIN_AGE_S:
                log.info("%s: čerstvý soubor (%.0f s), počkám na další běh", rid, age)
                return
            local = tmpdir / src.name
            shutil.copy2(src, local)
            dur = probe(local)
            log.info("%s: %s, %s, přepisuji…", rid, src.name, fmt_dur(dur))
            t = time.time()
            text = transcribe(local)
            atomic_write(Path(a.state_dir) / "transcripts" / f"{rid}.txt", text)
            st.set(rid, status="transcribed", src=src.name, duration=dur, chars=len(text), attempts=0,
                   transcribe_s=round(time.time() - t, 1))
            log.info("%s: přepsáno (%d znaků, %.1f s)", rid, len(text), time.time() - t)
            it = st.get(rid)
        if it["status"] == "transcribed":
            target = Path(it["archive"]) if it.get("archive") else archive_target(Path(a.processed), rid, src.name)
            st.set(rid, archive=str(target))
            text = (Path(a.state_dir) / "transcripts" / f"{rid}.txt").read_text("utf-8")
            rel = "voice_inbox/processed/" + target.parent.name + "/" + target.name
            note = find_note(Path(a.vault), rid)
            sm = None
            if not note and text.strip():
                sm = summarize(text, pages(), Path(a.state_dir))
            note = note or write_note(Path(a.vault), rid, text, it["duration"], rel, sm)
            ns = "summarized" if sm else ("needs-summary" if PENDING_MARK in note.read_text("utf-8") else "done")
            st.set(rid, status="noted", note=str(note), note_status=ns, domain=sm["domain"] if sm else None)
            if ns == "needs-summary" and not it.get("summary_attempts"):
                summary_failed(st, rid, it)
            log.info("%s: poznámka %s (%s)", rid, note, ns)
            it = st.get(rid)
        if it["status"] == "noted":
            target = Path(it["archive"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            st.set(rid, status="archived")
            log.info("%s: audio → %s", rid, target)
    except Exception as e:  # noqa: BLE001 – každá chyba = pokus; po MAX_ATTEMPTS stav failed
        n = it.get("attempts", 0) + 1
        status = "failed" if n >= MAX_ATTEMPTS else it["status"]
        st.set(rid, attempts=n, error=f"{type(e).__name__}: {e}"[:500], status=status)
        log.error("%s: pokus %d/%d selhal ve stavu %s: %s", rid, n, MAX_ATTEMPTS, it["status"], e)


def main() -> int:
    p = argparse.ArgumentParser()
    for k, v in DEFAULTS.items():
        p.add_argument("--" + k.replace("_", "-"), default=v)
    p.add_argument("--retry-failed", action="store_true", help="vrátí failed položky do hry")
    a = p.parse_args()
    setup_logging(Path(a.state_dir))
    try:
        if not acquire_mutex():
            log.info("jiná instance běží – konec")
            return 0
        inc = Path(a.incoming)
        if not inc.is_dir():
            log.warning("incoming %s není dostupné (G: nepřipojený?) – konec", inc)
            return 0
        st = State(Path(a.state_dir) / "state.json")
        if a.retry_failed:
            for rid, it in st.items.items():
                if it["status"] == "failed":
                    it.update(status="transcribed" if (Path(a.state_dir) / "transcripts" / f"{rid}.txt").exists()
                              else "new", attempts=0)
                if it.get("note_status") == "needs-summary":
                    it.update(summary_attempts=0, summary_next=None)
            st.save()
        cache: list = []

        def pages() -> list[str]:  # seznam stránek vaultu jen když je co shrnovat
            if not cache:
                cache.append(vault_pages(Path(a.wiki)))
            return cache[0]

        files = sorted(f for f in inc.iterdir() if f.is_file() and AUDIO_RE.match(f.name))
        touched = set()  # co tento běh už zkusil shrnout, se v tomtéž běhu znovu nezkouší
        if files:
            with tempfile.TemporaryDirectory(prefix="vi_", dir=Path(a.state_dir)) as tmp:
                for f in files:
                    m = AUDIO_RE.match(f.name)
                    touched.add(m.group(1) + (m.group(2) or ""))
                    process(f, m.group(1) + (m.group(2) or ""), st, a, Path(tmp), pages)
        for rid, it in list(st.items.items()):
            if rid not in touched and summary_due(it):
                try:
                    resummarize(rid, it, st, a, pages())
                except Exception as e:  # noqa: BLE001
                    log.error("%s: dosumarizování selhalo: %s", rid, e)
                    summary_failed(st, rid, it)
        return 0
    except Exception:  # noqa: BLE001
        log.exception("neošetřená chyba")
        return 1


if __name__ == "__main__":
    sys.exit(main())
