#!/usr/bin/env python3
"""
apex_deploy.py — Deploy Apex Terminal to the cloud for Android PWA access.

Architecture:
  Backend  → Render.com   (FastAPI + PostgreSQL, free tier)
  Frontend → Netlify.com  (React build, free tier, HTTPS automatic)

After deploy:
  1. Open Chrome on Android
  2. Go to your Netlify URL
  3. Chrome shows "Add to Home Screen" banner → tap Install
  4. Apex Terminal appears as an app icon on your home screen

Usage:
    python apex_deploy.py --check         # check prerequisites
    python apex_deploy.py --frontend      # deploy frontend to Netlify only
    python apex_deploy.py --guide         # show step-by-step manual guide
    python apex_deploy.py                 # full deployment guide
"""

from __future__ import annotations

import os
import subprocess
import sys
import shutil
import json
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"

_USE_COLOR = True

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def ok(msg: str)   -> None: print(_c("92", f"  ✓  {msg}"))
def info(msg: str) -> None: print(_c("94", f"  →  {msg}"))
def warn(msg: str) -> None: print(_c("93", f"  ⚠  {msg}"))
def head(msg: str) -> None: print(_c("1;96", f"\n{'─'*60}\n  {msg}\n{'─'*60}"))
def step(n: int, msg: str) -> None: print(_c("1;37", f"\n  [{n}] {msg}"))


# ─────────────────────────────────────────────────────────────────────────────
# Prerequisite checks
# ─────────────────────────────────────────────────────────────────────────────

def check_prerequisites() -> dict[str, bool]:
    head("Prerequisite Check")
    results = {}

    # Git
    git = shutil.which("git")
    results["git"] = bool(git)
    ok("git found") if git else warn("git not found — needed for Render deploy")

    # Node / npm
    node = shutil.which("node")
    npm  = shutil.which("npm")
    results["node"] = bool(node)
    results["npm"]  = bool(npm)
    if node:
        v = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
        ok(f"Node.js {v}")
    else:
        warn("Node.js not found — install from https://nodejs.org/")

    # Netlify CLI
    netlify = shutil.which("netlify")
    results["netlify"] = bool(netlify)
    if netlify:
        ok("Netlify CLI found")
    else:
        warn("Netlify CLI not installed (optional — can deploy via drag-and-drop)")
        info("To install: npm install -g netlify-cli")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Frontend build
# ─────────────────────────────────────────────────────────────────────────────

def build_frontend(backend_url: str | None = None) -> bool:
    head("Building Frontend")

    # Set API URL for production
    env_file = FRONTEND / ".env.production"
    if backend_url:
        env_file.write_text(f"VITE_API_BASE_URL={backend_url}\n")
        ok(f"API URL set to: {backend_url}")
    else:
        if not env_file.exists():
            env_file.write_text("VITE_API_BASE_URL=https://your-backend.onrender.com\n")
            warn("No backend URL set — edit frontend/.env.production before building")

    # npm install
    if not (FRONTEND / "node_modules").exists():
        info("Installing npm dependencies...")
        result = subprocess.run(["npm", "install"], cwd=FRONTEND, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr[-1000:])
            return False
        ok("npm dependencies installed")

    # npm run build
    info("Building React app (npm run build)...")
    result = subprocess.run(["npm", "run", "build"], cwd=FRONTEND, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-1000:])
        return False

    dist = FRONTEND / "dist"
    if dist.exists():
        size = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file())
        ok(f"Build complete → {dist} ({size//1024}KB)")
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Netlify deploy
# ─────────────────────────────────────────────────────────────────────────────

