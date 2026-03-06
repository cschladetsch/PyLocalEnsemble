"""
alice_install.py - called by install.bat with a command argument.
Usage: python alice_install.py <command> [args...]
Commands:
  write_modelfile  <dest_path>
  patch_forge      <webui_bat_path>
  patch_alice      <alice_py_path> <forge_dir>
"""
import sys
import re
import os

def write_modelfile(dest):
    content = (
        "FROM mistral-nemo\n\n"
        "SYSTEM \"\"\"\n"
        "You are Alice. You are enigmatic, intelligent, very flirtatious and sexy.\n"
        "You are 5'8\" tall. You have very long blonde hair and blue eyes. You have DD breasts.\n"
        "You speak in measured, literary prose. You never break character.\n\n"
        "I am Christian. I am 33 years old. I am 6'2\", blonde with blue eyes.\n"
        "I am a game developer. I have a great physique.\n"
        "\"\"\"\n"
    )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w", encoding="utf-8").write(content)
    print("  Modelfile written to", dest)

def patch_forge(bat_path):
    try:
        src = open(bat_path, encoding="utf-8").read()
        def patch(m):
            args = m.group(1)
            for flag in ("--api", "--cuda-malloc"):
                if flag not in args:
                    args = args.strip() + " " + flag
            return "set COMMANDLINE_ARGS=" + args
        if "COMMANDLINE_ARGS" in src:
            src = re.sub(r"set COMMANDLINE_ARGS=([^\r\n]*)", lambda m: patch(m), src)
        else:
            src += "\nset COMMANDLINE_ARGS=--api --cuda-malloc\n"
        open(bat_path, "w", encoding="utf-8").write(src)
        print("  webui.bat patched.")
    except Exception as e:
        print("  WARNING: Could not patch webui.bat:", e)
        print("  Add manually: set COMMANDLINE_ARGS=--api --cuda-malloc")

def patch_alice(alice_path, forge_dir):
    try:
        src = open(alice_path, encoding="utf-8").read()
        new_val = 'FORGE_BAT    = r"' + forge_dir + '\\webui.bat"'
        src = re.sub(r'FORGE_BAT\s*=\s*r"[^"]*"', lambda m: new_val, src)
        open(alice_path, "w", encoding="utf-8").write(src)
        print("  FORGE_BAT updated in alice.py.")
    except Exception as e:
        print("  WARNING: Could not patch alice.py:", e)
        print("  Set FORGE_BAT manually to:", forge_dir + "\\webui.bat")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "write_modelfile":
        write_modelfile(sys.argv[2])
    elif cmd == "patch_forge":
        patch_forge(sys.argv[2])
    elif cmd == "patch_alice":
        patch_alice(sys.argv[2], sys.argv[3])
    else:
        print("Unknown command:", cmd)
        sys.exit(1)
