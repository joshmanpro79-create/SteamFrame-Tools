#!/usr/bin/env python3
# Frame Automations for Steam Frame
# Copyright (c) 2026 joshmanpro79-create. Released under the MIT License.
"""Time-based automations for Steam Frame: play a sound, speak a phrase or run
a script a set time after Boot, after Wake, or at a Clock time.

User-level only: no sudo, no Developer Mode, nothing outside your home folder.
Run `frame-automations help` for the commands.
"""

import datetime as dt
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # Python < 3.9
    ZoneInfo = None

    def available_timezones():
        return set()

APP = "frame-automations"
VERSION = "1.0.0"
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / APP
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / APP
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
STOP_FILE = STATE_DIR / "stop-request"
PID_FILE = STATE_DIR / "service.pid"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / APP
WINDOW_PY_FILE = DATA_DIR / "window-python"

AUDIO_NATIVE = {".wav", ".ogg", ".oga", ".flac"}
AUDIO_DECODE = {".mp3", ".m4a", ".mp4", ".aac", ".opus", ".webm", ".wma"}
SCRIPT_EXT = {".sh", ".bash", ".py"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TRIGGERS = ("boot", "wake", "clock")
SCHEDULES = ("daily", "once", "custom")
MAX_DELAY = 120 * 60
EVENT_LOG_KEEP_DAYS = 30

TEMPLATE = {
    "name": "",
    "enabled": True,
    "trigger": "clock",        # boot | wake | clock
    "delay_seconds": 120,      # boot / wake
    "time": "14:00",           # clock, 24-hour
    "schedule": "daily",       # daily | once | custom
    "days": [0, 1, 2, 3, 4],   # custom: 0 = Monday
    "date": "",                # clock + once: YYYY-MM-DD
    "action": "file",          # file | speak
    "file": "",
    "text": "",
    "repeat_count": 1,         # 1-10 plays, 0 = loop until stopped
    "log_keep_days": 30,       # scripts: 0 = Do Not Delete, 1-30 days
}
DEFAULTS = {
    "enabled": True,
    "timezone": "",            # "" = system time zone
    "missed_grace_minutes": 10,
    "min_sleep_seconds": 30,
    "automations": [],
}


# --------------------------------------------------------------------------
# Config and state
# --------------------------------------------------------------------------

def _copy(obj):
    return json.loads(json.dumps(obj))


def new_automation(**kw):
    a = _copy(TEMPLATE)
    a["id"] = uuid.uuid4().hex[:8]
    a.update(kw)
    return a


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def normalize(a):
    out = _copy(TEMPLATE)
    if isinstance(a, dict):
        out.update(a)
    out["id"] = str(out.get("id") or uuid.uuid4().hex[:8])
    out["name"] = str(out.get("name") or "")
    out["enabled"] = bool(out.get("enabled"))
    out["trigger"] = out["trigger"] if out.get("trigger") in TRIGGERS else "clock"
    out["schedule"] = out["schedule"] if out.get("schedule") in SCHEDULES else "daily"
    out["action"] = out["action"] if out.get("action") in ("file", "speak") else "file"
    out["delay_seconds"] = _int(out.get("delay_seconds"), 120, 0, MAX_DELAY)
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(out.get("time") or ""))
    out["time"] = (f"{min(int(m.group(1)), 23):02d}:{min(int(m.group(2)), 59):02d}"
                   if m else "14:00")
    days = set()
    for d in out.get("days") or []:
        try:
            if 0 <= int(d) <= 6:
                days.add(int(d))
        except (TypeError, ValueError):
            pass
    out["days"] = sorted(days)
    out["date"] = str(out.get("date") or "")
    out["file"] = str(out.get("file") or "")
    out["text"] = str(out.get("text") or "")
    out["repeat_count"] = _int(out.get("repeat_count"), 1, 0, 10)
    out["log_keep_days"] = _int(out.get("log_keep_days"), 30, 0, 30)
    return out


def load_config():
    cfg = _copy(DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"Settings file unreadable ({e}); using defaults.", file=sys.stderr)
    cfg["automations"] = [normalize(a) for a in (cfg.get("automations") or [])
                          if isinstance(a, dict)]
    return cfg


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def save_config(cfg):
    atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2) + "\n")


def config_mtime():
    try:
        return CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        return 0