def deploy_to_netlify() -> str | None:
    head("Deploying to Netlify")
    netlify = shutil.which("netlify")

    if not netlify:
        warn("Netlify CLI not found. Manual deploy:")
        info("1. Go to https://app.netlify.com/drop")
        info("2. Drag the 'frontend/dist' folder onto the page")
        info("3. Your site is live instantly with HTTPS")
        return None

    dist = FRONTEND / "dist"
    if not dist.exists():
        warn("No dist folder — run build first")
        return None

    # Create _redirects for SPA routing
    (dist / "_redirects").write_text("/*  /index.html  200\n")

    info("Deploying to Netlify...")
    result = subprocess.run(
        [netlify, "deploy", "--prod", "--dir", str(dist), "--json"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            url = data.get("deploy_url") or data.get("url", "")
            ok(f"Deployed! URL: {url}")
            return url
        except Exception:
            ok("Deployed!")
            return None
    else:
        warn(f"Netlify deploy failed: {result.stderr[-500:]}")
        info("Try: netlify login  →  then re-run this script")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Manual deploy guide
# ─────────────────────────────────────────────────────────────────────────────

def print_manual_guide():
    head("Step-by-Step Deploy Guide")

    print("""
  The goal: Backend on Render, Frontend on Netlify → Open on Android Chrome
  → "Add to Home Screen" → Apex Terminal installed as app.

  ─────────────────────────────────────────────────────────────────────────
  PART 1 — Backend on Render (free tier, ~$0/month)
  ─────────────────────────────────────────────────────────────────────────
  """)

    step(1, "Push this project to GitHub")
    print("""
      git init
      git add .
      git commit -m "Apex Terminal v12"
      # Create a new repo at github.com, then:
      git remote add origin https://github.com/YOUR_USER/apex-terminal.git
      git push -u origin main
    """)

    step(2, "Create a Render account")
    print("""
      → Go to https://render.com → Sign up (free)
      → Dashboard → New → Blueprint
      → Connect your GitHub repo
      → Render reads render.yaml automatically and creates:
          • apex-api    (FastAPI backend)
          • apex-db     (PostgreSQL)
          • apex-terminal (frontend — optional, use Netlify instead)
    """)

    step(3, "Configure environment on Render")
    print("""
      → apex-api service → Environment:
          DATA_PROVIDER = hybrid
          SCORE_WORKERS = 4
          XBRL_CACHE_TTL_DAYS = 7
      → After first deploy, open Shell and run:
          python seed_demo.py
          # Or for real data:
          curl -X POST https://your-api.onrender.com/universe/ingest?workers=4
    """)

    step(4, "Note your backend URL")
    print("""
      Your Render backend will have a URL like:
          https://apex-api-xxxx.onrender.com
      Copy this — you need it for the frontend.
    """)

    print("""
  ─────────────────────────────────────────────────────────────────────────
  PART 2 — Frontend on Netlify (free tier, HTTPS automatic)
  ─────────────────────────────────────────────────────────────────────────
  """)

    step(5, "Set your backend URL and build")
    print("""
      # Edit frontend/.env.production:
      VITE_API_BASE_URL=https://apex-api-xxxx.onrender.com

      # Build:
      cd frontend
      npm install
      npm run build
      # Output: frontend/dist/
    """)

    step(6, "Deploy to Netlify (drag-and-drop, no CLI needed)")
    print("""
      → Go to https://app.netlify.com/drop
      → Drag the frontend/dist/ folder onto the page
      → Netlify gives you a URL like: https://apex-xxxx.netlify.app
      → Add a _redirects file in dist/:
          echo "/*  /index.html  200" > frontend/dist/_redirects
      → (for SPA routing to work correctly)
    """)

    step(7, "Or with Netlify CLI (one command)")
    print("""
      npm install -g netlify-cli
      netlify login
      python apex_deploy.py --frontend
    """)

    print("""
  ─────────────────────────────────────────────────────────────────────────
  PART 3 — Install on Android
  ─────────────────────────────────────────────────────────────────────────
  """)

    step(8, "Open Chrome on Android")
    print("""
      → Navigate to your Netlify URL (https://apex-xxxx.netlify.app)
      → Wait for the page to fully load
    """)

    step(9, "Install the app")
    print("""
      → Chrome shows a banner at the bottom: "Add Apex Terminal to Home screen"
      → Tap "Install" (or "Add")
      → OR: Chrome menu (⋮) → "Add to Home screen"
      → Apex Terminal appears as an app icon on your home screen
    """)

    step(10, "Launch")
    print("""
      → Tap the Apex icon
      → Opens fullscreen, no browser chrome — looks like a native app
      → Bottom navigation: Dashboard · Scanner · Carteira
      → Works offline for app shell (data requires connection)
    """)

    print(_c("92", """
  ─────────────────────────────────────────────────────────────────────────
  Done. Apex Terminal is now a PWA on your Android device.
  ─────────────────────────────────────────────────────────────────────────
    """))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Apex Terminal deploy helper")
    p.add_argument("--check",    action="store_true", help="Check prerequisites only")
    p.add_argument("--frontend", action="store_true", help="Build + deploy frontend only")
    p.add_argument("--guide",    action="store_true", help="Print manual deploy guide")
    p.add_argument("--backend-url", type=str, default=None,
                   help="Backend URL for VITE_API_BASE_URL (e.g. https://apex-api.onrender.com)")
    args = p.parse_args()

    print(_c("1;96", "\n  Apex Terminal — Deploy to Cloud & Android PWA\n"))

    if args.check:
        check_prerequisites()
        return

    if args.guide:
        print_manual_guide()
        return

    if args.frontend:
        check_prerequisites()
        if build_frontend(args.backend_url):
            url = deploy_to_netlify()
            if url:
                head("Install on Android")
                print(f"\n  Open Chrome on Android and go to:\n")
                print(f"  {_c('1;92', url)}\n")
                print("  Chrome will prompt to install Apex Terminal as an app.\n")
        return

    # Default: show guide
    print_manual_guide()


if __name__ == "__main__":
    main()
