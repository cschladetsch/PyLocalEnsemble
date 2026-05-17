"""Step 7: Self-hosted web fonts (latin subset, woff2) for the frontend.

Eliminates the visible font swap that happens when Google Fonts loads asynchronously
mid-conversation. The .woff2 files are excluded from git (see .gitignore) — the
installer downloads them into server/static/fonts/ on first run.
"""
import os
from installer.helpers import SCRIPT_DIR, heading, ok, info, _download

FONTS_DIR = os.path.join(SCRIPT_DIR, "static", "fonts")

# Latin-subset woff2 files served by fonts.gstatic.com.
# URLs were extracted from the css2 endpoint at fonts.googleapis.com
# (latin unicode-range U+0000-00FF). Cyrillic / vietnamese / latin-ext
# subsets are intentionally omitted — Alice's chat output is English only.
_FONTS = {
    "almendra-400.woff2":
        "https://fonts.gstatic.com/s/almendra/v28/H4ckBXKAlMnTn0CskxY9yL4.woff2",
    "almendra-italic-400.woff2":
        "https://fonts.gstatic.com/s/almendra/v28/H4ciBXKAlMnTn0CskxY4-LyYhw.woff2",
    "cinzel-decorative-400.woff2":
        "https://fonts.gstatic.com/s/cinzeldecorative/v19/daaCSScvJGqLYhG8nNt8KPPswUAPni7TTMw.woff2",
    "cormorant-garamond-300.woff2":
        "https://fonts.gstatic.com/s/cormorantgaramond/v21/co3umX5slCNuHLi8bLeY9MK7whWMhyjypVO7abI26QOD_qE6KnTOig.woff2",
    "cormorant-garamond-italic-300.woff2":
        "https://fonts.gstatic.com/s/cormorantgaramond/v21/co3smX5slCNuHLi8bLeY9MK7whWMhyjYrGFEsdtdc62E6zd5rDD-iNM8.woff2",
    "montserrat-300-400.woff2":
        "https://fonts.gstatic.com/s/montserrat/v31/JTUSjIg1_i6t8kCHKm459Wlhyw.woff2",
    "pinyon-script-400.woff2":
        "https://fonts.gstatic.com/s/pinyonscript/v24/6xKpdSJbL9-e9LuoeQiDRQR8WOXaOg.woff2",
    "share-tech-mono-400.woff2":
        "https://fonts.gstatic.com/s/sharetechmono/v16/J7aHnp1uDWRBEqV98dVQztYldFcLowEF.woff2",
}


def install_fonts():
    heading("7/7", "Web fonts (self-hosted)")
    os.makedirs(FONTS_DIR, exist_ok=True)

    missing = [(name, url) for name, url in _FONTS.items()
               if not os.path.exists(os.path.join(FONTS_DIR, name))]

    if not missing:
        ok(f"all {len(_FONTS)} font files already present")
        return

    info(f"downloading {len(missing)} font file(s) ...")
    for name, url in missing:
        _download(url, os.path.join(FONTS_DIR, name), name)
    ok(f"installed {len(missing)} font file(s) to static/fonts/")
