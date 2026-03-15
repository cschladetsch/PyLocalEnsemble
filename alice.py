"""Root launcher — delegates to server/alice.py."""
import runpy, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))
runpy.run_path(os.path.join(os.path.dirname(__file__), "server", "alice.py"), run_name="__main__")
