# Battery Announce for Steam Frame

By joshmanpro79-create · MIT License

Spoken battery alerts using an offline Piper voice. User-level only: no sudo, no Developer Mode, nothing outside your home folder.

## What it says

| Situation | Announcement |
|---|---|
| On battery | At 40, 30 and 20 percent (levels and messages editable) |
| On battery, below 15% | Every 1%: "Battery low. 14 percent." From 5%: "Battery critical." |
| Charger connected | "Charging. 62 percent. About 45 minutes to full." (after ~8 s) |
| Charger removed | "On battery. 98 percent." |
| While charging | Nothing by default; add levels such as "80 percent charged." |
| Reaches 100% | "Fully charged." |
| Not yet calibrated | At 80, 60, 40 and 20 percent: "Battery 80 percent. Calibration requested for accuracy. Please open Battery Calibrate." |
| Manual readout | "Charging / Discharging. 62 percent. Estimated 45 minutes to full / 2 hours remaining." |

Each level is spoken once per discharge (or per charge); small up-and-down wobbles don't repeat it. All of these can be changed in **Battery Alerts**.

## Install (about 2 minutes, needs internet once)

1. Switch to **Desktop Mode** and copy this folder anywhere in your home folder, e.g. `~/Downloads/battery-announce`.
2. Open **Konsole** and run:
   ```
   cd ~/Downloads/battery-announce
   bash install.sh
   ```
   It first checks that everything it needs is present. If SteamOS's Python can't draw the Battery Calibrate and Battery Alerts windows (it usually lacks the tkinter window toolkit), it installs a private copy of Python that can (about 70 MB download, 190 MB on disk, only in your home folder). It then downloads Piper (~25 MB) and the voice (~60 MB), installs the service, and says "Battery announcements ready."
3. **Calibrate** so the spoken number matches Steam: launch **Battery Calibrate** (see below), or from Konsole run `python3 ~/.local/bin/battery-announce calibrate 87` with the % Steam shows.

## Launchers for Game Mode / VR

The installer creates four launchers. In Desktop Mode open Steam → **Games → Add a Non-Steam Game to My Library**, tick **Battery Readout**, **Battery Announce On-Off**, **Battery Calibrate** and **Battery Alerts**, then click **Add Selected Programs**. Launch them from your library like any game:

- **Battery Readout** speaks the current status once. It works even when announcements are muted.
- **Battery Announce On-Off** toggles the automatic announcements and says which state it's in.
- **Battery Calibrate** opens a window showing the raw **Battery sensor** % and what the **Announcer says**. Set the big number to what Steam's battery indicator shows (point and click the −10 / −1 / +1 / +10 buttons, or use the arrow keys), then **Save** (or Enter). If it already matches, nothing changes. If it differs, the correction is saved, the running announcer picks it up within seconds, and it speaks the new value. The window closes itself 10 seconds later.
- **Battery Alerts** opens the alert settings (see below).

## Alert settings (Battery Alerts)

Four pages, picked with the tabs at the top. Nothing changes until you press **Save**; the running announcer then uses the new settings within a few seconds. Every message has a **▶** button to hear it.

- **On battery:** the levels spoken as the battery drops. Pick a level from its dropdown (1–99%; click anywhere on the box, the list scrolls), **✕** removes it, **+ Add level** adds one. A level already in the list can't be picked twice. Each level has its own message. A message left as the default wording ("Battery {p} percent.") keeps following the default.
- **Low battery:** dropdowns for where the every-1% countdown starts ("Off" turns it off) and where the wording changes to "critical", with both messages.
- **Charging:** levels spoken as the battery charges up, e.g. 80%. Empty by default.
- **Charger Plugin Status:** turn the plugged-in, unplugged and fully-charged messages **On/Off** and reword them.

**Defaults** restores the factory settings (press Save to keep them). In messages, `{p}` is the percentage, `{eta}` the time-to-full sentence and `{t}` the time. Typing a message needs a keyboard: in Game Mode, open Steam's on-screen keyboard.

A level can only appear once per list. When several levels are passed at once (e.g. after sleep), only one message is spoken. A calibration reminder replaces a level message at the same moment.

