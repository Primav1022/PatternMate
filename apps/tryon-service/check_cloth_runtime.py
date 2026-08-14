from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("WARP_CACHE_PATH", str(Path(__file__).resolve().parents[2] / ".cache" / "warp"))

import warp as wp
import newton
from newton.solvers import SolverStyle3D


def main() -> None:
    wp.init()
    device = wp.get_preferred_device()
    print(json.dumps({
        "newton_version": newton.__version__,
        "warp_version": wp.__version__,
        "device": str(device),
        "cuda": bool(device.is_cuda),
        "style3d_solver": SolverStyle3D.__name__,
    }, indent=2))


if __name__ == "__main__":
    main()
