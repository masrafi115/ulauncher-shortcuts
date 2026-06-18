import os
import sys
import json
import logging
import subprocess
import webbrowser
import difflib
from pathlib import Path

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, PreferencesEvent, PreferencesUpdateEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction

logger = logging.getLogger(__name__)

class ShortcutsPlugin(Extension):
    def __init__(self):
        super(ShortcutsPlugin, self).__init__()
        self.preferences = {'shortcuts_path': '~/.config/ulauncher/my-shortcuts.json'}
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent, PreferencesEventListener())
        self.subscribe(ItemEnterEvent, ItemEnterEventListener())

    def get_storage_path(self):
        pref_path = self.preferences.get('shortcuts_path') if self.preferences else None
        if not pref_path:
            pref_path = '~/.config/ulauncher/my-shortcuts.json'
        return Path(pref_path).expanduser()

    def load_shortcuts(self):
        try:
            path = self.get_storage_path()
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'w') as f:
                    json.dump({}, f)
                return {}
            with open(path, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"Failed to load shortcuts: {e}")
            return {}

    def save_shortcuts(self, data):
        try:
            path = self.get_storage_path()
            with open(path, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save shortcuts: {e}")

class PreferencesEventListener(EventListener):
    def on_event(self, event, extension):
        if not extension.preferences:
            extension.preferences = {}
        if hasattr(event, 'preferences'):
            extension.preferences.update(event.preferences)
        else:
            extension.preferences[event.id] = event.new_value

class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
        keyword = event.get_keyword()
        argument = event.get_argument() or ""
        raw_args = argument.strip()
        shortcuts = extension.load_shortcuts()
        items = []
        icon = "images/icon.png"

        def extract_target_string(config_dict):
            return config_dict.get("Path", config_dict.get("Url", config_dict.get("Arguments", "")))

        # -----------------------------------------------------------------
        # MANAGEMENT UTILITIES (add, remove, group)
        # -----------------------------------------------------------------
        if raw_args.startswith("commit_action "):
            payload = raw_args[14:].strip()
            bits = payload.split(maxsplit=2)
            if len(bits) >= 2:
                action_type, target_key = bits[0], bits[1]
                extra_data = bits[2] if len(bits) == 3 else ""

                if action_type == "add" and "|" in extra_data:
                    stype, sdata = [x.strip() for x in extra_data.split("|", 1)]
                    new_entry = {"Type": stype.capitalize()}
                    
                    if stype.lower() == "url":
                        new_entry["Url"] = sdata
                    elif stype.lower() in ["directory", "file"]:
                        new_entry["Path"] = sdata
                    elif stype.lower() == "shell":
                        new_entry["ShellType"] = "Cmd"
                        new_entry["Arguments"] = sdata
                        new_entry["Silent"] = True
                    
                    shortcuts[target_key] = new_entry
                    extension.save_shortcuts(shortcuts)

                elif action_type == "remove":
                    if target_key in shortcuts:
                        del shortcuts[target_key]
                    extension.save_shortcuts(shortcuts)

                elif action_type == "add_group":
                    group_keys = extra_data.split()
                    shortcuts[target_key] = {"Type": "Group", "Keys": group_keys}
                    extension.save_shortcuts(shortcuts)

                return RenderResultListAction([
                    ExtensionResultItem(
                        icon=icon, name="✨ Action Processed Successfully!",
                        description="Press Enter to return to main shortcuts panel.",
                        on_enter=SetUserQueryAction(f"{keyword} ")
                    )
                ])

        bits = raw_args.split(maxsplit=3)
        cmd_trigger = bits[0].lower() if len(bits) > 0 else ""

        if cmd_trigger == "add" and len(bits) >= 3:
            stype = bits[1]
            skey = bits[2]
            spayload = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon, name=f"➕ Register new {stype} Shortcut: '{skey}'", description=f"Payload: {spayload}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add {skey} {stype} | {spayload}")
                )
            ])

        if cmd_trigger == "remove" and len(bits) >= 2:
            skey = bits[1]
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon, name=f"🗑️ Remove Shortcut Named: '{skey}'", description="Press Enter to permanently clear this shortcut.",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action remove {skey}")
                )
            ])

        if cmd_trigger == "group" and len(bits) >= 3 and bits[1].lower() == "add":
            gkey = bits[2]
            gkeys = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon, name=f"📁 Package New Collection Group: '{gkey}'", description=f"Links shortcuts keys: {gkeys}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add_group {gkey} {gkeys}")
                )
            ])

        # -----------------------------------------------------------------
        # INTERFACE LOOP MATCH ENGINE
        # -----------------------------------------------------------------
        if raw_args:
            search_bits = raw_args.split(maxsplit=1)
            user_key = search_bits[0]
            user_arg = search_bits[1].strip() if len(search_bits) == 2 else ""
            
            if user_key in shortcuts and user_arg:
                sc = shortcuts[user_key]
                stype = sc.get("Type", "Unknown")
                desc = extract_target_string(sc)
                payload_data = {"args": user_arg, "config": sc}
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon=icon, name=f"🚀 Run {user_key} with args", description=f"Query: '{user_arg}' -> {desc}",
                        on_enter=ExtensionCustomAction(payload_data, keep_app_open=False)
                    )
                ])

            matched_keys = difflib.get_close_matches(raw_args, list(shortcuts.keys()), n=8, cutoff=0.1)
            if not matched_keys:
                matched_keys = [k for k in shortcuts.keys() if raw_args.lower() in k.lower()]
            targets = [(k, shortcuts[k]) for k in matched_keys if k in shortcuts]
        else:
            targets = list(shortcuts.items())

        for skey, sc in targets:
            stype = sc.get("Type", "Unknown")
            prefix = "📁" if stype in ["Directory", "File"] else "🌐" if stype == "Url" else "⚡" if stype == "Shell" else "📦"
            
            desc = extract_target_string(sc) if stype != "Group" else f"Chained components: {', '.join(sc.get('Keys', []))}"
            payload_data = {"args": "", "config": sc}

            items.append(ExtensionResultItem(
                icon=icon,
                name=f"{prefix} [{str(skey)}] {stype} Shortcut",
                description=f"Target: {desc}",
                on_enter=ExtensionCustomAction(payload_data, keep_app_open=False)
            ))

        if not items:
            items.append(ExtensionResultItem(
                icon=icon, name="Shortcuts Manager Hub",
                description="Syntax: add [type] [key] [target]",
                on_enter=DoNothingAction()
            ))

        return RenderResultListAction(items[:15])

