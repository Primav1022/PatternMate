from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


root = Path(os.getenv("COMFY_ROOT", "/root/autodl-tmp/ComfyUI"))
attention = root / "comfy" / "ldm" / "modules" / "attention.py"
source = attention.read_text(encoding="utf-8")
old = "COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = comfy_kitchen.int8_attention_is_available()"
new = "COMFY_KITCHEN_INT8_ATTENTION_IS_AVAILABLE = hasattr(comfy_kitchen, 'int8_attention_is_available') and comfy_kitchen.int8_attention_is_available()"
if old in source:
    attention.write_text(source.replace(old, new), encoding="utf-8")
os.chdir(root)
sys.path.insert(0, str(root))
sys.argv[0] = str(root / "main.py")
runpy.run_path(sys.argv[0], run_name="__main__")
