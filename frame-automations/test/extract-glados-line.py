#!/usr/bin/env python3
# Extract a Portal 2 GLaDOS voice line (default: her "…it's you." wake-up line)
# from your own Portal 2 install into ~/Music/Portal 2 GLaDOS/.
# Copyright (c) 2026 joshmanpro79-create. Released under the MIT License.
"""
How it finds the file
  1. Reads Portal 2's English captions (the text shown for every spoken line).
  2. Keeps the GLaDOS lines whose text contains the phrase.
  3. Uses the game's sound scripts to turn each caption into its .wav path.
  4. Copies that .wav out of the game's VPK archives (or loose files).

Usage
  python3 extract-glados-line.py                    # the "it's you" line
  python3 extract-glados-line.py "long time"        # any other phrase
  python3 extract-glados-line.py --play             # also play what it found
  python3 extract-glados-line.py --game "/run/media/deck/SD/steamapps/common/Portal 2"
Only Python's standard library is used. Nothing is changed in the game folder.
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

DEFAULT_PHRASE = "it's you"
OUT_DIR = Path.home() / "Music" / "Portal 2 GLaDOS"


# ---------------------------------------------------------------- finding the game

def steam_libraries():
    roots = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
    libs = []
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
                libs.append(Path(p.replace("\\\\", "/")))
        libs.append(root)
    seen, out = set(), []
    for lib in libs:
        key = os.path.realpath(lib)
        if key not in seen:
            seen.add(key)
            out.append(lib)
    return out


def find_game(explicit=None):
    cands = [Path(explicit)] if explicit else [lib / "steamapps/common/Portal 2" for lib in steam_libraries()]
    for c in cands:
        if (c / "portal2").is_dir():
            return c
    return None


# ---------------------------------------------------------------- VPK reading

class VPK:
    """Minimal reader for Valve VPK v1/v2 directory archives (pak01_dir.vpk)."""

    def __init__(self, dir_path):
        self.dir_path = Path(dir_path)
        self.entries = {}
        with open(self.dir_path, "rb") as f:
            sig, ver, tree = struct.unpack("<III", f.read(12))
            if sig != 0x55AA1234:
                raise ValueError(f"{dir_path} isn't a VPK file")
            header = 12 if ver == 1 else 28
            f.seek(header)
            data = f.read(tree)
        self.data_base = header + tree
        pos = 0

        def cstr():
            nonlocal pos
            end = data.index(b"\0", pos)
            s = data[pos:end].decode("utf-8", "replace")
            pos = end + 1
            return s

        while True:
            ext = cstr()
            if not ext:
                break
            while True:
                path = cstr()
                if not path:
                    break
                while True:
                    name = cstr()
                    if not name:
                        break
                    _crc, preload, arch, off, length, _term = struct.unpack_from("<IHHIIH", data, pos)
                    pos += 18
                    pre = data[pos:pos + preload]
                    pos += preload
                    full = name if path.strip() == "" else f"{path}/{name}"
                    if ext.strip():
                        full += "." + ext
                    self.entries[full.lower()] = (arch, off, length, pre)

    def read(self, name):
        arch, off, length, pre = self.entries[name.lower()]
        if length == 0:
            return pre
        if arch == 0x7FFF:
            src, off = self.dir_path, self.data_base + off
        else:
            src = self.dir_path.with_name(self.dir_path.name.replace("_dir.vpk", f"_{arch:03d}.vpk"))
        with open(src, "rb") as f:
            f.seek(off)
            return pre + f.read(length)


class GameFiles:
    """Looks a game path up in loose folders first, then in every VPK."""

    def __init__(self, game):
        self.dirs = []
        for sub in ("portal2_dlc2", "portal2_dlc1", "update", "portal2"):
            d = game / sub
            if d.is_dir():
                self.dirs.append(d)
        self.vpks = []
        for d in self.dirs:
            for v in sorted(d.glob("pak*_dir.vpk")):
                try:
                    self.vpks.append(VPK(v))
                except (OSError, ValueError, struct.error) as e:
                    print(f"  (skipping {v.name}: {e})")

    def names(self):
        out = set()
        for d in self.dirs:
            for p in d.rglob("*"):
                if p.is_file():
                    out.add(str(p.relative_to(d)).replace(os.sep, "/").lower())
        for v in self.vpks:
            out.update(v.entries)
        return out

    def read(self, name):
        for d in self.dirs:
            p = d / name
            if p.is_file():
                return p.read_bytes()
        for v in self.vpks:
            if name.lower() in v.entries:
                return v.read(name)
        return None

    def read_all(self, name):
        """Every copy of a file (DLC, update and main game each have their own captions)."""
        out = []
        for d in self.dirs:
            p = d / name
            if p.is_file():
                out.append((str(p), p.read_bytes()))
        for v in self.vpks:
            if name.lower() in v.entries:
                out.append((f"{v.dir_path.parent.name}/{v.dir_path.name}:{name}", v.read(name)))
        return out


# ---------------------------------------------------------------- captions

def decode_text(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if len(raw) > 1 and raw[1:2] == b"\0":
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def parse_caption_txt(text):
    """closecaption_english.txt: "token"  "caption text" -> {token_lower: text}"""
    return {k.lower(): v for k, v in re.findall(r'^[ \t]*"([^"\r\n]+)"[ \t]+"([^\r\n]*)"', text, re.M)}


def parse_caption_dat(raw):
    """Compiled closecaption_english.dat -> {crc32_of_token: text}"""
    magic, ver, nblocks, blocksize, ndir, dataoff = struct.unpack_from("<6i", raw, 0)
    if raw[:4] != b"VCCD":
        raise ValueError("not a compiled caption file")
    out = {}
    for i in range(ndir):
        h, block, off, length = struct.unpack_from("<IiHH", raw, 24 + i * 12)
        start = dataoff + block * blocksize + off
        out[h] = raw[start:start + length].decode("utf-16-le", "replace").rstrip("\0")
    return out


def clean(text):
    text = re.sub(r"<[^>]*>", "", text).replace("’", "'")
    return text.strip()


def norm(text):
    text = clean(text).lower().replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


# ---------------------------------------------------------------- sound scripts

WAVE_PREFIX = "*#@<>^)(}$!?&~`+%"


def parse_soundscripts(text):
    """{sound_name_lower: [wave paths]} from a soundscript file."""
    text = re.sub(r"//[^\n]*", "", text)
    tokens = re.findall(r'"[^"]*"|[{}]|[^\s{}"]+', text)
    out, depth, name, last, pending = {}, 0, None, None, None
    for t in tokens:
        if t == "{":
            if depth == 0:
                # A block with no name before it isn't a sound entry; skip it.
                name = last.lower() if last else None
                if name:
                    out.setdefault(name, [])
            depth += 1
            last = None
        elif t == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                name = None
            last = None
        else:
            t = t.strip('"')
            if depth > 0 and pending == "wave" and name:
                out[name].append(t.lstrip(WAVE_PREFIX).replace("\\", "/"))
            pending = t.lower() if depth > 0 else None
            last = t
    return out


# ---------------------------------------------------------------- report

def report(files, all_names, captions_txt, captions_dat, sounds, matches, hits):
    """What each lookup step found, to paste back when the search comes up empty."""
    print("\n--- Lookup report (paste this back if you need help) ---")
    print("Folders searched: " + (", ".join(d.name for d in files.dirs) or "none"))
    for v in files.vpks:
        print(f"  {v.dir_path.parent.name}/{v.dir_path.name}: {len(v.entries)} files")
    caps = sorted(x for x in all_names if x.startswith("resource/") and
                  ("caption" in x or "subtitle" in x) and "english" in x)
    print(f"Caption files: {', '.join(caps) or 'none'}")
    print(f"  captions read: {len(captions_txt)} from .txt, {len(captions_dat)} from .dat")
    scripts = [x for x in all_names if x.startswith("scripts/") and x.endswith(".txt")]
    print(f"Script files: {len(scripts)}; sound entries with a wave: {len(sounds)}")
    gl = [k for k, v in sounds.items() if "glados" in k or any("glados" in w.lower() for w in v)]
    print(f"  GLaDOS sound entries: {len(gl)}" + (f" (e.g. {', '.join(sorted(gl)[:3])})" if gl else ""))
    vo = sorted(x for x in all_names if x.startswith("sound/vo/glados/"))
    print(f"Files in sound/vo/glados: {len(vo)}" + (f" (e.g. {', '.join(Path(x).name for x in vo[:3])})" if vo else ""))
    wake = [Path(x).name for x in vo if "wake" in x]
    if wake:
        print(f"  with 'wake' in the name: {', '.join(wake[:12])}")
    print(f"Captions containing the phrase: {len(matches)}")
    for text, sound, key in matches[:15]:
        waves = sounds.get(sound, []) if sound else []
        where = ", ".join(waves[:2]) if waves else "no matching sound entry"
        print(f"  • {text[:70]}\n      key: {key}  ->  {where}")
    if hits and not any("glados" in (h[1] + h[2]).lower() for h in hits):
        print("  (matches were found, but none look like GLaDOS)")
    print("--- end of report ---")


# ---------------------------------------------------------------- extract by name

def extract_named(files, all_names, args, sounds, captions_txt, captions_dat):
    import fnmatch
    vo = sorted(x for x in all_names if x.startswith("sound/vo/") and x.endswith(".wav"))
    picked = []
    for pat in args.wav:
        pat = pat.strip().strip(",").lower()
        if not pat:
            continue
        if not pat.endswith(".wav") and not pat.endswith("*"):
            pat += ".wav"
        found = [x for x in vo if fnmatch.fnmatch(Path(x).name, pat)]
        # Prefer GLaDOS's folder when the same name exists for several characters.
        gl = [x for x in found if x.startswith("sound/vo/glados/")]
        found = gl or found
        if not found:
            print(f"  ✗ {pat}: not in the game files")
        picked += [x for x in found if x not in picked]
    if not picked:
        return 1

    # wave path -> caption text, where the game has one
    by_wave = {}
    for sound, waves in sounds.items():
        text = captions_txt.get(sound) or captions_dat.get(zlib.crc32(sound.encode()) & 0xFFFFFFFF)
        if text:
            for w in waves:
                by_wave.setdefault(("sound/" + w).lower(), clean(text))

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("aplay")
    saved = 0
    for name in picked:
        data = files.read(name)
        if data is None:
            print(f"  ✗ {Path(name).name}: couldn't be read")
            continue
        dest = out / Path(name).name
        dest.write_bytes(data)
        saved += 1
        print(f"  ✓ {dest.name}" + (f"  “{by_wave[name][:80]}”" if name in by_wave else "  (no caption)"))
        if args.play and player:
            subprocess.run([player, str(dest)])
    print(f"\nSaved {saved} file(s) to {out}")
    return 0 if saved else 1


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Extract a GLaDOS line from Portal 2.")
    ap.add_argument("phrase", nargs="?", default=DEFAULT_PHRASE, help=f'text to look for (default "{DEFAULT_PHRASE}")')
    ap.add_argument("--game", help="Portal 2 folder, if it isn't in a normal Steam library")
    ap.add_argument("--out", default=str(OUT_DIR), help=f"where to save (default {OUT_DIR})")
    ap.add_argument("--play", action="store_true", help="play each file after saving it")
    ap.add_argument("--debug", action="store_true", help="always print the lookup report")
    ap.add_argument("--wav", nargs="+", metavar="NAME",
                    help="extract these files by name instead of searching by words; "
                         "wildcards work, e.g. --wav 'chellgladoswakeup*' '*wakeup*'")
    args = ap.parse_args()

    game = find_game(args.game)
    if not game:
        print("Portal 2 wasn't found. Install it from Steam, or pass its folder with --game.")
        return 1
    print(f"Portal 2: {game}")
    files = GameFiles(game)
    all_names = files.names()

    # 1. captions
    captions_txt, captions_dat = {}, {}
    for n in sorted(x for x in all_names if re.fullmatch(r"resource/(closecaption|subtitles)_english\.(txt|dat)", x)):
        for where, raw in files.read_all(n):
            try:
                if n.endswith(".dat"):
                    parsed = parse_caption_dat(raw)
                    for k, v in parsed.items():
                        captions_dat.setdefault(k, v)
                else:
                    for k, v in parse_caption_txt(decode_text(raw)).items():
                        captions_txt.setdefault(k, v)
            except (ValueError, struct.error) as e:
                print(f"  (couldn't read {where}: {e})")
    if not captions_txt and not captions_dat:
        print("No English caption files were found, so the line can't be looked up by its words.")
        return 1

    # 2. sound scripts: sound name -> wave files
    sounds = {}
    for n in sorted(x for x in all_names if x.startswith("scripts/") and x.endswith(".txt")):
        try:
            raw = files.read(n)
            if raw and b"wave" in raw.lower():
                for k, v in parse_soundscripts(decode_text(raw)).items():
                    if v:
                        sounds.setdefault(k, v)
        except Exception as e:  # one odd script file shouldn't stop the search
            print(f"  (skipping {n}: {e})")
    by_crc = {zlib.crc32(k.encode()) & 0xFFFFFFFF: k for k in sounds}

    if args.wav:
        return extract_named(files, all_names, args, sounds, captions_txt, captions_dat)

    # 3. captions containing the phrase -> wave paths
    want = re.compile(r"\b" + re.escape(norm(args.phrase)) + r"\b")  # whole words: not "it's your"
    matches = []  # (caption text, sound name or None, caption key)
    for token, text in captions_txt.items():
        if want.search(norm(text)):
            matches.append((clean(text), token if token in sounds else None, token))
    for h, text in captions_dat.items():
        if want.search(norm(text)):
            matches.append((clean(text), by_crc.get(h), f"hash {h:08x}"))
    hits = []  # (caption text, sound name, wave path)
    for text, sound, _key in matches:
        for w in sounds.get(sound, []) if sound else []:
            hits.append((text, sound, w))

    def is_glados(h):
        return "glados" in h[1].lower() or "glados" in h[2].lower()

    glados = [h for h in hits if is_glados(h)]
    seen, unique = set(), []
    for h in glados:
        if h[2].lower() not in seen:
            seen.add(h[2].lower())
            unique.append(h)
    # The wake-up line, which starts with "Oh", first.
    unique.sort(key=lambda h: (0 if norm(h[0]).startswith("oh") else 1, len(h[0])))

    if not unique or args.debug:
        if not unique:
            print(f'No GLaDOS line containing "{args.phrase}" was found.')
        report(files, all_names, captions_txt, captions_dat, sounds, matches, hits)
        if not unique:
            return 1

    # 4. extract
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("aplay")
    print(f'GLaDOS lines containing "{args.phrase}":')
    saved = 0
    for text, sound, wave in unique:
        data = files.read("sound/" + wave)
        if data is None:
            print(f"  ✗ {text}\n      ({wave} isn't in the game files)")
            continue
        dest = out / Path(wave).name
        dest.write_bytes(data)
        saved += 1
        print(f"  ✓ {text}\n      {dest}")
        if args.play and player:
            subprocess.run([player, str(dest)])
    if saved:
        print("\nIn Frame Automations, choose File, press Browse… and pick the file above.")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
