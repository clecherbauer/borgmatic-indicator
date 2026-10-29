# borgmatic-indicator

Small GNOME top-bar indicator (AppIndicator) for [borgmatic](https://torsion.org/borgmatic/) running as a **systemd user service**. One Python file, no dependencies beyond what Ubuntu ships.

- Icon shows state: OK · running · stale (no successful backup for 36 h) · failed
- While a backup runs: bytes read, elapsed time and the file currently being processed (read from `/proc`, no log parsing)
- Submenu with the last 15 archives: date, original size, deduplicated (new) size, duration
- Menu: *Back up now*, *Show log*, *Refresh list*
- Desktop notification when a backup finishes or fails

UI strings are German.

## Requirements

- GNOME with the AppIndicator extension (`ubuntu-appindicators@ubuntu.com` on Ubuntu)
- `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `libnotify-bin` (`notify-send`)
- borgmatic ≥ 1.8 run by `~/.config/systemd/user/borgmatic.service` + `borgmatic.timer`

## Setup

1. Let borgmatic write a timestamp after each successful backup (`~/.config/borgmatic/config.yaml`):

   ```yaml
   after_backup:
       - mkdir -p ~/.local/state && date +%s > ~/.local/state/borgmatic-last-success
   ```

2. Install and autostart:

   ```sh
   install -m755 borgmatic-indicator.py ~/.local/bin/
   cp borgmatic-indicator.desktop ~/.config/autostart/
   ~/.local/bin/borgmatic-indicator.py &
   ```

The archive list is fetched with `borgmatic info --last 15 --json` while idle (the repo is locked during a backup) and cached in `~/.local/state/borgmatic-archives.json`, so it still shows when the repository is unreachable.

## License

MIT
