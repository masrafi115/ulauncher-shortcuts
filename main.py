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
from ulauncher.api.shared.event import KeywordQueryEvent, PreferencesEvent, PreferencesUpdateEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction
from ulauncher.api.shared.action.RunScriptAction import RunScriptAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction

logger = logging.getLogger(__name__)

class ShortcutsPlugin(Extension):
    def __init__(self):
        super(ShortcutsPlugin, self).__init__()
        self.preferences = {}
        self.shortcuts_file = None
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent, PreferencesEventListener())

    def get_storage_path(self):
        pref_path = self.preferences.get('shortcuts_path', '~/.config/ulauncher/shortcuts.json')
        return Path(pref_path).expanduser()

    def load_shortcuts(self):
        path = self.get_storage_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump([], f)
            return []
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return []

    def save_shortcuts(self, data):
        path = self.get_storage_path()
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)

class PreferencesEventListener(EventListener):
    def on_event(self, event, extension):
        if hasattr(event, 'preferences'):
            extension.preferences = event.preferences
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

        # -----------------------------------------------------------------
        # SECTION 1: Intercept Native Management Commands (Add, Remove, Group)
        # -----------------------------------------------------------------
        if raw_args.startswith("commit_action "):
            payload = raw_args[14:].strip()
            bits = payload.split(maxsplit=2)
            if len(bits) >= 2:
                action_type, target_key = bits[0], bits[1]
                extra_data = bits[2] if len(bits) == 3 else ""

                if action_type == "add":
                    # Expecting data format: type | path/arguments
                    if "|" in extra_data:
                        stype, sdata = [x.strip() for x in extra_data.split("|", 1)]
                        # Format entry data mimicking target schema
                        new_entry = {"Type": stype.capitalize(), "Key": target_key}
                        if stype.lower() in ["directory", "file", "url"]:
                            new_entry["Path"] = sdata
                        elif stype.lower() == "shell":
                            new_entry["ShellType"] = "Cmd"
                            new_entry["Arguments"] = sdata
                            new_entry["Silent"] = True
                        
                        # Strip previous duplicate keys
                        shortcuts = [s for s in shortcuts if s.get("Key") != target_key]
                        shortcuts.append(new_entry)
                        extension.save_shortcuts(shortcuts)

                elif action_type == "remove":
                    shortcuts = [s for s in shortcuts if s.get("Key") != target_key]
                    extension.save_shortcuts(shortcuts)

                elif action_type == "add_group":
                    # Extra data contains shortcut keys space separated
                    group_keys = extra_data.split()
                    new_group = {"Type": "Group", "Key": target_key, "Keys": group_keys}
                    shortcuts = [s for s in shortcuts if s.get("Key") != target_key]
                    shortcuts.append(new_group)
                    extension.save_shortcuts(shortcuts)

                return RenderResultListAction([
                    ExtensionResultItem(
                        icon=icon,
                        name="✨ Action Processed Successfully!",
                        description="Press Enter to return to main commands terminal context.",
                        on_enter=SetUserQueryAction(f"{keyword} ")
                    )
                ])

        # -----------------------------------------------------------------
        # SECTION 2: Parsing Inline Admin Utility Rules (add, remove, group add)
        # -----------------------------------------------------------------
        bits = raw_args.split(maxsplit=3)
        cmd_trigger = bits[0].lower() if len(bits) > 0 else ""

        # Syntax A: 'q add <type> <name> <payload>'
        if cmd_trigger == "add" and len(bits) >= 3:
            stype = bits[1]
            sname = bits[2]
            spayload = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"➕ Register new {stype} Shortcut: '{sname}'",
                    description=f"Payload Target: {spayload}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add {sname} {stype} | {spayload}")
                )
            ])

        # Syntax B: 'q remove <name>'
        if cmd_trigger == "remove" and len(bits) >= 2:
            sname = bits[1]
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"🗑️ Remove Shortcut Named: '{sname}'",
                    description="Press Enter to delete this from configurations mapping registry.",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action remove {sname}")
                )
            ])

        # Syntax C: 'q group add <group_name> <keys...>'
        if cmd_trigger == "group" and len(bits) >= 3 and bits[1].lower() == "add":
            gname = bits[2]
            gkeys = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"📁 Package New Collection Group: '{gname}'",
                    description=f"Links shortcuts keys: {gkeys}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add_group {gname} {gkeys}")
                )
            ])

        # -----------------------------------------------------------------
        # SECTION 3: Standard Shortcut Resolution & Execution Engine
        # -----------------------------------------------------------------
        # If no configuration strings match utility triggers, treat raw_args as search query
        shortcut_map = {s.get("Key"): s for s in shortcuts if "Key" in s}

        if raw_args:
            # Fuzzy match keys against user arguments input string query tracking
            matched_keys = difflib.get_close_matches(raw_args, list(shortcut_map.keys()), n=5, cutoff=0.2)
            if not matched_keys:
                matched_keys = [k for k in shortcut_map.keys() if raw_args.lower() in k.lower()]
            targets = [shortcut_map[k] for k in matched_keys]
        else:
            targets = shortcuts

        # Generate output rows based on the target shortcut schemas
        for sc in targets:
            stype = sc.get("Type", "Unknown")
            skey = sc.get("Key", "Unknown")

            if stype == "Directory":
                path = os.path.expandvars(sc.get("Path", ""))
                items.append(ExtensionResultItem(
                    icon=icon, name=f"📁 {skey}", description=f"Open Directory Explorer: {path}",
                    on_enter=RunScriptAction(f"xdg-open '{path}'")
                ))
            elif stype == "File":
                path = os.path.expandvars(sc.get("Path", ""))
                items.append(ExtensionResultItem(
                    icon=icon, name=f"📄 {skey}", description=f"Open File Document Assets: {path}",
                    on_enter=RunScriptAction(f"xdg-open '{path}'")
                ))
            elif stype == "Url":
                url = sc.get("Path", "")
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                items.append(ExtensionResultItem(
                    icon=icon, name=f"🌐 {skey}", description=f"Launch Web Browser Target Address: {url}",
                    on_enter=RunScriptAction(f"xdg-open '{url}'")
                ))
            elif stype == "Shell":
                cmd = sc.get("Arguments", "")
                items.append(ExtensionResultItem(
                    icon=icon, name=f"⚡ {skey}", description=f"Execute Native Background Script Payload: {cmd}",
                    on_enter=RunScriptAction(cmd)
                ))
            elif stype == "Group":
                # Handle group collections launching strategy
                gkeys = sc.get("Keys", [])
                chained_commands = []
                for k in gkeys:
                    child = shortcut_map.get(k)
                    if child:
                        if child.get("Type") in ["Directory", "File"]:
                            chained_commands.append(f"xdg-open '{os.path.expandvars(child.get('Path'))}'")
                        elif child.get("Type") == "Url":
                            u = child.get("Path")
                            if not u.startswith(("http://", "https://")): u = "https://" + u
                            chained_commands.append(f"xdg-open '{u}'")
                        elif child.get("Type") == "Shell":
                            chained_commands.append(child.get("Arguments"))
                
                exec_payload = " & ".join(chained_commands) if chained_commands else "true"
                items.append(ExtensionResultItem(
                    icon=icon, name=f"📦 Multi-Group Collection: {skey}", 
                    description=f"Launches {len(gkeys)} aggregated shortcuts concurrently",
                    on_enter=RunScriptAction(exec_payload)
                ))

        # Helper placeholder context if mapping dictionary arrays are completely empty
        if not items:
            items.append(ExtensionResultItem(
                icon=icon,
                name="Shortcuts System Engine Context Terminal",
                description="Commands layout: 'add [type] [name] [payload]' or 'remove [name]'",
                on_enter=DoNothingAction()
            ))

        return RenderResultListAction(items)

if __name__ == '__main__':
    ShortcutsPlugin().run()
