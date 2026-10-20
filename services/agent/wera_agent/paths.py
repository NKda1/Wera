"""Repository locations. Config and fixtures live at the repo root, shared with the web app."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("WERA_ROOT") or Path(__file__).resolve().parents[3])
CONFIG_DIR = ROOT / "config"
FIXTURES_DIR = ROOT / "fixtures"
SCHEMAS_DIR = ROOT / "packages" / "schemas" / "json"
SUPABASE_DIR = ROOT / "supabase"
AGENT_DIR = ROOT / "services" / "agent"
