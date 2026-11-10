# Frame Automations for Steam Frame

By joshmanpro79-create · MIT License

Play a sound, speak a phrase, or run a script at a set time. User-level only: no sudo, no Developer Mode, nothing outside your home folder.

## What it can do

| Trigger | Fires | Example |
|---|---|---|
| **Boot** | After the Frame has been awake this long since it was turned on | Play `welcome.ogg` 2 minutes after booting |
| **Wake** | This long after each wake from sleep | Run `sync.sh` 30 seconds after waking |
| **Clock** | At a time of day, in the time zone you pick | Alarm at 2:00 PM every day |

Each automation also has a **Repeat** setting:

- **Daily:** every boot, every wake, or every day.
- **Once:** the next boot or wake only, or a Clock time on a date you pick. Afterwards the row switches itself Off rather than being deleted.
- **Custom:** tick the days of the week it's allowed to fire on.

The action is one of:

- **An audio file:** WAV, OGG or FLAC play right away. MP3, M4A, AAC and similar need the MP3/M4A support the installer offers. Play it once, 2–10 times, or loop until stopped.
- **A script:** `.sh`, `.py`, or any file marked executable. Its output is written to a daily log file.
- **Speak:** type a phrase and it's spoken with the Piper voice Battery Announce installed.

## How the timing works

- **Sleep doesn't count toward Boot.** Suppose a Boot row is set for 2 minutes and you put the Frame to sleep 1 minute after turning it on. That row fires 1 minute after you wake it.
- **Wake ignores short sleeps.** Sleeps under 30 seconds, like taking the headset off briefly, don't count as a wake. If the Frame sleeps again before a Wake row's delay is up, that countdown is cancelled and the next wake starts a fresh one.
- **A cold boot is not a wake.** If you want a sound after both, make two rows.
- **Missed Clock times.** If the Frame was asleep or off at a Clock time, the row fires when it wakes or boots, as long as that's within 10 minutes. Otherwise the time is skipped and noted in the log.
- **New and edited rows only look ahead.** A row you create or change only fires for moments after you save it. For example, adding a 2-minute Boot row an hour after booting waits for the next boot.
- **Sounds never overlap.** They play one at a time, in order. If Battery Announce is speaking, the sound waits until it finishes.
- **While Automations are Off,** anything that comes due is skipped and logged, not saved for later. A one-time row is used up even if it was skipped this way.

## Install (a few minutes, needs internet once)

1. Switch to **Desktop Mode** and copy this folder anywhere in your home folder, e.g. `~/Downloads/frame-automations`.
2. Open **Konsole** and run:
   ```
   cd ~/Downloads/frame-automations
   bash install.sh
   ```
   The installer does the following:
   - Checks that everything it needs is present.
   - Reuses Battery Announce's private window Python if you have it. Otherwise it installs its own (about 70 MB download), only if SteamOS's Python can't draw windows.
   - Asks whether to add MP3/M4A support (about 40 MB).
   - Starts the background service, creates the launchers, and says "Frame automations ready."

## Launchers for Game Mode / VR

The installer creates three launchers. To add them to Steam:

1. In Desktop Mode, open Steam → **Games → Add a Non-Steam Game to My Library**.
2. Tick **Frame Automations**, **Frame Automations On-Off** and **Frame Automations Stop Sound**.
3. Click **Add Selected Programs**.

What each one does:

- **Frame Automations** opens the editor window (below).
- **Frame Automations On-Off** turns all automations on or off and says which state it's in.
- **Frame Automations Stop Sound** stops whatever an automation is playing, including a looping alarm, and clears anything waiting to play.

## The window

The top bar shows the current time in your chosen time zone and how long the Frame has been awake. Below it:

- **Automations On:** the same switch as the On-Off launcher.
- **Time zone:** "System" follows SteamOS's zone; pick another to run Clock rows on that zone's time.
- **Open Logs:** opens the logs folder.
- **MP3/M4A support:** shows whether it's installed, with an **Install** button if not.

Each automation is a two-line row:

- **Line 1:** **On**, **Name**, **Trigger** (Boot / Wake / Clock), then the delay (minutes and seconds, up to 120 minutes) or the clock time (hour, minute, AM/PM). Then **Repeat** (Daily / Once / Custom). Custom shows a checkbox for each day; Once with Clock shows a date dropdown.
- **Line 2:** **Action** (File / Speak). File has a path box, **Browse…** and a note saying whether the file is *audio*, a *script*, *⚠ missing* or *⚠ unsupported*. Speak has a box for the phrase.
- **Line 2, right side:** for audio and speech, how many times to play. For scripts, **Keep logs**: *Do Not Delete* or 1–30 days.
- **▶ Test** runs the row once now (scripts ask first). **✕** deletes the row.

