"""The `saber` package lives in ../fob-modules/saber, shared by every folder in this repo.

This shim makes `from saber import X` work from engine-ghost without a second copy, and puts
fob-modules on the import path so the shared model helpers (glb_to_fbx, verify_model, ...)
import too. Keep engine-ghost next to fob-modules, as it is in the repository.
"""
import os
import sys

_FOB = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'fob-modules'))
if _FOB not in sys.path:
    sys.path.append(_FOB)
__path__ = [os.path.join(_FOB, 'saber')]
_init = os.path.join(__path__[0], '__init__.py')
with open(_init, encoding='utf-8') as _f:
    exec(compile(_f.read(), _init, 'exec'))