# -----------------------------------------------------------------
# SMART EXECUTION CONTAINER (ROUTES TO SYSTEM CORE APPS DYNAMICALLY)
# -----------------------------------------------------------------
class ItemEnterEventListener(EventListener):
    def on_event(self, event, extension):
        payload = event.get_data()
        if not payload:
            return
            
        user_arg = payload.get("args", "")
        sc = payload.get("config", {})
        stype = sc.get("Type", "Unknown")

        def resolve_args(target_str, args_str):
            if not target_str: return ""
            return target_str.replace("${q}", args_str).replace("%s", args_str) if args_str else target_str.replace("${q}", "").replace("%s", "")

        def execute_uri(uri_string):
            """Intelligently routes URLs to the browser, and protocols to native applications."""
            if not uri_string: return
            
            # Detect native application schemas (obsidian://, vlc://, etc.)
            if "://" in uri_string and not uri_string.startswith(("http://", "https://")):
                subprocess.Popen(["xdg-open", uri_string], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Normal HTTP links go out to standard browser
                if "://" not in uri_string:
                    uri_string = "https://" + uri_string
                webbrowser.open(uri_string)

        if stype == "Url":
            url = sc.get("Url", sc.get("Path", ""))
            url = resolve_args(url, user_arg)
            execute_uri(url)

        elif stype in ["Directory", "File"]:
            path = os.path.expandvars(sc.get("Path", sc.get("Url", "")))
            if path and os.path.exists(path):
                subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif stype == "Shell":
            cmd = sc.get("Arguments", "")
            cmd = resolve_args(cmd, user_arg)
            if cmd:
                subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif stype == "Group":
            shortcuts = extension.load_shortcuts()
            for k in sc.get("Keys", []):
                child = shortcuts.get(k)
                if not child: continue
                ctype = child.get("Type")
                
                if ctype in ["Directory", "File"]:
                    p = os.path.expandvars(child.get("Path", child.get("Url", "")))
                    if p: subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif ctype == "Url":
                    u = child.get("Url", child.get("Path", ""))
                    u = resolve_args(u, user_arg)
                    execute_uri(u)
                elif ctype == "Shell":
                    c = child.get("Arguments", "")
                    c = resolve_args(c, user_arg)
                    if c: subprocess.Popen(c, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == '__main__':
    ShortcutsPlugin().run()
