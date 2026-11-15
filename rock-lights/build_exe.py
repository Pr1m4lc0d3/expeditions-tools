"""Build "Rock Lights Tool.exe": the tool as one Windows program, so nobody needs Python.

    python build_exe.py            -> out/Rock Lights Tool.exe

Needs PyInstaller (pip install pyinstaller). The exe still needs Blender and the Expeditions
editor on the PC that runs it (they do the modelling and the conversion), but nothing else.
It bundles only the four shared modules the tool uses, not the whole saber package.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SABER = HERE.parent / "fob-modules" / "saber"
STAGE = HERE / "out" / "stage"
NAME = "Rock Lights Tool"


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "saber").mkdir(parents=True)
    for f in ("add_rock_lights.py", "pods.py", "pods_blender.py"):
        shutil.copy2(HERE / f, STAGE / f)
    for f in ("convert.py", "gamepaths.py", "tga.py", "stock_mesh.py"):
        shutil.copy2(SABER / f, STAGE / "saber" / f)
    (STAGE / "saber" / "__init__.py").write_text('"""the shared saber modules the rock-lights tool uses"""\n',
                                                 encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
                        "--name", NAME, "--icon", str(HERE / "rock_lights.ico"),
                        "--distpath", str(HERE / "out"), "--workpath", str(STAGE / "work"),
                        "--specpath", str(STAGE), "--paths", str(STAGE),
                        "--add-data", f"{STAGE / 'pods_blender.py'};.",
                        "--hidden-import", "pods", "--hidden-import", "saber.convert",
                        "--hidden-import", "saber.gamepaths", "--hidden-import", "saber.tga",
                        "--hidden-import", "saber.stock_mesh",
                        *[a for m in ("scipy", "matplotlib", "pandas", "torch", "cv2", "pytest")
                          for a in ("--exclude-module", m)],
                        str(STAGE / "add_rock_lights.py")], capture_output=True, text=True)
    exe = HERE / "out" / f"{NAME}.exe"
    if r.returncode or not exe.is_file():
        print(r.stdout[-2000:], r.stderr[-3000:])
        return 1
    print(f"built {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
