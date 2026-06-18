import os
import sys
import json
import logging
import subprocess
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
        # Isolated default database specifically named my-shortcuts.json
        self.preferences = {'shortcuts_path': '~/.config/ulauncher/my-shortcuts.json'}
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent, PreferencesEventListener())

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

        # -----------------------------------------------------------------
        # SECTION 1: Intercept Native Management Commands
        # -----------------------------------------------------------------
        if raw_args.startswith("commit_action "):
            payload = raw_args[14:].strip()
            bits = payload.split(maxsplit=2)
            if len(bits) >= 2:
                action_type, target_key = bits[0], bits[1]
                extra_data = bits[2] if len(bits) == 3 else ""

                if action_type == "add":
                    if "|" in extra_data:
                        stype, sdata = [x.strip() for x in extra_data.split("|", 1)]
                        new_entry = {"Type": stype.capitalize(), "Key": target_key}
                        
                        if stype.lower() in ["directory", "file", "url"]:
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
                    new_group = {"Type": "Group", "Key": target_key, "Keys": group_keys}
                    shortcuts[target_key] = new_group
                    extension.save_shortcuts(shortcuts)

                return RenderResultListAction([
                    ExtensionResultItem(
                        icon=icon,
                        name="✨ Action Processed Successfully!",
                        description="Press Enter to return to main shortcuts panel.",
                        on_enter=SetUserQueryAction(f"{keyword} ")
                    )
                ])

        # -----------------------------------------------------------------
        # SECTION 2: Parsing Inline Admin Utility Rules
        # -----------------------------------------------------------------
        bits = raw_args.split(maxsplit=3)
        cmd_trigger = bits[0].lower() if len(bits) > 0 else ""

        # Syntax: q add <type> <key> <payload>
        if cmd_trigger == "add" and len(bits) >= 3:
            stype = bits[1]
            skey = bits[2]
            spayload = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"➕ Register new {stype} Shortcut: '{skey}'",
                    description=f"Payload: {spayload}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add {skey} {stype} | {spayload}")
                )
            ])

        # Syntax: q remove <key>
        if cmd_trigger == "remove" and len(bits) >= 2:
            skey = bits[1]
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"🗑️ Remove Shortcut Named: '{skey}'",
                    description="Press Enter to permanently clear this shortcut.",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action remove {skey}")
                )
            ])

        # Syntax: q group add <group_key> <keys...>
        if cmd_trigger == "group" and len(bits) >= 3 and bits[1].lower() == "add":
            gkey = bits[2]
            gkeys = bits[3] if len(bits) == 4 else ""
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=icon,
                    name=f"📁 Package New Collection Group: '{gkey}'",
                    description=f"Links shortcuts keys: {gkeys}",
                    on_enter=SetUserQueryAction(f"{keyword} commit_action add_group {gkey} {gkeys}")
                )
            ])

        # -----------------------------------------------------------------
        # SECTION 3: Standard Shortcut Resolution & Execution Engine
        # -----------------------------------------------------------------
        search_bits = raw_args.split(maxsplit=1)
        user_key = search_bits[0] if len(search_bits) > 0 else ""
        user_arg = search_bits[1].strip() if len(search_bits) == 2 else ""

        # Exact key direct hit matching routing layout
        if user_key in shortcuts:
            sc = shortcuts[user_key]
            stype = sc.get("Type", "Unknown")
            
            if stype == "Url":
                url = sc.get("Path", "")
                # Dynamic argument replacement matching Flow Launcher ${q} or standard input templates
                if user_arg:
                    url = url.replace("${q}", user_arg).replace("%s", user_arg)
                else:
                    url = url.replace("${q}", "").replace("%s", "")
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                
                return RenderResultListAction([ExtensionResultItem(
                    icon=icon, name=f"🌐 Launching URL: {user_key}", description=f"Open browser: {url}",
                    on_enter=RunScriptAction(f"xdg-open '{url}'")
                )])
                
            elif stype in ["Directory", "File"]:
                path = os.path.expandvars(sc.get("Path", ""))
                return RenderResultListAction([ExtensionResultItem(
                    icon=icon, name=f"📁 Open {stype}: {user_key}", description=f"Path: {path}",
                    on_enter=RunScriptAction(f"xdg-open '{path}'")
                )])
                
            elif stype == "Shell":
                cmd = sc.get("Arguments", "")
                if user_arg:
                    cmd = cmd.replace("${q}", user_arg).replace("%s", user_arg)
                return RenderResultListAction([ExtensionResultItem(
                    icon=icon, name=f"⚡ Run Command: {user_key}", description=f"Executing: {cmd}",
                    on_enter=RunScriptAction(cmd)
                )])
                
            elif stype == "Group":
                gkeys = sc.get("Keys", [])
                chained = []
                for k in gkeys:
                    child = shortcuts.get(k)
                    if child:
                        ctype = child.get("Type")
                        if ctype in ["Directory", "File"]:
                            chained.append(f"xdg-open '{os.path.expandvars(child.get('Path'))}'")
                        elif ctype == "Url":
                            u = child.get("Path").replace("${q}", user_arg).replace("%s", user_arg)
                            if not u.startswith(("http://", "https://")): u = "https://" + u
                            chained.append(f"xdg-open '{u}'")
                        elif ctype == "Shell":
                            chained.append(child.get("Arguments").replace("${q}", user_arg).replace("%s", user_arg))
                
                exec_payload = " & ".join(chained) if chained else "true"
                return RenderResultListAction([ExtensionResultItem(
                    icon=icon, name=f"📦 Launch Multi-Group: {user_key}", description=f"Triggers {len(gkeys)} actions at once",
                    on_enter=RunScriptAction(exec_payload)
                )])

        # If no explicit match, build list view and fuzzy search filtering matches
        if raw_args:
            matched_keys = difflib.get_close_matches(raw_args, list(shortcuts.keys()), n=5, cutoff=0.2)
            if not matched_keys:
                matched_keys = [k for k in shortcuts.keys() if raw_args.lower() in k.lower()]
            targets = [shortcuts[k] for k in matched_keys if k in shortcuts]
        else:
            targets = list(shortcuts.values())

        for sc in targets:
            stype = sc.get("Type", "Unknown")
            skey = sc.get("Key", "Unknown")
            
            prefix = "📁" if stype in ["Directory", "File"] else "🌐" if stype == "Url" else "⚡" if stype == "Shell" else "📦"
            desc = sc.get("Path") if "Path" in sc else sc.get("Arguments") if "Arguments" in sc else f"Group matching keys: {', '.join(sc.get('Keys', []))}"

            items.append(ExtensionResultItem(
                icon=icon,
                name=f"{prefix} [{skey}] {stype} Shortcut",
                description=f"Payload config: {desc}",
                on_enter=SetUserQueryAction(f"{keyword} {skey} ")
            ))

        if not items:
            items.append(ExtensionResultItem(
                icon=icon,
                name="Shortcuts Hub Command Terminal",
                description="Usage: add [type] [key] [payload] (Types: directory, file, url, shell)",
                on_enter=DoNothingAction()
            ))

        return RenderResultListAction(items)

if __name__ == '__main__':
    ShortcutsPlugin().run()
