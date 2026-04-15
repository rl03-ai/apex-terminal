#!/usr/bin/env python3
"""
apex_launch.py — Apex Terminal unified launcher.

Usage:
    python apex_launch.py              # start everything (demo mode)
    python apex_launch.py --real       # start with yfinance real data
    python apex_launch.py --setup      # first-time setup only, then exit
    python apex_launch.py --backend    # backend only
    python apex_launch.py --frontend   # frontend only
    python apex_launch.py --ingest     # trigger bulk ingest after start
    python apex_launch.py --status     # check if services are running
    python apex_launch.py --stop       # stop all running services

What this script does:
  1. Checks prerequisites (Python 3.10+, Node 18+, pip)
  2. Creates / activates backend virtualenv
  3. Installs backend dependencies (pip install -r requirements.txt)
  4. Creates .env if missing, with sensible defaults
  5. Creates the SQLite database and runs seed data (demo mode)
  6. Starts uvicorn (backend) in a subprocess
  7. Installs frontend npm dependencies
  8. Starts Vite dev server (frontend) in a subprocess
  9. Prints URLs and waits; Ctrl+C shuts everything down cleanly

Ports (configurable via env or flags):
  Backend  : http://localhost:8000   (API + docs at /docs)
  Frontend : http://localhost:5173   (Vite dev) or 4173 (preview)
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.resolve()
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
VENV_DIR = BACKEND_DIR / ".venv"

IS_WINDOWS = platform.system() == "Windows"
PYTHON_BIN = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
PIP_BIN    = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("pip.exe" if IS_WINDOWS else "pip")

# ─────────────────────────────────────────────────────────────────────────────
# Colours (disabled on Windows unless ANSI is supported)
# ─────────────────────────────────────────────────────────────────────────────

_USE_COLOR = not IS_WINDOWS or os.environ.get("TERM") == "xterm"

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def ok(msg: str)    -> None: print(_c("92", f"  ✓  {msg}"))
def info(msg: str)  -> None: print(_c("94", f"  →  {msg}"))
def warn(msg: str)  -> None: print(_c("93", f"  ⚠  {msg}"))
def err(msg: str)   -> None: print(_c("91", f"  ✗  {msg}"), file=sys.stderr)
def head(msg: str)  -> None: print(_c("1;96", f"\n{'─'*60}\n  {msg}\n{'─'*60}"))

# ─────────────────────────────────────────────────────────────────────────────
# Prerequisite checks
# ─────────────────────────────────────────────────────────────────────────────

def check_python() -> None:
    v = sys.version_info
    if v < (3, 10):
        err(f"Python 3.10+ required, found {v.major}.{v.minor}")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


def check_node() -> bool:
    node = shutil.which("node")
    if not node:
        warn("Node.js not found — frontend will not start. Install from https://nodejs.org/")
        return False
    result = subprocess.run([node, "--version"], capture_output=True, text=True)
    version_str = result.stdout.strip().lstrip("v")
    try:
        major = int(version_str.split(".")[0])
        if major < 18:
            warn(f"Node 18+ recommended, found v{version_str}")
            return True
        ok(f"Node.js v{version_str}")
        return True
    except ValueError:
        warn(f"Could not parse Node version: {version_str}")
        return True


def check_npm() -> bool:
    npm = shutil.which("npm")
    if not npm:
        warn("npm not found")
        return False
    ok("npm found")
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Virtualenv + dependencies
# ─────────────────────────────────────────────────────────────────────────────

def ensure_venv() -> None:
    if PYTHON_BIN.exists():
        ok(f"Virtualenv exists at {VENV_DIR.relative_to(ROOT)}")
        return
    info("Creating virtualenv…")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    ok("Virtualenv created")


def install_backend_deps() -> None:
    req = BACKEND_DIR / "requirements.txt"
    if not req.exists():
        err(f"requirements.txt not found at {req}")
        sys.exit(1)

    info("Installing backend dependencies (may take a minute on first run)…")
    result = subprocess.run(
        [str(PIP_BIN), "install", "-r", str(req), "--quiet"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        err("pip install failed:\n" + result.stderr[-2000:])
        sys.exit(1)
    ok("Backend dependencies installed")


def install_frontend_deps() -> None:
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists():
        ok("Frontend node_modules exists")
        return
    info("Installing frontend dependencies (npm install)…")
    result = subprocess.run(
        ["npm", "install", "--silent"],
        cwd=str(FRONTEND_DIR),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        err("npm install failed:\n" + result.stderr[-2000:])
        return  # Non-fatal — frontend is optional
    ok("Frontend dependencies installed")

# ─────────────────────────────────────────────────────────────────────────────
# .env setup
# ─────────────────────────────────────────────────────────────────────────────

def ensure_env(real_data: bool = False) -> None:
    env_file = BACKEND_DIR / ".env"
    example  = BACKEND_DIR / ".env.example"

    if env_file.exists():
        ok(".env exists")
        return

    if example.exists():
        content = example.read_text()
    else:
        content = (
            "APP_NAME=Apex Signal API\n"
            "DEBUG=true\n"
            "SECRET_KEY=apex-local-dev-secret-change-in-prod\n"
            "DATABASE_URL=sqlite:///./apex.db\n"
            "ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173\n"
            "DEFAULT_EXCHANGE=NASDAQ\n"
        )

    # Set provider
    provider = "yfinance" if real_data else "demo"
    if "DATA_PROVIDER=" in content:
        lines = [
            f"DATA_PROVIDER={provider}" if l.startswith("DATA_PROVIDER=") else l
            for l in content.splitlines()
        ]
        content = "\n".join(lines) + "\n"
    else:
        content += f"\nDATA_PROVIDER={provider}\n"

    env_file.write_text(content)
    ok(f".env created (DATA_PROVIDER={provider})")


def ensure_frontend_env() -> None:
    env_file = FRONTEND_DIR / ".env"
    if env_file.exists():
        return
    env_file.write_text("VITE_API_BASE_URL=http://localhost:8000\n")
    ok("Frontend .env created")

# ─────────────────────────────────────────────────────────────────────────────
# Database seed
# ─────────────────────────────────────────────────────────────────────────────

def run_seed(real_data: bool = False) -> None:
    db_file = BACKEND_DIR / "apex.db"

    if db_file.exists():
        ok("Database exists — skipping seed")
        return

    seed_script = BACKEND_DIR / "seed_demo.py"
    if not seed_script.exists():
        warn("seed_demo.py not found — skipping seed")
        return

    if real_data:
        info("Real data mode — skipping demo seed (use POST /universe/ingest after start)")
        # Still need to create tables
        info("Creating database tables…")
        result = subprocess.run(
            [str(PYTHON_BIN), "-c",
             "import sys; sys.path.insert(0,'app'); "
             "from app.core.database import Base, engine; "
             "from app.models import asset, portfolio, user; "
             "Base.metadata.create_all(bind=engine); print('Tables created')"],
            cwd=str(BACKEND_DIR),
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            ok("Database tables created")
        else:
            warn(f"Table creation warning: {result.stderr[-500:]}")
        return

    info("Running demo seed…")
    result = subprocess.run(
        [str(PYTHON_BIN), "seed_demo.py"],
        cwd=str(BACKEND_DIR),
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ok("Demo data seeded")
    else:
        warn(f"Seed warning (may be harmless): {result.stderr[-500:]}")

# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_backend(host: str = "localhost", port: int = 8000, timeout: int = 30) -> bool:
    info(f"Waiting for backend on {host}:{port}…")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/")
            resp = conn.getresponse()
            if resp.status < 500:
                ok(f"Backend ready (HTTP {resp.status})")
                return True
        except (ConnectionRefusedError, OSError):
            pass
        time.sleep(0.8)
    warn(f"Backend did not respond within {timeout}s")
    return False


def check_status(backend_port: int = 8000, frontend_port: int = 5173) -> None:
    head("Service Status")
    for name, host, port, path in [
        ("Backend API", "localhost", backend_port, "/"),
        ("API Docs",    "localhost", backend_port, "/docs"),
        ("Frontend",   "localhost", frontend_port, "/"),
    ]:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", path)
            resp = conn.getresponse()
            ok(f"{name:20s}  http://{host}:{port}{path}  (HTTP {resp.status})")
        except Exception:
            err(f"{name:20s}  http://{host}:{port}{path}  NOT REACHABLE")

# ─────────────────────────────────────────────────────────────────────────────
# Trigger ingest
# ─────────────────────────────────────────────────────────────────────────────

def trigger_ingest(port: int = 8000, workers: int = 8) -> None:
    head("Triggering Universe Ingest")
    info(f"POST http://localhost:{port}/universe/ingest?workers={workers}")
    try:
        conn = http.client.HTTPConnection("localhost", port, timeout=10)
        conn.request(
            "POST",
            f"/universe/ingest?workers={workers}",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = resp.read().decode()
        data = json.loads(body)
        ok(f"Ingest started: {data.get('message', body[:120])}")
        info("Watch backend logs for progress. Check GET /universe/scheduler for job status.")
    except Exception as exc:
        warn(f"Could not trigger ingest: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# Process management
# ─────────────────────────────────────────────────────────────────────────────

_processes: list[subprocess.Popen] = []


def _stop_all() -> None:
    if not _processes:
        return
    info("Stopping all services…")
    for proc in reversed(_processes):
        if proc.poll() is None:
            try:
                if IS_WINDOWS:
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    _processes.clear()
    ok("All services stopped")


def _signal_handler(sig: int, frame) -> None:
    print()
    _stop_all()
    sys.exit(0)


def start_backend(port: int = 8000, reload: bool = True) -> subprocess.Popen:
    cmd = [
        str(PYTHON_BIN), "-m", "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--log-level", "info",
    ]
    if reload:
        cmd.append("--reload")

    info(f"Starting backend on port {port}…")
    proc = subprocess.Popen(
        cmd,
        cwd=str(BACKEND_DIR),
        # Don't capture — let output flow to terminal
    )
    _processes.append(proc)
    return proc


def start_frontend(port: int = 5173) -> subprocess.Popen | None:
    npm = shutil.which("npm")
    if not npm:
        warn("npm not found — skipping frontend")
        return None

    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        warn("Frontend node_modules missing — run setup first")
        return None

    info(f"Starting frontend on port {port}…")
    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(port), "--host"],
        cwd=str(FRONTEND_DIR),
    )
    _processes.append(proc)
    return proc


def stop_services() -> None:
    """Find and kill any processes using the default ports."""
    head("Stopping Apex Services")
    for port in [8000, 5173]:
        if IS_WINDOWS:
            result = subprocess.run(
                f"netstat -ano | findstr :{port}",
                shell=True, capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[-1].isdigit():
                    pid = parts[-1]
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                    ok(f"Killed process {pid} on port {port}")
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True,
            )
            for pid_str in result.stdout.strip().splitlines():
                if pid_str.isdigit():
                    subprocess.run(["kill", "-TERM", pid_str], capture_output=True)
                    ok(f"Sent SIGTERM to PID {pid_str} on port {port}")

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Apex Terminal — unified launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--real",      action="store_true", help="Use yfinance real data (default: demo)")
    p.add_argument("--setup",     action="store_true", help="Run setup only, don't start services")
    p.add_argument("--backend",   action="store_true", help="Start backend only")
    p.add_argument("--frontend",  action="store_true", help="Start frontend only")
    p.add_argument("--ingest",    action="store_true", help="Trigger bulk ingest after backend starts")
    p.add_argument("--status",    action="store_true", help="Check if services are running")
    p.add_argument("--stop",      action="store_true", help="Stop all running services")
    p.add_argument("--no-reload", action="store_true", help="Disable uvicorn --reload")
    p.add_argument("--backend-port",  type=int, default=8000)
    p.add_argument("--frontend-port", type=int, default=5173)
    p.add_argument("--workers",   type=int, default=8, help="Ingest workers (--ingest flag)")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── Quick actions (no setup needed) ──────────────────────────────────────
    if args.status:
        check_status(args.backend_port, args.frontend_port)
        return

    if args.stop:
        stop_services()
        return

    # ── Banner ────────────────────────────────────────────────────────────────
    print(_c("1;96", r"""
   █████╗ ██████╗ ███████╗██╗  ██╗
  ██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝
  ███████║██████╔╝█████╗   ╚███╔╝
  ██╔══██║██╔═══╝ ██╔══╝   ██╔██╗
  ██║  ██║██║     ███████╗██╔╝ ██╗
  ╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝
  Terminal  —  Unified Launcher
    """))

    mode = "REAL DATA (yfinance)" if args.real else "DEMO DATA"
    print(f"  Mode: {_c('93', mode)}\n")

    # ── Prerequisites ─────────────────────────────────────────────────────────
    head("Checking prerequisites")
    check_python()
    has_node = check_node()
    if has_node:
        check_npm()

    # ── Setup ─────────────────────────────────────────────────────────────────
    head("Setup")
    ensure_venv()
    install_backend_deps()
    ensure_env(real_data=args.real)
    run_seed(real_data=args.real)

    if has_node and not args.backend:
        ensure_frontend_env()
        install_frontend_deps()

    if args.setup:
        head("Setup complete")
        ok("Run  python apex_launch.py  to start services")
        return

    # ── Register signal handlers ──────────────────────────────────────────────
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ── Start services ────────────────────────────────────────────────────────
    start_be = not args.frontend
    start_fe = not args.backend and has_node

    if start_be:
        head("Starting Backend")
        start_backend(port=args.backend_port, reload=not args.no_reload)
        wait_for_backend(port=args.backend_port, timeout=40)

    if start_fe:
        head("Starting Frontend")
        start_frontend(port=args.frontend_port)

    # ── Post-start actions ────────────────────────────────────────────────────
    if args.ingest and start_be:
        time.sleep(2)
        trigger_ingest(port=args.backend_port, workers=args.workers)

    # ── Print URLs ────────────────────────────────────────────────────────────
    head("Apex Terminal is running")
    print()
    if start_be:
        print(f"  {_c('92', '●')}  Backend API   {_c('1', f'http://localhost:{args.backend_port}')}")
        print(f"  {_c('92', '●')}  API Docs      {_c('1', f'http://localhost:{args.backend_port}/docs')}")
    if start_fe:
        print(f"  {_c('92', '●')}  Frontend      {_c('1', f'http://localhost:{args.frontend_port}')}")
    print()

    if args.real:
        print(_c("93", "  Real data mode: after startup run the first ingest:"))
        print(_c("93", f"    curl -X POST 'http://localhost:{args.backend_port}/universe/ingest?workers=8'"))
        print(_c("93", "  Or restart with:  python apex_launch.py --real --ingest"))
        print()

    print(_c("90", "  Press Ctrl+C to stop all services"))
    print()

    # ── Wait loop ─────────────────────────────────────────────────────────────
    try:
        while True:
            # Check if any process died unexpectedly
            for i, proc in enumerate(_processes):
                if proc.poll() is not None:
                    name = "Backend" if i == 0 else "Frontend"
                    code = proc.returncode
                    if code != 0:
                        warn(f"{name} exited with code {code}")
                        # Attempt restart once
                        info(f"Restarting {name}…")
                        if name == "Backend":
                            _processes[i] = start_backend(
                                port=args.backend_port,
                                reload=not args.no_reload,
                            )
                        else:
                            new_proc = start_frontend(port=args.frontend_port)
                            if new_proc:
                                _processes[i] = new_proc
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_all()


if __name__ == "__main__":
    main()
