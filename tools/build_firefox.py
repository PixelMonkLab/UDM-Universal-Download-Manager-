"""Rebuild extension-firefox/ from extension-chrome/ (run after changing the add-on).

    python tools/build_firefox.py
"""
import json
import pathlib
import shutil

root = pathlib.Path(__file__).resolve().parent.parent
src, ff = root / "extension-chrome", root / "extension-firefox"
shutil.rmtree(ff, ignore_errors=True)
shutil.copytree(src, ff)
m = json.loads((ff / "manifest.json").read_text(encoding="utf-8"))
m["background"] = {"scripts": ["common.js", "background.js"]}
m.pop("version_name", None)                      # Firefox doesn't support it
m.pop("side_panel", None)                        # Firefox uses sidebar_action instead
m["permissions"] = [p for p in m["permissions"] if p != "sidePanel"]
m["sidebar_action"] = {"default_panel": "popup.html?panel=1", "default_title": "UDM",
                       "default_icon": {"16": "icons/16.png", "32": "icons/32.png"}, "open_at_install": False}
m["browser_specific_settings"] = {"gecko": {"id": "udm@local.app", "strict_min_version": "115.0",
                                            "data_collection_permissions": {"required": ["none"]}}}
(ff / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
print("extension-firefox rebuilt")