From Konsole, the same settings:
```
python3 ~/.local/bin/battery-announce alerts                                  # show everything
python3 ~/.local/bin/battery-announce alerts add battery 50 "Half way. {p} percent."
python3 ~/.local/bin/battery-announce alerts remove battery 30
python3 ~/.local/bin/battery-announce alerts add charging 80
python3 ~/.local/bin/battery-announce alerts message full "Battery full."
python3 ~/.local/bin/battery-announce alerts off unplug
python3 ~/.local/bin/battery-announce alerts reset                            # defaults
python3 ~/.local/bin/battery-announce alerts help                             # everything else
```
If the window can't be drawn (for example if the private Python failed to install), the Battery Alerts launcher shows these Konsole instructions instead.

### Calibration reminders
Until you've calibrated near each of 80, 60, 40 and 20 percent, the announcer asks for calibration when the battery drops past those levels. Reminders only play on battery and repeat on later discharges until they're cleared. At 40% and 20% the reminder replaces the normal level announcement, so you hear one message, not two.

Each time you save in Battery Calibrate, the reminder **closest to the level you calibrated at** is cleared, even if a different reminder prompted you. For example, if the 80% reminder plays but you calibrate at 64%, the 60% reminder is cleared and 80% stays pending. Saving counts even when the window says "Already matches Steam". If the closest reminder is already cleared, that save doesn't clear another one. Calibrations you saved before this update count the same way.

The window lists the reminders still pending. **Reset** brings all four back. To turn reminders off, add `"calibration_prompts": false` to the config file.

### Calibration tips
- Calibrating at several levels lets it correct across the whole range. A re-calibration near an earlier level replaces that earlier reading.
- Both readings move while you look, so save soon after checking Steam's number. The window refreshes every 2 seconds.
- **Reset** clears all calibration, so the announcer goes back to the raw sensor value.
- If the window can't be drawn at all, the launcher falls back to a simple slider dialog (zenity) or input box (kdialog) that does the same thing.

From Konsole the same commands are:
```
python3 ~/.local/bin/battery-announce now      # readout
python3 ~/.local/bin/battery-announce off      # mute automatic announcements
python3 ~/.local/bin/battery-announce on
python3 ~/.local/bin/battery-announce toggle
python3 ~/.local/bin/battery-announce status   # readings, correction, what it found
python3 ~/.local/bin/battery-announce calibrate-gui   # the calibration window
python3 ~/.local/bin/battery-announce alerts-gui      # the alert settings window
python3 ~/.local/bin/battery-announce test     # hear every phrase
```

To stop the background service completely (rather than mute it):
```
export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemctl --user disable --now battery-announce
```

## Customize

Settings live in `~/.config/battery-announce/config.json` (created the first time you save a setting). Battery Alerts and Battery Calibrate write it for you; you can also edit it by hand. The service reloads it within seconds. Other keys you can set:

```json
{
  "calibration_prompt_levels": [80, 60, 40, 20],
  "calibration_prompts": true,
  "volume": 1.0,
  "length_scale": 1.0,
  "plug_in_delay_seconds": 8
}
```
`length_scale` above 1.0 speaks slower. Every spoken phrase is under `"phrases"` in the script's `DEFAULTS` and can be overridden the same way.

## Troubleshooting

- **Battery Calibrate or Battery Alerts doesn't open:** run `bash install.sh` again from Konsole and read the "Checking the window toolkit" step. It installs the private window Python if it's missing. `battery-announce status` shows which Python draws the windows on the "Window Python" line.

- **No sound:** run `battery-announce status`; Piper, Voice and Player should all show paths. Check the log with `journalctl --user -u battery-announce -n 50` (after the two `export` lines above).
- **"Failed to connect to bus" in Konsole:** run the two `export` lines above first. Desktop Mode's nested session can't always reach your main session's service manager.
- **Percentage still off:** `battery-announce reset-calibration`, then calibrate again at two different levels.
- **Time estimate missing:** right after plugging in, the charge rate can read zero. The manual readout then uses the rate the service measured over the last few minutes.

## Uninstall
```
bash ~/Downloads/battery-announce/uninstall.sh
```
Then remove the four Non-Steam shortcuts from your library.

## License

Copyright (c) 2026 joshmanpro79-create. Released under the MIT License; see `LICENSE` for the full text.

The installer downloads two third-party components that keep their own licenses: the [Piper](https://github.com/rhasspy/piper) speech engine and the `en_US-lessac-medium` [Piper voice](https://huggingface.co/rhasspy/piper-voices). They aren't included in this package.
