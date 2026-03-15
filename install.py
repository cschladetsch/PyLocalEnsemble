#!/usr/bin/env python3
"""
Alice installer — run once to set up everything needed.
  python install.py
"""
import os, json, shutil

from installer.helpers      import CONFIG_FILE, CONF_DIR, ok, info
from installer.packages     import check_python, install_packages
from installer.llama        import install_llama_server
from installer.model        import setup_model
from installer.tts_install  import install_tts_models
from installer.forge_install import install_forge

# Re-export for tests: from install import _pick_llama_asset
from installer.llama import _pick_llama_asset  # noqa: F401

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _bootstrap_configs():
    """Copy example configs to their live locations on first run."""
    if not os.path.exists(CONFIG_FILE):
        example = os.path.join(CONF_DIR, "alice.example.json")
        if os.path.exists(example):
            shutil.copy(example, CONFIG_FILE)
            info("created alice.json from conf/alice.example.json")

    personas_file = os.path.join(_SCRIPT_DIR, "personas.json")
    if not os.path.exists(personas_file):
        personas_example = os.path.join(CONF_DIR, "personas.example.json")
        if os.path.exists(personas_example):
            shutil.copy(personas_example, personas_file)
            info("created personas.json from conf/personas.example.json")


def main():
    print("=" * 50)
    print("  Alice - Installer")
    print("=" * 50)

    check_python()
    install_packages()
    _bootstrap_configs()

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    install_llama_server(cfg)
    setup_model(cfg)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    ok("alice.json saved")

    install_tts_models()
    install_forge(cfg)

    print("\n" + "=" * 50)
    print("  Installation complete!")
    print("=" * 50)
    print()
    print("  Start Alice:")
    print("    python alice.py")
    print()
    print("  Then open:  http://localhost:8000")
    print()


if __name__ == "__main__":
    main()
