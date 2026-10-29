#!/usr/bin/env python3
"""GNOME-Leistenindikator für borgmatic: Status, laufendes Backup, Archivliste."""
import json, os, subprocess, threading, time
from datetime import datetime

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AI

UNIT = "borgmatic.service"
STAMP = os.path.expanduser("~/.local/state/borgmatic-last-success")
CACHE = os.path.expanduser("~/.local/state/borgmatic-archives.json")
HOME = os.path.expanduser("~")
STALE_H = 36  # so lange ohne erfolgreiches Backup -> Warnung


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def gb(n):
    return f"{n / 1e9:,.1f} GB".replace(",", "'")


def dur(s):
    s = int(s)
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min"


def ago(ts):
    h = (time.time() - ts) / 3600
    return f"vor {int(h * 60)} min" if h < 1 else f"vor {h:.0f} h" if h < 48 else f"vor {h / 24:.0f} Tagen"


def borg_progress():
    """Fortschritt des laufenden 'borg create' aus /proc (kein Log-Spam nötig)."""
    pid = sh("pgrep", "-n", "-f", "^/usr/bin/python3 /usr/bin/borg create")
    if not pid:
        return None
    try:
        io = dict(l.split(": ") for l in open(f"/proc/{pid}/io").read().splitlines())
        cur = ""
        for fd in os.listdir(f"/proc/{pid}/fd"):
            p = os.readlink(f"/proc/{pid}/fd/{fd}")
            if p.startswith(HOME) and "/.cache/" not in p:
                cur = p[len(HOME) + 1:]
        return int(io["rchar"]), int(sh("ps", "-o", "etimes=", "-p", pid)), cur
    except (OSError, ValueError, KeyError):
        return None