def load_state():
    try:
        s = json.loads(STATE_FILE.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(s):
    atomic_write(STATE_FILE, json.dumps(s, indent=1) + "\n")


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def system_tz_name():
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name:
            return name
    except OSError:
        pass
    return os.environ.get("TZ", "").lstrip(":")


def get_tz(cfg):
    for name in ((cfg or {}).get("timezone") or "", system_tz_name()):
        if name and ZoneInfo:
            try:
                return ZoneInfo(name)
            except Exception:
                pass
    return dt.datetime.now().astimezone().tzinfo


def now_in(cfg):
    return dt.datetime.now(get_tz(cfg))


def mono():
    """Seconds since boot, not counting time asleep."""
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def boottime():
    """Seconds since boot, including time asleep."""
    return time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def fmt12(hhmm):
    h, m = map(int, hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def fmt_duration(seconds):
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {sec} s" if sec else f"{m} min"
    return f"{sec} s"


def describe(a):
    if a["trigger"] == "clock":
        when = f"Clock {fmt12(a['time'])}"
    else:
        when = f"{a['trigger'].title()} + {fmt_duration(a['delay_seconds'])}"
    if a["schedule"] == "custom":
        rep = ",".join(DAYS[d] for d in a["days"]) or "no days"
    elif a["schedule"] == "once":
        rep = f"once {a['date']}" if a["trigger"] == "clock" and a["date"] else "once"
    else:
        rep = "daily"
    return f"{when}, {rep}"


def label(a):
    if a.get("name"):
        return a["name"]
    if a.get("action") == "speak" and a.get("text"):
        t = a["text"].strip()
        return f"“{t[:30]}{'…' if len(t) > 30 else ''}”"
    if a.get("file"):
        return Path(a["file"]).name
    return "Untitled automation"


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in AUDIO_NATIVE or ext in AUDIO_DECODE:
        return "audio"
    if ext in SCRIPT_EXT:
        return "script"
    try:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return "script"
    except OSError:
        pass
    return "unknown"


# --------------------------------------------------------------------------
# Tools: player, decoder, speech
# --------------------------------------------------------------------------

def find_player():
    for p in ("pw-play", "paplay", "aplay"):
        w = shutil.which(p)
        if w:
            return w
    return None


def find_ffmpeg():
    local = DATA_DIR / "bin" / "ffmpeg"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("ffmpeg")


_piper_cache = {}


def find_piper(cfg=None):
    """Find a Piper binary and voice, preferring the one Battery Announce installed."""
    cfg = cfg or {}
    if "result" in _piper_cache:
        return _piper_cache["result"]
    piper, voice = cfg.get("piper_path"), cfg.get("voice_path")
    roots = [str(DATA_DIR), str(HOME / ".local/share/battery-announce"),
             str(HOME / ".local/share"), str(HOME / ".local/opt")]
    depths = ("", "*/", "*/*/", "*/*/*/")
    if not (piper and os.path.isfile(piper) and os.access(piper, os.X_OK)):
        piper = None
        for r in roots:
            for d in depths:
                for p in sorted(glob.glob(f"{r}/{d}piper")):
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        piper = p
                        break
                if piper:
                    break
            if piper:
                break
        piper = piper or shutil.which("piper")
    if not (voice and os.path.isfile(voice)):
        cands = []
        for r in roots:
            for d in depths:
                cands += [c for c in glob.glob(f"{r}/{d}*.onnx") if os.path.isfile(c + ".json")]
        cands = sorted(set(cands), key=lambda c: (0 if "lessac" in c else 1, c))
        voice = cands[0] if cands else None
    result = (piper, voice) if piper and voice else (None, None)
    _piper_cache["result"] = result
    return result


def speech_engine(cfg=None):
    piper, voice = find_piper(cfg)
    if piper:
        return f"Piper ({Path(voice).name})"
    for tool in ("espeak-ng", "espeak"):
        if shutil.which(tool):
            return tool
    return None


def synth_speech(text, out, cfg=None):
    out = Path(out)
    piper, voice = find_piper(cfg)
    if piper:
        try:
            r = subprocess.run([piper, "--model", voice, "--output_file", str(out)],
                               input=text.encode(), cwd=os.path.dirname(piper),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            if r.returncode == 0 and out.exists() and out.stat().st_size > 44:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    for tool in ("espeak-ng", "espeak"):
        w = shutil.which(tool)
        if w:
            try:
                r = subprocess.run([w, "-w", str(out), text], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=60)
                if r.returncode == 0 and out.exists():
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
    return False


def playable_file(path, player):
    """Return a file the player can handle, decoding MP3/M4A etc. to a cached WAV."""
    ext = Path(path).suffix.lower()
    needs = ext in AUDIO_DECODE or (os.path.basename(player) == "aplay" and ext != ".wav")
    if not needs:
        return path
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError(f"{Path(path).name} needs MP3/M4A support. Install it from the "
                           "Frame Automations window, or run: frame-automations install-decoder")
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"decoded-{key}.wav"
    if out.exists():
        os.utime(out)
        return str(out)
    tmp = CACHE_DIR / f"decoded-{key}.part.wav"
    r = subprocess.run([ff, "-y", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", "48000", str(tmp)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
    if r.returncode != 0 or not tmp.exists():
        err = r.stderr.decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"Couldn't decode {Path(path).name}: {err}")
    os.replace(tmp, out)
    return str(out)


FFMPEG_URLS = {
    "arm64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz",
    "amd64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
}


def install_decoder(progress=print):
    """Download a static ffmpeg into ~/.local/share/frame-automations/bin."""
    import tarfile
    import urllib.request
    machine = os.uname().machine.lower()
    arch = ("arm64" if machine in ("aarch64", "arm64") else
            "amd64" if machine in ("x86_64", "amd64") else None)
    if not arch:
        raise RuntimeError(f"No MP3/M4A decoder download for this processor ({machine}).")
    dest = DATA_DIR / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    mb = 1024 * 1024
    with tempfile.TemporaryDirectory() as tmpd:
        archive = Path(tmpd) / "ffmpeg.tar.xz"
        progress("Downloading MP3/M4A support (about 40 MB)…")
        req = urllib.request.Request(FFMPEG_URLS[arch], headers={"User-Agent": f"{APP}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = last = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done - last >= 5 * mb:
                    last = done
                    progress(f"Downloading… {done / mb:.0f} MB" + (f" of {total / mb:.0f} MB" if total else ""))
        progress("Unpacking…")
        with tarfile.open(archive, "r:xz") as t:
            member = next((m for m in t.getmembers()
                           if m.isfile() and m.name.rsplit("/", 1)[-1] == "ffmpeg"), None)
            if not member:
                raise RuntimeError("The download didn't contain ffmpeg.")
            tmp = dest / "ffmpeg.part"
            with t.extractfile(member) as src, open(tmp, "wb") as f:
                shutil.copyfileobj(src, f)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest / "ffmpeg")
    r = subprocess.run([str(dest / "ffmpeg"), "-version"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg was downloaded but won't run on this system.")
    progress("MP3/M4A support installed.")
    return str(dest / "ffmpeg")


# --------------------------------------------------------------------------
# Battery Announce coordination and desktop environment
# --------------------------------------------------------------------------

PLAYER_COMMS = {"piper", "aplay", "paplay", "pw-play", "pw-cat", "ffplay", "mpv", "play", "sox",
                "espeak-ng", "espeak"}


def _proc_table():
    procs = {}
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            with open(f"/proc/{e}/stat") as f:
                stat = f.read()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
            procs[int(e)] = (comm, ppid)
        except (OSError, ValueError, IndexError):
            pass
    return procs


def battery_announce_busy():
    """True while a Battery Announce process is speaking (has a player/piper child)."""
    try:
        procs = _proc_table()
    except OSError:
        return False
    me = os.getpid()
    roots = set()
    for pid in procs:
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read()
        except OSError:
            continue
        if b"battery-announce" in cmd and APP.encode() not in cmd:
            roots.add(pid)
    if not roots:
        return False
    children = {}
    for pid, (_, pp) in procs.items():
        children.setdefault(pp, []).append(pid)
    stack, seen = list(roots), set()
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch in seen:
                continue
            seen.add(ch)
            if procs[ch][0] in PLAYER_COMMS:
                return True
            stack.append(ch)
    return False


ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "PULSE_SERVER")


def _environ_of(names):
    uid = os.getuid()
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            if os.stat(f"/proc/{e}").st_uid != uid:
                continue
            with open(f"/proc/{e}/comm") as f:
                if f.read().strip() not in names:
                    continue
            with open(f"/proc/{e}/environ", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        env = {}
        for kv in raw.split(b"\0"):
            if b"=" in kv:
                k, v = kv.split(b"=", 1)
                env[k.decode(errors="replace")] = v.decode(errors="replace")
        if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
            return env
    return {}


def desktop_env():
    """Environment for scripts, borrowed from the running desktop or Steam session so
    scripts can open windows in Desktop Mode or Game Mode."""
    env = os.environ.copy()
    src = (_environ_of({"plasmashell", "kwin_wayland", "kwin_x11"})
           or _environ_of({"steam", "gamescope", "gamescope-session"}))
    for k in ENV_KEYS:
        if src.get(k):
            env[k] = src[k]
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        env["DISPLAY"] = ":0"
    return env


# --------------------------------------------------------------------------
# Audio queue
# --------------------------------------------------------------------------

class AudioPlayer:
    """Plays one thing at a time; waits while Battery Announce is speaking."""

    def __init__(self, log, cfg_getter):
        self.log = log
        self.cfg_getter = cfg_getter
        self.q = queue.Queue()
        self.gen = 0
        self.proc = None
        self.active = False
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    def add(self, kind, value, repeat, name):
        with self.lock:
            self.q.put((self.gen, kind, value, repeat, name))

    def busy(self):
        return self.active or not self.q.empty()

    def stop(self):
        with self.lock:
            self.gen += 1
            drained = 0
            while True:
                try:
                    self.q.get_nowait()
                    drained += 1
                except queue.Empty:
                    break
            proc = self.proc
        stopped = False
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(1)
            except subprocess.TimeoutExpired:
                proc.kill()
            stopped = True
        return stopped or drained > 0

    def _prepare(self, kind, value):
        player = find_player()
        if not player:
            raise RuntimeError("No audio player found (pw-play, paplay or aplay).")
        if kind == "speak":
            cfg = self.cfg_getter()
            key = hashlib.sha1(f"{value}|{find_piper(cfg)}".encode()).hexdigest()[:16]
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            out = CACHE_DIR / f"speak-{key}.wav"
            if not out.exists():
                tmp = CACHE_DIR / f"speak-{key}.part.wav"
                if not synth_speech(value, tmp, cfg):
                    raise RuntimeError("No speech voice found. Battery Announce provides the "
                                       "Piper voice; espeak-ng also works.")
                os.replace(tmp, out)
            else:
                os.utime(out)
            return player, str(out)
        return player, playable_file(value, player)

    def _worker(self):
        while True:
            gen, kind, value, repeat, name = self.q.get()
            if gen != self.gen:
                continue
            self.active = True
            try:
                try:
                    player, path = self._prepare(kind, value)
                except Exception as e:
                    self.log(f"Can't play {name}: {e}")
                    continue
                n = 0
                while gen == self.gen and (repeat == 0 or n < repeat):
                    waited = 0.0
                    while gen == self.gen and waited < 60 and battery_announce_busy():
                        time.sleep(0.25)
                        waited += 0.25
                    with self.lock:
                        if gen != self.gen:
                            break
                        try:
                            self.proc = subprocess.Popen([player, path], stdin=subprocess.DEVNULL,
                                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        except OSError as e:
                            self.log(f"Can't play {name}: {e}")
                            break
                        proc = self.proc
                    _, err = proc.communicate()
                    if proc.returncode != 0 and gen == self.gen:
                        self.log(f"Player error for {name}: {err.decode(errors='replace').strip()[-200:]}")
                        break
                    n += 1
            finally:
                with self.lock:
                    self.proc = None
                self.active = False


# --------------------------------------------------------------------------
# Scripts and logs
# --------------------------------------------------------------------------

def script_command(path):
    ext = Path(path).suffix.lower()
    if ext in (".sh", ".bash"):
        return [shutil.which("bash") or "/bin/bash", path]
    if ext == ".py":
        return [shutil.which("python3") or sys.executable, path]
    return [path]


def script_log_path(path, cfg, when=None):
    when = when or now_in(cfg)
    return LOG_DIR / f"{when:%Y-%m-%d}_{Path(path).name}.log"


def run_script(path, name, cfg, log):
    """Start a script; its output goes to logs/YYYY-MM-DD_<file name>.log.
    Returns (process, log path, waiter thread) or None."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = now_in(cfg)
    logfile = script_log_path(path, cfg, now)
    try:
        f = open(logfile, "a", buffering=1)
    except OSError as e:
        log(f"{name}: can't write log {logfile}: {e}")
        return None
    f.write(f"\n=== {now:%Y-%m-%d %H:%M:%S} · {name} · started ===\n")
    f.flush()
    try:
        proc = subprocess.Popen(script_command(path), cwd=os.path.dirname(path) or None,
                                env=desktop_env(), stdin=subprocess.DEVNULL, stdout=f,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        f.write(f"Could not start: {e}\n")
        f.close()
        log(f"{name}: could not start {Path(path).name}: {e}")
        return None
    start = time.monotonic()

    def waiter():
        rc = proc.wait()
        try:
            f.write(f"=== exit code {rc} after {fmt_duration(time.monotonic() - start)} ===\n")
            f.close()
        except (OSError, ValueError):
            pass
        log(f"{name}: {Path(path).name} finished (exit code {rc}).")

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    return proc, logfile, t


def event_log_path(cfg, when=None):
    when = when or now_in(cfg)
    return LOG_DIR / f"{when:%Y-%m-%d}_{APP}.log"


def make_logger(cfg_getter, echo=True):
    lock = threading.Lock()

    def log(msg):
        cfg = cfg_getter()
        now = now_in(cfg)
        if echo:
            print(msg, flush=True)
        with lock:
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                with open(event_log_path(cfg, now), "a") as f:
                    f.write(f"{now:%Y-%m-%d %H:%M:%S} {msg}\n")
            except OSError:
                pass
    return log


def retention_by_logname(cfg):
    """Map log file name suffix ('_backup.sh.log') -> days to keep (0 = forever).
    When several rows run scripts with the same file name, the longest wins."""
    keep = {f"_{APP}.log": EVENT_LOG_KEEP_DAYS}
    for a in cfg["automations"]:
        if a["action"] != "file" or not a["file"]:
            continue
        suffix = f"_{Path(a['file']).name}.log"
        days = a["log_keep_days"]
        if suffix not in keep:
            keep[suffix] = days
        elif keep[suffix] != 0:
            keep[suffix] = 0 if days == 0 else max(keep[suffix], days)
    return keep


def cleanup_logs(cfg, today=None):
    """Delete script logs older than each row's 'Keep logs' setting. Returns count."""
    today = today or now_in(cfg).date()
    keep = retention_by_logname(cfg)
    removed = 0
    try:
        files = list(LOG_DIR.glob("*.log"))
    except OSError:
        return 0
    for f in files:
        m = re.match(r"(\d{4}-\d{2}-\d{2})(_.+\.log)$", f.name)
        if not m:
            continue
        days = keep.get(m.group(2))
        if not days:  # unknown script (row deleted) or Do Not Delete
            continue
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < today - dt.timedelta(days=days):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def cleanup_cache(max_age_days=30):
    cutoff = time.time() - max_age_days * 86400
    for f in CACHE_DIR.glob("*.wav"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Running an automation's action
# --------------------------------------------------------------------------

def run_action(a, cfg, audio, log, repeat_override=None):
    """Returns 'speak', 'audio', a run_script() tuple, or None on failure."""
    name = label(a)
    repeat = a["repeat_count"] if repeat_override is None else repeat_override
    if a["action"] == "speak":
        text = a["text"].strip()
        if not text:
            log(f"{name}: nothing to say.")
            return None
        log(f"Running {name} ({describe(a)}): speak “{text[:60]}”.")
        audio.add("speak", text, repeat, name)
        return "speak"
    path = os.path.expanduser(a["file"])
    if not path:
        log(f"{name}: no file chosen.")
        return None
    if not os.path.isfile(path):
        log(f"{name}: file not found: {path}")
        return None
    kind = kind_of(path)
    if kind == "audio":
        times = "until stopped" if repeat == 0 else ("once" if repeat == 1 else f"{repeat} times")
        log(f"Running {name} ({describe(a)}): play {Path(path).name} {times}.")
        audio.add("file", path, repeat, name)
        return "audio"
    if kind == "script":
        log(f"Running {name} ({describe(a)}): run {Path(path).name}.")
        return run_script(path, name, cfg, log)
    log(f"{name}: don't know how to run {Path(path).name}. Choose an audio file or a script.")
    return None


# --------------------------------------------------------------------------
# The trigger engine (background service)
# --------------------------------------------------------------------------

TRIGGER_KEYS = ("enabled", "trigger", "delay_seconds", "time", "schedule", "days", "date")


def signature(a):
    return hashlib.sha1(json.dumps({k: a.get(k) for k in TRIGGER_KEYS},
                                   sort_keys=True).encode()).hexdigest()[:12]


class Engine:
    """Decides when Boot, Wake and Clock automations are due.

    Boot:  fires once per boot after `delay_seconds` of awake time (sleep doesn't count).
    Wake:  fires `delay_seconds` after each wake from a sleep of at least min_sleep_seconds;
           sleeping again before then cancels it.
    Clock: fires at the time in the chosen time zone; if the Frame was asleep or off,
           fires on wake/boot if still within missed_grace_minutes, otherwise skipped.
    A row that is created or changed only fires for moments after the change.
    """

    def __init__(self, audio=None, log=None):
        self.cfg = load_config()
        self.mtime = config_mtime()
        self.log = log or make_logger(lambda: self.cfg)
        self.audio = audio or AudioPlayer(self.log, lambda: self.cfg)
        self.bid = boot_id()
        self.state = load_state()
        s = self.state
        if s.get("boot_id") != self.bid:
            s["boot_id"] = self.bid
            s["boot_fired"] = []
        s.setdefault("boot_fired", [])
        s.setdefault("clock_fired", {})
        s.setdefault("armed", {})
        self.dirty = True
        self.last_sleep = boottime() - mono()
        self.wake_mono = None
        self.wake_fired = set()
        self.last_maint = 0.0
        self.rearm()

    # -- bookkeeping ------------------------------------------------------
    def rearm(self):
        armed, ids = self.state["armed"], set()
        for a in self.cfg["automations"]:
            ids.add(a["id"])
            sig = signature(a)
            if armed.get(a["id"], {}).get("sig") != sig:
                armed[a["id"]] = {"sig": sig, "wall": time.time(), "boot_id": self.bid, "mono": mono()}
                self.dirty = True
        for k in list(armed):
            if k not in ids:
                del armed[k]
                self.state["clock_fired"].pop(k, None)
                self.dirty = True

    def armed_after(self, a, mono_point):
        """True if the row was set up during this boot after the moment it would fire."""
        ar = self.state["armed"].get(a["id"], {})
        return ar.get("boot_id") == self.bid and ar.get("mono", 0) > mono_point + 1

    def finish_once(self, a):
        cfg = load_config()
        for b in cfg["automations"]:
            if b["id"] == a["id"]:
                b["enabled"] = False
                b["done_at"] = time.time()
        save_config(cfg)
        self.cfg, self.mtime = cfg, config_mtime()
        self.rearm()
        self.log(f"{label(a)} was a one-time automation; switched it Off.")

    def fire(self, a):
        if not self.cfg.get("enabled", True):
            self.log(f"Skipped {label(a)} ({describe(a)}): automations are Off.")
            return
        run_action(a, self.cfg, self.audio, self.log)

    @staticmethod
    def day_ok(a, now):
        return a["schedule"] != "custom" or now.weekday() in a["days"]

    # -- checks -------------------------------------------------------------
    def _check_boot(self, a, m, now):
        if a["id"] in self.state["boot_fired"] or m < a["delay_seconds"]:
            return
        self.state["boot_fired"].append(a["id"])
        self.dirty = True
        if self.armed_after(a, a["delay_seconds"]) or not self.day_ok(a, now):
            return
        self.fire(a)
        if a["schedule"] == "once":
            self.finish_once(a)

    def _check_wake(self, a, m, now):
        if self.wake_mono is None or a["id"] in self.wake_fired:
            return
        due = self.wake_mono + a["delay_seconds"]
        if m < due:
            return
        self.wake_fired.add(a["id"])
        if self.armed_after(a, due) or not self.day_ok(a, now):
            return
        self.fire(a)
        if a["schedule"] == "once":
            self.finish_once(a)

    def _check_clock(self, a, m, now):
        tz = now.tzinfo
        grace = dt.timedelta(minutes=float(self.cfg.get("missed_grace_minutes", 10)))
        h, mi = map(int, a["time"].split(":"))
        armed_wall = self.state["armed"].get(a["id"], {}).get("wall", 0)
        fired = self.state["clock_fired"].setdefault(a["id"], [])
        if a["schedule"] == "once":
            try:
                dates = [dt.date.fromisoformat(a["date"])]
            except ValueError:
                return
        else:
            today = now.date()
            dates = [today - dt.timedelta(days=1), today]
        for d in dates:
            if a["schedule"] == "custom" and d.weekday() not in a["days"]:
                continue
            target = dt.datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)
            key = f"{d.isoformat()}T{a['time']}"
            if key in fired or now < target:
                continue
            if target.timestamp() < armed_wall - 1:
                # The row was set up after this moment had passed.
                if a["schedule"] == "once" and now >= target + grace:
                    self.finish_once(a)
                continue
            fired.append(key)
            del fired[:-10]
            self.dirty = True
            if now < target + grace:
                self.fire(a)
            else:
                self.log(f"Missed {label(a)} at {fmt12(a['time'])} on {d} (asleep or off).")
            if a["schedule"] == "once":
                self.finish_once(a)

    # -- main step ----------------------------------------------------------
    def tick(self):
        mt = config_mtime()
        if mt != self.mtime:
            self.mtime = mt
            self.cfg = load_config()
            self.rearm()
            self.log("Settings reloaded.")
        if STOP_FILE.exists():
            try:
                STOP_FILE.unlink()
            except OSError:
                pass
            if self.audio.stop():
                self.log("Sound stopped.")
        m = mono()
        slept = boottime() - m
        delta = slept - self.last_sleep
        self.last_sleep = slept
        if delta >= float(self.cfg.get("min_sleep_seconds", 30)):
            self.wake_mono = m
            self.wake_fired = set()
            self.log(f"Woke after {fmt_duration(delta)} asleep.")
        now = now_in(self.cfg)
        for a in list(self.cfg["automations"]):
            if not a["enabled"]:
                continue
            try:
                getattr(self, "_check_" + a["trigger"])(a, m, now)
            except Exception as e:
                self.log(f"Error checking {label(a)}: {e}")
        if time.time() - self.last_maint > 3600:
            self.last_maint = time.time()
            n = cleanup_logs(self.cfg)
            if n:
                self.log(f"Deleted {n} old log file(s).")
            cleanup_cache()
        if self.dirty:
            try:
                save_state(self.state)
            except OSError:
                pass
            self.dirty = False


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def service_pid():
    try:
        pid = int(PID_FILE.read_text().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if APP.encode() in f.read():
                return pid
    except (OSError, ValueError):
        pass
    return None


def window_python():
    try:
        p = WINDOW_PY_FILE.read_text().strip()
        return p if p and os.access(p, os.X_OK) else None
    except OSError:
        return None


def say(text, cfg=None):
    """Speak right away (used for launcher feedback). Returns True if spoken."""
    player = find_player()
    if not player:
        return False
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "say.wav"
        if synth_speech(text, out, cfg):
            subprocess.run([player, str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


def notify(text):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "Frame Automations", text], env=desktop_env(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_run():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    eng = Engine()
    eng.log(f"Frame Automations {VERSION} started: {len(eng.cfg['automations'])} automation(s), "
            f"{'On' if eng.cfg.get('enabled', True) else 'Off'}.")
    while True:
        try:
            eng.tick()
        except Exception as e:
            eng.log(f"Error: {e}")
        time.sleep(0.5)


def cmd_set_enabled(value):
    cfg = load_config()
    cfg["enabled"] = (not cfg.get("enabled", True)) if value is None else value
    save_config(cfg)
    msg = "Automations on." if cfg["enabled"] else "Automations off."
    print(msg)
    if not say(msg, cfg):
        notify(msg)
    return 0


def cmd_stop():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STOP_FILE.touch()
    print("Stop requested; any playing sound stops within a second.")
    return 0


def row_line(i, a):
    flag = "On " if a["enabled"] else "Off"
    if a["action"] == "speak":
        what = f"speak “{a['text'][:40]}”"
    else:
        f = a["file"] or "(no file)"
        what = Path(f).name
        if a["file"] and not os.path.isfile(os.path.expanduser(a["file"])):
            what += "  ⚠ missing"
    if a["action"] == "file" and a["file"] and kind_of(a["file"]) == "script":
        keep = "logs kept forever" if a["log_keep_days"] == 0 else f"logs kept {a['log_keep_days']} d"
        extra = f" ({keep})"
    else:
        rc = a["repeat_count"]
        extra = " (loop)" if rc == 0 else (f" ({rc}×)" if rc > 1 else "")
    return f"{i:>2}. [{flag}] {label(a)} — {describe(a)} → {what}{extra}"


def cmd_list():
    cfg = load_config()
    if not cfg["automations"]:
        print("No automations yet. Open the Frame Automations window to add one.")
    for i, a in enumerate(cfg["automations"], 1):
        print(row_line(i, a))
    return 0


def cmd_status():
    cfg = load_config()
    pid = service_pid()
    now = now_in(cfg)
    tzname = cfg.get("timezone") or f"System ({system_tz_name() or 'local'})"
    piper, voice = find_piper(cfg)
    print(f"Frame Automations {VERSION}")
    print(f"  Service        : {'running (pid %d)' % pid if pid else 'not running'}")
    print(f"  Automations    : {'On' if cfg.get('enabled', True) else 'Off'}, {len(cfg['automations'])} row(s)")
    print(f"  Time zone      : {tzname}; now {now:%a %Y-%m-%d %I:%M:%S %p %Z}")
    print(f"  Awake time     : {fmt_duration(mono())} since boot ({fmt_duration(boottime())} including sleep)")
    print(f"  Player         : {find_player() or 'none found'}")
    print(f"  MP3/M4A        : {find_ffmpeg() or 'not installed (frame-automations install-decoder)'}")
    print(f"  Speech         : {piper or speech_engine(cfg) or 'none found'}")
    print(f"  Voice          : {voice or '-'}")
    print(f"  Window Python  : {window_python() or ('this Python' if _has_tk() else 'none (run install.sh)')}")
    print(f"  Settings       : {CONFIG_FILE}")
    print(f"  Logs           : {LOG_DIR}")
    print()
    return cmd_list()


def cmd_test(n):
    cfg = load_config()
    try:
        a = cfg["automations"][int(n) - 1]
    except (IndexError, ValueError):
        print("Give the row number from `frame-automations list`.")
        return 1
    return run_once_and_wait(a, cfg)


def cmd_run_row():
    """Used by the window's ▶ Test: a row as JSON on stdin, run once."""
    try:
        req = json.loads(sys.stdin.read())
        a = normalize(req["row"])
    except (ValueError, KeyError, TypeError) as e:
        print(f"Bad test request: {e}")
        return 1
    cfg = dict(load_config(), timezone=req.get("timezone", ""))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    return run_once_and_wait(a, cfg)


def run_once_and_wait(a, cfg):
    log = make_logger(lambda: cfg)
    audio = AudioPlayer(log, lambda: cfg)
    r = run_action(a, cfg, audio, log, repeat_override=1)
    try:
        if isinstance(r, tuple):
            print(f"Started {Path(a['file']).name}; output goes to {r[1].name}.")
            r[2].join()
        elif r:
            time.sleep(0.2)
            while audio.busy():
                time.sleep(0.2)
            print(f"Finished testing {label(a)}.")
    except (KeyboardInterrupt, SystemExit):
        audio.stop()
        print("Stopped.")
    return 0 if r else 1


def cmd_install_decoder():
    if find_ffmpeg():
        print(f"MP3/M4A support is already installed: {find_ffmpeg()}")
        return 0
    try:
        install_decoder()
        return 0
    except Exception as e:
        print(f"Couldn't install MP3/M4A support: {e}")
        return 1


def cmd_logs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(LOG_DIR)
    if shutil.which("xdg-open") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        subprocess.Popen(["xdg-open", str(LOG_DIR)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


HELP = f"""Frame Automations {VERSION}

  frame-automations gui              open the automations window
  frame-automations list             show your automations
  frame-automations status           service, time zone, tools and automations
  frame-automations on | off | toggle   turn all automations on or off
  frame-automations stop             stop any sound that is playing
  frame-automations test N           run row N from `list` now (plays once)
  frame-automations install-decoder  add MP3/M4A support
  frame-automations logs             show (and open) the logs folder
  frame-automations run              the background service (systemd starts this)
"""


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "run":
        return cmd_run()
    if cmd == "gui":
        return cmd_gui()
    if cmd == "list":
        return cmd_list()
    if cmd == "status":
        return cmd_status()
    if cmd in ("on", "off", "toggle"):
        return cmd_set_enabled({"on": True, "off": False}.get(cmd))
    if cmd == "stop":
        return cmd_stop()
    if cmd == "test" and len(argv) > 2:
        return cmd_test(argv[2])
    if cmd == "run-row":
        return cmd_run_row()
    if cmd == "install-decoder":
        return cmd_install_decoder()
    if cmd == "logs":
        return cmd_logs()
    if cmd == "say" and len(argv) > 2:  # used by install.sh
        if not say(" ".join(argv[2:]), load_config()):
            print(" ".join(argv[2:]))
        return 0
    if cmd == "voice":  # used by install.sh
        print(speech_engine(load_config()) or "none")
        return 0
    print(HELP)
    return 0 if cmd in ("help", "-h", "--help") else 1


# --------------------------------------------------------------------------
# Window (tkinter)
# --------------------------------------------------------------------------

tk = ttk = messagebox = filedialog = None

HOURS = [str(h) for h in range(1, 13)]
MINUTES = [f"{m:02d}" for m in range(60)]
DELAY_MIN = [str(m) for m in range(121)]
DELAY_SEC = [str(s) for s in range(60)]
PLAY_CHOICES = ["Play once"] + [f"Play {n}×" for n in range(2, 11)] + ["Loop until stopped"]
KEEP_CHOICES = ["Do Not Delete"] + ["1 day"] + [f"{d} days" for d in range(2, 31)]


def play_label(n):
    return "Loop until stopped" if n == 0 else ("Play once" if n == 1 else f"Play {n}×")


def play_value(s):
    if s.startswith("Loop"):
        return 0
    if s == "Play once":
        return 1
    return _int(re.sub(r"\D", "", s), 1, 1, 10)


def keep_label(d):
    return "Do Not Delete" if d == 0 else ("1 day" if d == 1 else f"{d} days")


def keep_value(s):
    return 0 if s.startswith("Do") else _int(re.sub(r"\D", "", s), 30, 1, 30)


def canonical(d):
    return json.dumps(normalize(d), sort_keys=True)


def _has_tk():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def show_message(text):
    for cmd in (["kdialog", "--title", "Frame Automations", "--msgbox", text],
                ["zenity", "--info", "--title=Frame Automations", "--no-wrap", f"--text={text}"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd)
                return
            except OSError:
                pass


def cmd_gui():
    if not _has_tk():
        alt = window_python()
        if alt and os.path.realpath(alt) != os.path.realpath(sys.executable):
            os.execv(alt, [alt, os.path.abspath(__file__), "gui"])
        msg = ("The Frame Automations window can't be drawn: this Python has no window "
               "toolkit (tkinter).\n\nRun  bash install.sh  again from Konsole; it installs "
               "a private Python that can draw it.\n\nMeanwhile, from Konsole:\n"
               "  python3 ~/.local/bin/frame-automations list\n"
               "  python3 ~/.local/bin/frame-automations status")
        print(msg)
        show_message(msg)
        return 1
    global tk, ttk, messagebox, filedialog
    import tkinter as _tk
    from tkinter import filedialog as _fd, messagebox as _mb, ttk as _ttk
    tk, ttk, messagebox, filedialog = _tk, _ttk, _mb, _fd
    try:
        root = tk.Tk()
    except tk.TclError as e:
        msg = f"The window can't open here ({e})."
        print(msg)
        show_message(msg)
        return 1
    AutomationsWindow(root)
    root.mainloop()
    return 0


def combo(parent, var, values, width, command=None):
    cb = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly", height=16)
    if command:
        cb.bind("<<ComboboxSelected>>", lambda e: command())
    return cb


class RowUI:
    def __init__(self, app, parent, data):
        self.app = app
        self.data = dict(data)
        self.id = data["id"]
        tz_now = app.now()
        f = self.frame = ttk.Frame(parent, padding=(10, 8, 10, 4))

        self.v_on = tk.BooleanVar(value=data["enabled"])
        self.v_name = tk.StringVar(value=data["name"])
        self.v_trig = tk.StringVar(value=data["trigger"].title())
        mins, secs = divmod(data["delay_seconds"], 60)
        self.v_dmin = tk.StringVar(value=str(mins))
        self.v_dsec = tk.StringVar(value=str(secs))
        h, mi = map(int, data["time"].split(":"))
        self.v_hour = tk.StringVar(value=str((h % 12) or 12))
        self.v_min = tk.StringVar(value=f"{mi:02d}")
        self.v_ampm = tk.StringVar(value="AM" if h < 12 else "PM")
        self.v_sched = tk.StringVar(value=data["schedule"].title())
        self.v_days = [tk.BooleanVar(value=i in data["days"]) for i in range(7)]
        self.date_map = {}
        dates = [tz_now.date() + dt.timedelta(days=i) for i in range(366)]
        if data["date"]:
            try:
                d0 = dt.date.fromisoformat(data["date"])
                if d0 not in dates:
                    dates.insert(0, d0)
            except ValueError:
                pass
        for d in dates:
            self.date_map[f"{d:%a %b} {d.day}, {d.year}"] = d.isoformat()
        self.date_rev = {v: k for k, v in self.date_map.items()}
        self.v_date = tk.StringVar(value=self.date_rev.get(data["date"], ""))
        self.v_action = tk.StringVar(value="Speak" if data["action"] == "speak" else "File")
        self.v_file = tk.StringVar(value=data["file"])
        self.v_text = tk.StringVar(value=data["text"])
        self.v_play = tk.StringVar(value=play_label(data["repeat_count"]))
        self.v_keep = tk.StringVar(value=keep_label(data["log_keep_days"]))

        # Line 1: when
        l1 = ttk.Frame(f)
        l1.pack(fill="x")
        ttk.Checkbutton(l1, text="On", variable=self.v_on).pack(side="left")
        ttk.Label(l1, text="Name").pack(side="left", padx=(12, 4))
        ttk.Entry(l1, textvariable=self.v_name, width=18).pack(side="left")
        ttk.Label(l1, text="Trigger").pack(side="left", padx=(14, 4))
        combo(l1, self.v_trig, ["Boot", "Wake", "Clock"], 6, self.refresh).pack(side="left")
        self.when = ttk.Frame(l1)
        self.when.pack(side="left", padx=(8, 0))
        self.delay_box = ttk.Frame(self.when)
        ttk.Label(self.delay_box, text="after").pack(side="left", padx=(0, 4))
        combo(self.delay_box, self.v_dmin, DELAY_MIN, 4).pack(side="left")
        ttk.Label(self.delay_box, text="min").pack(side="left", padx=(3, 6))
        combo(self.delay_box, self.v_dsec, DELAY_SEC, 3).pack(side="left")
        ttk.Label(self.delay_box, text="sec").pack(side="left", padx=(3, 0))
        self.clock_box = ttk.Frame(self.when)
        ttk.Label(self.clock_box, text="at").pack(side="left", padx=(0, 4))
        combo(self.clock_box, self.v_hour, HOURS, 3, self.refresh).pack(side="left")
        ttk.Label(self.clock_box, text=":").pack(side="left", padx=2)
        combo(self.clock_box, self.v_min, MINUTES, 3, self.refresh).pack(side="left")
        combo(self.clock_box, self.v_ampm, ["AM", "PM"], 4, self.refresh).pack(side="left", padx=(4, 0))
        ttk.Label(l1, text="Repeat").pack(side="left", padx=(14, 4))
        combo(l1, self.v_sched, ["Daily", "Once", "Custom"], 7, self.refresh).pack(side="left")
        self.extra = ttk.Frame(l1)
        self.extra.pack(side="left", padx=(8, 0))
        self.days_box = ttk.Frame(self.extra)
        for i, d in enumerate(DAYS):
            ttk.Checkbutton(self.days_box, text=d, variable=self.v_days[i]).pack(side="left", padx=(0, 2))
        self.date_box = ttk.Frame(self.extra)
        ttk.Label(self.date_box, text="on").pack(side="left", padx=(0, 4))
        combo(self.date_box, self.v_date, list(self.date_map), 16).pack(side="left")
        self.repeat_note = ttk.Label(self.extra, style="Muted.TLabel")
        ttk.Button(l1, text="✕", width=3, command=self.remove).pack(side="right")

        # Line 2: what
        l2 = ttk.Frame(f)
        l2.pack(fill="x", pady=(6, 0))
        ttk.Label(l2, text="Action").pack(side="left", padx=(30, 4))
        combo(l2, self.v_action, ["File", "Speak"], 6, self.refresh).pack(side="left")
        self.mid = ttk.Frame(l2)
        self.mid.pack(side="left", padx=(8, 0))
        self.file_box = ttk.Frame(self.mid)
        self.file_entry = ttk.Entry(self.file_box, textvariable=self.v_file, width=44)
        self.file_entry.pack(side="left")
        self.file_entry.bind("<Configure>", lambda e: self.file_entry.xview_moveto(1.0))
        ttk.Button(self.file_box, text="Browse…", command=self.browse).pack(side="left", padx=(4, 0))
        self.file_note = ttk.Label(self.file_box, width=13)
        self.file_note.pack(side="left", padx=(6, 0))
        self.text_box = ttk.Frame(self.mid)
        ttk.Entry(self.text_box, textvariable=self.v_text, width=58).pack(side="left")
        self.opt = ttk.Frame(l2)
        self.opt.pack(side="left", padx=(8, 0))
        self.play_cb = combo(self.opt, self.v_play, PLAY_CHOICES, 17)
        self.keep_box = ttk.Frame(self.opt)
        ttk.Label(self.keep_box, text="Keep logs").pack(side="left", padx=(0, 4))
        combo(self.keep_box, self.v_keep, KEEP_CHOICES, 13).pack(side="left")
        ttk.Button(l2, text="▶ Test", command=self.test).pack(side="right")
        ttk.Separator(f).pack(fill="x", pady=(8, 0))

        self.v_file.trace_add("write", lambda *_: self.refresh())
        self.refresh()
        self.initial = canonical(self.to_data())

    def file_kind(self):
        path = os.path.expanduser(self.v_file.get().strip())
        if not path:
            return ""
        if not os.path.isfile(path):
            return "missing"
        return kind_of(path)

    def refresh(self):
        trig, sched, action = self.v_trig.get(), self.v_sched.get(), self.v_action.get()
        for w in (self.delay_box, self.clock_box):
            w.pack_forget()
        (self.clock_box if trig == "Clock" else self.delay_box).pack(side="left")
        for w in (self.days_box, self.date_box, self.repeat_note):
            w.pack_forget()
        if sched == "Custom":
            self.days_box.pack(side="left")
        elif sched == "Once" and trig == "Clock":
            if not self.v_date.get():
                self.v_date.set(self.date_rev.get(self.next_occurrence().isoformat(), ""))
            self.date_box.pack(side="left")
        else:
            word = trig.lower()
            self.repeat_note.config(text=(f"next {word} only" if sched == "Once" else
                                          f"every {word}" if trig != "Clock" else "every day"))
            self.repeat_note.pack(side="left")
        for w in (self.file_box, self.text_box):
            w.pack_forget()
        kind = ""
        if action == "File":
            self.file_box.pack(side="left")
            kind = self.file_kind()
            text, style = {"": ("", "TLabel"), "missing": ("⚠ missing", "Warn.TLabel"),
                           "audio": ("audio", "Muted.TLabel"), "script": ("script", "Muted.TLabel"),
                           "unknown": ("⚠ unsupported", "Warn.TLabel")}[kind]
            self.file_note.config(text=text, style=style)
            try:
                typing = self.file_entry.focus_get() is self.file_entry
            except (KeyError, tk.TclError):
                typing = False
            if not typing:
                self.file_entry.after_idle(lambda: self.file_entry.xview_moveto(1.0))
        else:
            self.text_box.pack(side="left")
        self.play_cb.pack_forget()
        self.keep_box.pack_forget()
        if action == "File" and kind == "script":
            self.keep_box.pack(side="left")
        else:
            self.play_cb.pack(side="left")

    def next_occurrence(self):
        now = self.app.now()
        h = int(self.v_hour.get()) % 12 + (12 if self.v_ampm.get() == "PM" else 0)
        target = now.replace(hour=h, minute=int(self.v_min.get()), second=0, microsecond=0)
        return (target if target > now else target + dt.timedelta(days=1)).date()

    def to_data(self):
        d = dict(self.data)
        d["enabled"] = bool(self.v_on.get())
        d["name"] = self.v_name.get().strip()
        d["trigger"] = self.v_trig.get().lower()
        d["delay_seconds"] = min(MAX_DELAY, int(self.v_dmin.get()) * 60 + int(self.v_dsec.get()))
        h = int(self.v_hour.get()) % 12 + (12 if self.v_ampm.get() == "PM" else 0)
        d["time"] = f"{h:02d}:{int(self.v_min.get()):02d}"
        d["schedule"] = self.v_sched.get().lower()
        d["days"] = [i for i, v in enumerate(self.v_days) if v.get()]
        d["date"] = (self.date_map.get(self.v_date.get(), "")
                     if d["schedule"] == "once" and d["trigger"] == "clock" else "")
        d["action"] = self.v_action.get().lower()
        d["file"] = self.v_file.get().strip()
        d["text"] = self.v_text.get().strip()
        d["repeat_count"] = play_value(self.v_play.get())
        d["log_keep_days"] = keep_value(self.v_keep.get())
        return normalize(d)

    def validate(self, d, n):
        who = f"Row {n}" + (f" ({d['name']})" if d["name"] else "")
        if d["action"] == "file":
            if not d["file"]:
                return f"{who}: choose a file with Browse…"
            if os.path.isfile(os.path.expanduser(d["file"])) and kind_of(d["file"]) == "unknown":
                return f"{who}: {Path(d['file']).name} isn't an audio file or a script."
        elif not d["text"]:
            return f"{who}: type what to say."
        if d["schedule"] == "custom" and not d["days"]:
            return f"{who}: tick at least one day."
        if d["enabled"] and d["schedule"] == "once" and d["trigger"] == "clock":
            if not d["date"]:
                return f"{who}: pick a date."
            date = dt.date.fromisoformat(d["date"])
            h, m = map(int, d["time"].split(":"))
            now = self.app.now()
            if dt.datetime(date.year, date.month, date.day, h, m, tzinfo=now.tzinfo) <= now:
                return f"{who}: that one-time date and time has already passed."
        return None

    def browse(self):
        path = self.app.pick_file(self.v_file.get())
        if path:
            self.v_file.set(path)
            if Path(path).suffix.lower() in AUDIO_DECODE and not find_ffmpeg():
                self.app.offer_decoder()

    def test(self):
        self.app.test_row(self.to_data())

    def remove(self):
        self.app.remove_row(self)


class AutomationsWindow:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.rows = []
        # Tk here must not share the process with Python threads (some Tk builds abort),
        # so tests and downloads run as child processes that the window polls.
        self.jobs = {}  # kind ("test" / "decoder") -> (Popen, output file path)

        root.title("Frame Automations")
        root.geometry("1240x720")
        root.minsize(960, 480)
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(size=11)
            except tk.TclError:
                pass
        root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = tkfont.nametofont("TkDefaultFont")
        self.title_font = base.copy()
        self.title_font.configure(size=16, weight="bold")
        self.bold_font = base.copy()
        self.bold_font.configure(weight="bold")
        style.configure("Title.TLabel", font=self.title_font)
        style.configure("Muted.TLabel", foreground="#5f6368")
        style.configure("Warn.TLabel", foreground="#b3261e")
        style.configure("Accent.TButton", font=self.bold_font)
        # Don't let the mouse wheel change dropdown values while scrolling the list.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.unbind_class("TCombobox", seq)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="Frame Automations", style="Title.TLabel").pack(side="left")
        self.clock_lbl = ttk.Label(head, style="Muted.TLabel")
        self.clock_lbl.pack(side="right")

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 0))
        self.v_enabled = tk.BooleanVar(value=self.cfg.get("enabled", True))
        ttk.Checkbutton(bar, text="Automations On", variable=self.v_enabled).pack(side="left")
        ttk.Label(bar, text="Time zone").pack(side="left", padx=(18, 4))
        self.sys_tz_label = f"System ({system_tz_name() or 'local'})"
        zones = sorted(z for z in available_timezones() if not z.startswith(("posix/", "right/")))
        self.v_tz = tk.StringVar(value=self.cfg.get("timezone") or self.sys_tz_label)
        combo(bar, self.v_tz, [self.sys_tz_label] + zones, 30, self.tick_clock).pack(side="left")
        ttk.Button(bar, text="Open Logs", command=self.open_logs).pack(side="left", padx=(18, 0))
        self.mp3_btn = ttk.Button(bar, text="Install MP3/M4A support", command=self.install_decoder)
        self.mp3_lbl = ttk.Label(bar, style="Muted.TLabel")
        self.refresh_mp3()

        ttk.Separator(outer).pack(fill="x", pady=(10, 0))
        wrap = ttk.Frame(outer)
        wrap.pack(fill="both", expand=True)
        bg = style.lookup("TFrame", "background") or "#dcdad5"
        self.canvas = tk.Canvas(wrap, highlightthickness=0, background=bg)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        root.bind_all("<Button-4>", lambda e: self.scroll(e, -3))
        root.bind_all("<Button-5>", lambda e: self.scroll(e, 3))
        root.bind_all("<MouseWheel>", lambda e: self.scroll(e, -3 if e.delta > 0 else 3))
        self.empty_lbl = ttk.Label(self.inner, style="Muted.TLabel", padding=20,
                                   text="No automations yet. Press  + Add automation  to make one.")

        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(10, 0))
        ttk.Button(foot, text="+ Add automation", command=self.add_new).pack(side="left")
        ttk.Button(foot, text="■ Stop sound", command=self.stop_sound).pack(side="left", padx=(8, 0))
        ttk.Button(foot, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(foot, text="Close", command=self.close).pack(side="right", padx=(0, 8))
        self.status = ttk.Label(foot, style="Muted.TLabel", anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=(14, 8))

        for a in self.cfg["automations"]:
            self.add_row(a)
        self.update_empty()
        self.saved = self.snapshot()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-s>", self.save)
        self.tick_clock()
        self.poll_msgs()

    # -- helpers ------------------------------------------------------------
    def tz_setting(self):
        v = self.v_tz.get()
        return "" if v == self.sys_tz_label else v

    def now(self):
        return now_in({"timezone": self.tz_setting() if hasattr(self, "v_tz") else ""})

    def tick_clock(self):
        if getattr(self, "_clock_job", None):
            self.root.after_cancel(self._clock_job)
        now = self.now()
        self.clock_lbl.config(text=f"Now {now:%-I:%M:%S %p} {now.tzname()}   ·   "
                                   f"Awake since boot {fmt_duration(mono())}")
        self._clock_job = self.root.after(1000, self.tick_clock)

    def start_job(self, kind, args, stdin_text=None):
        self.kill_job(kind)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out = CACHE_DIR / f"window-{kind}.txt"
        with open(out, "w") as f:
            proc = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__)] + args,
                                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                    stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text.encode())
                proc.stdin.close()
            except OSError:
                pass
        self.jobs[kind] = (proc, out)

    def kill_job(self, kind):
        job = self.jobs.pop(kind, None)
        if job and job[0].poll() is None:
            try:
                os.killpg(job[0].pid, signal.SIGTERM)
            except OSError:
                pass

    def poll_msgs(self):
        for kind, (proc, out) in list(self.jobs.items()):
            try:
                lines = [l.strip() for l in out.read_text(errors="replace").splitlines() if l.strip()]
            except OSError:
                lines = []
            if lines:
                self.set_status(lines[-1])
            if proc.poll() is not None:
                del self.jobs[kind]
                if kind == "decoder":
                    self.refresh_mp3()
        self.root.after(250, self.poll_msgs)

    def set_status(self, text):
        self.status.config(text=text)

    def scroll(self, event, amount):
        if "popdown" in str(event.widget):
            return
        self.canvas.yview_scroll(amount, "units")

    def snapshot(self):
        return json.dumps([[canonical(r.to_data()) for r in self.rows],
                           bool(self.v_enabled.get()), self.tz_setting()])

    def update_empty(self):
        if self.rows:
            self.empty_lbl.pack_forget()
        else:
            self.empty_lbl.pack(fill="x")

    # -- rows -----------------------------------------------------------------
    def add_row(self, data):
        r = RowUI(self, self.inner, data)
        r.frame.pack(fill="x")
        self.rows.append(r)
        self.update_empty()
        return r

    def add_new(self):
        self.add_row(new_automation())
        self.root.after(80, lambda: self.canvas.yview_moveto(1.0))

    def remove_row(self, r):
        r.frame.destroy()
        self.rows.remove(r)
        self.update_empty()

    # -- files ------------------------------------------------------------------
    def pick_file(self, current):
        cur = os.path.expanduser(current or "")
        start = os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur)) else str(HOME)
        audio = sorted(AUDIO_NATIVE | AUDIO_DECODE)
        scripts = sorted(SCRIPT_EXT)
        pats = lambda exts: " ".join(f"*{e} *{e.upper()}" for e in exts)
        self.root.update_idletasks()
        kd = shutil.which("kdialog")
        if kd:
            try:
                flt = (f"Audio and scripts ({pats(audio)} {pats(scripts)})|Audio ({pats(audio)})|"
                       f"Scripts ({pats(scripts)})|All files (*)")
                r = subprocess.run([kd, "--title", "Choose an audio file or script",
                                    "--getopenfilename", start, flt], capture_output=True, text=True)
                if r.returncode in (0, 1):
                    return r.stdout.strip() if r.returncode == 0 else ""
            except OSError:
                pass
        zen = shutil.which("zenity")
        if zen:
            try:
                r = subprocess.run([zen, "--file-selection", "--title=Choose an audio file or script",
                                    f"--filename={start}/",
                                    f"--file-filter=Audio and scripts | {pats(audio)} {pats(scripts)}",
                                    f"--file-filter=Audio | {pats(audio)}",
                                    f"--file-filter=Scripts | {pats(scripts)}",
                                    "--file-filter=All files | *"], capture_output=True, text=True)
                if r.returncode in (0, 1):
                    return r.stdout.strip() if r.returncode == 0 else ""
            except OSError:
                pass
        return filedialog.askopenfilename(
            parent=self.root, initialdir=start, title="Choose an audio file or script",
            filetypes=[("Audio and scripts", pats(audio) + " " + pats(scripts)),
                       ("Audio", pats(audio)), ("Scripts", pats(scripts)), ("All files", "*")]) or ""

    def refresh_mp3(self):
        self.mp3_btn.pack_forget()
        self.mp3_lbl.pack_forget()
        if find_ffmpeg():
            self.mp3_lbl.config(text="MP3/M4A support: installed")
            self.mp3_lbl.pack(side="left", padx=(18, 0))
        else:
            self.mp3_btn.config(state="normal")
            self.mp3_btn.pack(side="left", padx=(18, 0))

    def offer_decoder(self):
        if messagebox.askyesno("MP3/M4A support",
                               "This file needs MP3/M4A support, which isn't installed yet.\n\n"
                               "Download it now? (about 40 MB, stored only in your home folder)",
                               parent=self.root):
            self.install_decoder()

    def install_decoder(self):
        if "decoder" in self.jobs:
            return
        self.mp3_btn.config(state="disabled")
        self.set_status("Starting the MP3/M4A download…")
        self.start_job("decoder", ["install-decoder"])

    def open_logs(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(LOG_DIR)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.set_status(f"Logs: {LOG_DIR}")
        else:
            messagebox.showinfo("Logs", f"Logs are in:\n{LOG_DIR}", parent=self.root)

    # -- actions ------------------------------------------------------------------
    def test_row(self, d):
        name = label(d)
        if d["action"] == "file":
            path = os.path.expanduser(d["file"])
            if not path:
                self.set_status("Choose a file first.")
                return
            if not os.path.isfile(path):
                self.set_status(f"{Path(path).name} is missing.")
                return
            if kind_of(path) == "script" and not messagebox.askyesno(
                    "Run script", f"Run {Path(path).name} now?", parent=self.root):
                return
            if Path(path).suffix.lower() in AUDIO_DECODE and not find_ffmpeg():
                self.offer_decoder()
                return
        self.start_job("test", ["run-row"], json.dumps({"row": d, "timezone": self.tz_setting()}))
        self.set_status(f"Testing {name}…")

    def stop_sound(self):
        self.kill_job("test")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FILE.touch()
        self.set_status("Sound stopped.")

    def save(self, *_):
        datas = []
        for i, r in enumerate(self.rows, 1):
            d = r.to_data()
            err = r.validate(d, i)
            if err:
                messagebox.showerror("Can't save yet", err, parent=self.root)
                return False
            datas.append(d)
        disk = load_config()
        disk_rows = {a["id"]: a for a in disk["automations"]}
        for r, d in zip(self.rows, datas):
            dr = disk_rows.get(d["id"])
            # The service switched a one-time row Off while this window was open, and
            # you didn't touch that row: keep it Off.
            if (dr and dr.get("done_at") and dr.get("done_at") != d.get("done_at")
                    and canonical(d) == r.initial):
                d["enabled"] = False
                d["done_at"] = dr["done_at"]
        disk["enabled"] = bool(self.v_enabled.get())
        disk["timezone"] = self.tz_setting()
        disk["automations"] = datas
        try:
            save_config(disk)
        except OSError as e:
            messagebox.showerror("Can't save", str(e), parent=self.root)
            return False
        self.cfg = disk
        for r, d in zip(self.rows, datas):
            r.data = d
            r.v_on.set(d["enabled"])
            r.initial = canonical(d)
        self.saved = self.snapshot()
        self.set_status("Saved. The service uses the new settings within a few seconds.")
        return True

    def close(self):
        if self.snapshot() != self.saved:
            ans = messagebox.askyesnocancel("Unsaved changes", "Save your changes before closing?",
                                            parent=self.root)
            if ans is None or (ans and not self.save()):
                return
        self.kill_job("test")
        self.root.destroy()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
