"""Rebuild the dependency-free host-workspace assets without the core build list."""
from pathlib import Path
from shutil import copyfile

UI = Path(__file__).resolve().parent
(UI / "dist").mkdir(exist_ok=True)
index = (UI / "index.html").read_text(encoding="utf-8")
admin = (UI / "admin" / "index.html").read_text(encoding="utf-8")
(UI / "dist" / "index.html").write_text(index.replace("<!-- bridge-admin -->", admin), encoding="utf-8", newline="\n")
for source, name in (("src/main.js", "main.js"), ("src/style.css", "style.css"),
                     ("admin/admin.js", "admin.js"), ("admin/style.css", "admin.css")):
    copyfile(UI / source, UI / "dist" / name)