class Indicator:
    def __init__(self):
        self.ind = AI.Indicator.new("borgmatic", "emblem-ok-symbolic", AI.IndicatorCategory.SYSTEM_SERVICES)
        self.ind.set_status(AI.IndicatorStatus.ACTIVE)
        self.prev_state = None
        self.archives = self.load_cache()
        self.fetching = False

        m = Gtk.Menu()
        self.status_item = self.label_item(m)
        self.detail_item = self.label_item(m)
        self.file_item = self.label_item(m)
        m.append(Gtk.SeparatorMenuItem())
        self.arch_root = Gtk.MenuItem(label="Backups")
        m.append(self.arch_root)
        m.append(Gtk.SeparatorMenuItem())
        self.run_item = self.action(m, "Jetzt sichern", lambda *_: sh("systemctl", "--user", "start", "--no-block", UNIT))
        self.action(m, "Log anzeigen", lambda *_: subprocess.Popen(
            ["gnome-terminal", "--", "journalctl", "--user", "-u", UNIT, "-n", "200", "-f"]))
        self.action(m, "Liste aktualisieren", lambda *_: self.fetch_archives())
        m.show_all()
        self.ind.set_menu(m)

        self.render_archives()
        self.tick()
        self.fetch_archives()
        GLib.timeout_add_seconds(5, self.tick)
        GLib.timeout_add_seconds(1800, lambda: self.fetch_archives() or True)

    @staticmethod
    def label_item(menu):
        i = Gtk.MenuItem(label="")
        i.set_sensitive(False)
        menu.append(i)
        return i

    @staticmethod
    def action(menu, label, cb):
        i = Gtk.MenuItem(label=label)
        i.connect("activate", cb)
        menu.append(i)
        return i

    # --- Archivliste (Repo ist während eines Backups gesperrt -> nur im Leerlauf, Ergebnis cachen)
    def load_cache(self):
        try:
            return json.load(open(CACHE))
        except (OSError, ValueError):
            return {"fetched": 0, "archives": []}

    def fetch_archives(self):
        if self.fetching or self.prev_state == "running":
            return
        self.fetching = True

        def work():
            try:
                out = subprocess.run(["borgmatic", "info", "--last", "15", "--json"],
                                     capture_output=True, text=True, timeout=180).stdout
                arch = json.loads(out)[0]["archives"]
                data = {"fetched": time.time(), "archives": [
                    {"time": a["start"], "size": a["stats"]["original_size"],
                     "new": a["stats"]["deduplicated_size"], "files": a["stats"]["nfiles"],
                     "duration": a["duration"]} for a in reversed(arch)]}
                with open(CACHE, "w") as f:
                    json.dump(data, f)
                GLib.idle_add(self.set_archives, data)
            except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
                pass  # NAS nicht erreichbar -> alter Cache bleibt
            finally:
                self.fetching = False

        threading.Thread(target=work, daemon=True).start()

    def set_archives(self, data):
        self.archives = data
        self.render_archives()

    def render_archives(self):
        arch = self.archives["archives"]
        sub = Gtk.Menu()
        if self.archives["fetched"]:
            i = Gtk.MenuItem(label=f"Stand: {ago(self.archives['fetched'])}")
            i.set_sensitive(False)
            sub.append(i)
            sub.append(Gtk.SeparatorMenuItem())
        for a in arch:
            t = datetime.fromisoformat(a["time"]).strftime("%a %d.%m. %H:%M")
            i = Gtk.MenuItem(label=f"{t}   {gb(a['size'])}  ·  +{gb(a['new'])} neu  ·  {dur(a['duration'])}")
            i.set_tooltip_text(f"{a['files']:,} Dateien".replace(",", "'"))
            sub.append(i)
        if not arch:
            i = Gtk.MenuItem(label="(noch keine Liste)")
            i.set_sensitive(False)
            sub.append(i)
        sub.show_all()
        self.arch_root.set_label(f"Backups ({len(arch)})")
        self.arch_root.set_submenu(sub)

    # --- Status
    def tick(self):
        active = sh("systemctl", "--user", "is-active", UNIT)
        result = sh("systemctl", "--user", "show", UNIT, "-p", "Result", "--value")
        try:
            last_ok = float(open(STAMP).read())
        except (OSError, ValueError):
            last_ok = 0
        nxt = sh("systemctl", "--user", "list-timers", "borgmatic.timer", "--no-legend", "-o", "json")
        try:
            nxt = datetime.fromtimestamp(json.loads(nxt)[0]["next"] / 1e6).strftime("%a %H:%M")
        except (ValueError, IndexError, KeyError, TypeError):
            nxt = "?"

        if active == "activating":
            state, icon = "running", "emblem-synchronizing-symbolic"
            p = borg_progress()
            self.status_item.set_label("Backup läuft …")
            if p:
                read, secs, cur = p
                self.detail_item.set_label(f"{gb(read)} gelesen · seit {dur(secs)}")
                self.file_item.set_label("… " + (cur[-60:] if cur else ""))
                self.ind.set_label(gb(read), "")
            else:
                self.detail_item.set_label("Vorbereitung / Aufräumen")
                self.file_item.set_label("")
                self.ind.set_label("", "")
        else:
            self.ind.set_label("", "")
            self.file_item.set_label(f"Nächstes: {nxt}")
            if result not in ("success", ""):
                state, icon = "failed", "dialog-error-symbolic"
                self.status_item.set_label(f"Letzter Lauf fehlgeschlagen ({result})")
            elif time.time() - last_ok > STALE_H * 3600:
                state, icon = "stale", "dialog-warning-symbolic"
                self.status_item.set_label("Backup zu alt")
            else:
                state, icon = "ok", "emblem-ok-symbolic"
                self.status_item.set_label("Backup OK")
            self.detail_item.set_label(f"Letztes erfolgreiches: {ago(last_ok)}" if last_ok else "Noch kein Backup")

        if state != self.prev_state and self.prev_state is not None:
            if state == "failed":
                sh("notify-send", "-u", "critical", "-i", "dialog-error", "Backup fehlgeschlagen",
                   "Details: Indikator → Log anzeigen")
            elif self.prev_state == "running" and state == "ok":
                sh("notify-send", "-i", "emblem-ok-symbolic", "Backup fertig")
            if self.prev_state == "running":
                self.prev_state = state
                self.fetch_archives()
        self.prev_state = state
        self.run_item.set_sensitive(state != "running")
        self.ind.set_icon_full(icon, state)
        return True


if __name__ == "__main__":
    Indicator()
    Gtk.main()