Nothing changes until you press **Save** (or Ctrl+S). The service picks up the new settings within a few seconds. Typing names or phrases needs a keyboard; in Game Mode, open Steam's on-screen keyboard.

**Browse…** uses the KDE file picker when it's available, and falls back to a simpler one otherwise. Files are used where they are, not copied. If one moves, its row shows *⚠ missing* and the log notes it each time the row comes due.

## Logs

Logs are in `~/.local/state/frame-automations/logs/`:

- **Each script** writes to a file named for the day and the script, e.g. `2026-11-09_backup.sh.log`. Each run adds a start line, the script's output, and its exit code.
- **The service** writes what it ran, skipped or missed to `YYYY-MM-DD_frame-automations.log`. These are kept for 30 days.

Each script row's **Keep logs** setting decides how long that script's logs stay. Once an hour, older ones are deleted. If two rows run scripts with the same file name, the longer setting wins. Logs for a script no longer used by any row are left alone.

## From Konsole

```
python3 ~/.local/bin/frame-automations list              # your automations, numbered
python3 ~/.local/bin/frame-automations status            # service, time zone, tools, automations
python3 ~/.local/bin/frame-automations on | off | toggle
python3 ~/.local/bin/frame-automations stop              # stop the sound that's playing
python3 ~/.local/bin/frame-automations test 2            # run row 2 now
python3 ~/.local/bin/frame-automations install-decoder   # add MP3/M4A support
python3 ~/.local/bin/frame-automations gui               # the window
```

To stop the background service completely (rather than turning automations off):
```
export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemctl --user disable --now frame-automations
```

## Settings file

`~/.config/frame-automations/config.json` holds everything the window saves, and the service reloads it within a second. Three other keys can be added by hand:

```json
{
  "missed_grace_minutes": 10,
  "min_sleep_seconds": 30,
  "piper_path": "", "voice_path": ""
}
```

## Troubleshooting

- **No sound:** run `frame-automations status`; it should list a Player. For MP3/M4A files, MP3/M4A should also show a path. Then check `journalctl --user -u frame-automations -n 50` (after the two `export` lines above).
- **Speak does nothing:** `status` should show a Speech line. If it says "none found", install Battery Announce, or set `piper_path` and `voice_path` in the settings file.
- **A script runs but its window doesn't appear:** scripts get the display settings of your Desktop Mode or Game Mode session. Check the script's log for errors.
- **The window doesn't open:** run `bash install.sh` again and read the "Checking the window toolkit" step.
- **"Failed to connect to bus" in Konsole:** run the two `export` lines above first.

## Extra: GLaDOS lines from Portal 2

`extract-glados-line.py` copies a GLaDOS voice line out of your own Portal 2 install. Portal 2 must be installed.

1. It finds lines by their words, using the game's English captions and sound scripts. With no phrase given, it looks for her "…it's you." wake-up line.
2. It saves the `.wav` to `~/Music/Portal 2 GLaDOS/` and prints the file's path.
3. You pick that file with **Browse…** in a row.

```
python3 ~/Downloads/frame-automations/extract-glados-line.py           # the "it's you" line
python3 ~/Downloads/frame-automations/extract-glados-line.py --play    # and play it
python3 ~/Downloads/frame-automations/extract-glados-line.py "long time"   # any other phrase
python3 ~/Downloads/frame-automations/extract-glados-line.py --wav '*wakeup*'  # by file name (wildcards work)
```
If a search finds nothing, the script prints a lookup report showing what each step found. `--wav` extracts files by name and shows each one's caption where the game has one.
If several lines match, all are saved, with lines starting "Oh" listed first. Portal 2 on an SD card is found through Steam's library list; otherwise pass `--game "/path/to/Portal 2"`. Nothing in the game folder is changed.

## Uninstall
```
bash ~/Downloads/frame-automations/uninstall.sh
```
It asks whether to keep your automations and logs. It never touches your own audio files or scripts. Then remove the three Non-Steam shortcuts from your library.

## License

Copyright (c) 2026 joshmanpro79-create. Released under the MIT License; see `LICENSE`.

The installer can download third-party components that keep their own licenses: a [python-build-standalone](https://github.com/astral-sh/python-build-standalone) Python (only if needed for the window) and a [static ffmpeg build](https://johnvansickle.com/ffmpeg/) (only if you accept MP3/M4A support). Speak actions use the Piper voice installed by Battery Announce. None of these are included in this package.
