from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / ".cache"


def local_runtime_env() -> dict[str, str]:
    """Keep local caches, downloads, models and generated artifacts on F:."""
    paths = {
        "PIP_CACHE_DIR": CACHE_ROOT / "pip",
        "TEMP": CACHE_ROOT / "tmp",
        "TMP": CACHE_ROOT / "tmp",
        "TORCH_HOME": CACHE_ROOT / "torch",
        "XDG_CACHE_HOME": CACHE_ROOT,
        "NPM_CONFIG_CACHE": CACHE_ROOT / "npm",
        "TRYON_RESULT_DIR": CACHE_ROOT / "tryon-results",
    }
    for path in paths.values():
        Path(path).mkdir(parents=True, exist_ok=True)
    return {key: str(value) for key, value in paths.items()}


def command_name(name: str) -> str:
    return f"{name}.cmd" if os.name == "nt" else name


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.4)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def endpoint_matches(url: str, marker: str) -> bool:
    try:
        with urlopen(url, timeout=1.2) as response:
            return marker in response.read().decode("utf-8", errors="ignore")
    except (OSError, URLError):
        return False


def reusable_service(name: str, port: int, health_url: str, marker: str) -> bool:
    if not port_in_use(port):
        return False
    if endpoint_matches(health_url, marker):
        print(f"Reusing existing {name} service on port {port}.")
        return True
    raise RuntimeError(
        f"Port {port} is already occupied by another application. "
        f"Stop that application or free the port before running npm run dev:local."
    )


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def has_tryon_runtime(python: Path) -> bool:
    if not python.exists():
        return False
    check = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn"],
        cwd=ROOT,
        env={**os.environ, **local_runtime_env()},
        capture_output=True,
        check=False,
    )
    return check.returncode == 0


def find_tryon_python() -> Path | None:
    configured = os.getenv("TRYON_PYTHON")
    candidates = [
        Path(configured) if configured else None,
        Path(r"F:\Anaconda\envs\pytorch\python.exe"),
        ROOT / ".venv-tryon" / "Scripts" / "python.exe",
    ]
    return next((path for path in candidates if path and has_tryon_runtime(path)), None)


def main() -> int:
    geometry_env = os.environ.copy()
    geometry_env.update(local_runtime_env())
    local_config = ROOT / "config" / "model.local.json"
    if local_config.exists():
        model = json.loads(local_config.read_text(encoding="utf-8")).get("model", {})
        geometry_env.update({
            "MODEL_ENABLED": "true" if model.get("enabled") else "false",
            "MODEL_PROVIDER": str(model.get("provider") or ""),
            "MODEL_BASE_URL": str(model.get("baseUrl") or ""),
            "MODEL_NAME": str(model.get("name") or ""),
            "MODEL_API_KEY": str(model.get("apiKey") or ""),
        })
    geometry_reused = reusable_service(
        "geometry",
        8788,
        "http://127.0.0.1:8788/health",
        '"service_build":"prototype-parametric-v2"',
    )
    geometry = None if geometry_reused else subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(ROOT / "apps" / "geometry-service"),
            "--host",
            "127.0.0.1",
            "--port",
            "8788",
        ],
        cwd=ROOT,
        env=geometry_env,
    )
    tryon = None
    tryon_reused = reusable_service("3D try-on", 8790, "http://127.0.0.1:8790/research/health", '"service_build":"anthropometric-fit-v2"')
    tryon_python = None if tryon_reused else find_tryon_python()
    if tryon_python:
        tryon_env = geometry_env.copy()
        tryon_env.update({
            "ENABLE_RESEARCH_3D": os.getenv("ENABLE_RESEARCH_3D", "true"),
            "TRYON_DEVICE": os.getenv("TRYON_DEVICE", "cuda:0"),
            "SMPL_MODEL_DIR": os.getenv(
                "SMPL_MODEL_DIR",
                str(ROOT / "tmp" / "SMPL_python_v.1.1.0" / "smpl" / "models"),
            ),
        })
        tryon = subprocess.Popen(
            [
                str(tryon_python),
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                str(ROOT / "apps" / "tryon-service"),
                "--host",
                "127.0.0.1",
                "--port",
                "8790",
            ],
            cwd=ROOT,
            env=tryon_env,
        )
    web_reused = reusable_service("web", 5173, "http://127.0.0.1:5173/index.html", '<div id="root"></div>')
    web = None if web_reused else subprocess.Popen(
        [command_name("npm"), "--prefix", "apps/web", "run", "dev"],
        cwd=ROOT,
        env=geometry_env,
    )
    print("\nLocal workbench is starting:")
    print("  Web:      see the Local URL printed by Vite")
    print("  Geometry: http://127.0.0.1:8788")
    print(f"  3D try-on: {'http://127.0.0.1:8790' if tryon or tryon_reused else 'runtime unavailable'}")
    if tryon_python:
        print(f"  3D Python: {tryon_python}")
    print(f"  Cache:    {CACHE_ROOT}")
    print("Press Ctrl+C once to stop all services.\n")
    try:
        while (geometry is None or geometry.poll() is None) and (web is None or web.poll() is None) and (tryon is None or tryon.poll() is None):
            time.sleep(0.25)
        if geometry is not None and geometry.poll() is not None:
            return geometry.returncode or 0
        if web is not None and web.poll() is not None:
            return web.returncode or 0
        return tryon.returncode if tryon and tryon.poll() is not None else 0
    except KeyboardInterrupt:
        return 0
    finally:
        if web:
            stop(web)
        if geometry:
            stop(geometry)
        if tryon:
            stop(tryon)


if __name__ == "__main__":
    raise SystemExit(main())
