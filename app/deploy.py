"""Deploy PWA na Cloudflare Pages (projekt voice-inbox) + ověření sha256 live vs. lokál.
Spuštění: python deploy.py   → poslední řádek: DEPLOY OK n/n | DEPLOY FAIL …"""
import hashlib, os, shutil, subprocess, sys, tempfile, time, urllib.request, winreg
from pathlib import Path

APP = Path(__file__).parent
URL = "https://voice-inbox-5l6.pages.dev/"
FILES = ["index.html", "manifest.webmanifest", "sw.js", "icon-180.png", "icon-192.png", "icon-512.png"] + \
        ["fonts/" + p.name for p in sorted((APP / "fonts").glob("*.woff2"))]

env = dict(os.environ)
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
    for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        if not env.get(name):
            env[name] = winreg.QueryValueEx(k, name)[0]

stage = Path(tempfile.mkdtemp(prefix="vi_deploy_"))
for f in FILES:
    (stage / f).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(APP / f, stage / f)
r = subprocess.run(f'npx --yes wrangler@4.162.0 pages deploy "{stage}" --project-name voice-inbox --branch main '
                   '--commit-dirty=true', shell=True, env=env, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", stdin=subprocess.DEVNULL)
print(r.stdout[-1500:], r.stderr[-1500:])
if r.returncode != 0:
    print("DEPLOY FAIL wrangler rc", r.returncode); sys.exit(1)

ok, bad = 0, []
for attempt in range(6):  # propagace na CDN
    ok, bad = 0, []
    for f in FILES:
        req = urllib.request.Request(URL + f + f"?v={time.time()}", headers={"Cache-Control": "no-cache", "User-Agent": "deploy-check"})
        live = hashlib.sha256(urllib.request.urlopen(req, timeout=30).read()).hexdigest()
        loc = hashlib.sha256((APP / f).read_bytes()).hexdigest()
        ok += live == loc
        if live != loc:
            bad.append(f)
    if not bad:
        break
    time.sleep(10)
shutil.rmtree(stage, ignore_errors=True)
print(f"DEPLOY OK {ok}/{len(FILES)}" if not bad else f"DEPLOY FAIL sha mismatch: {bad}")
