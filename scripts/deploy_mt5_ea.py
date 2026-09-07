#!/usr/bin/env python3
"""
scripts/deploy_mt5_ea.py

Automates deploying GoldHedgerPro_v4 (MQL5 EA + .set preset) to MetaTrader 5:
1. Searches for MetaTrader 5 / Wine / CodeWeavers installation paths on macOS.
2. Copies GoldHedgerPro_v4.mq5 and GoldHedgerPro_v4.set to MQL5/Experts/ and Presets.
3. Launches MetaTrader 5 / MetaEditor if available.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EA_DIR = REPO_ROOT / "mt5_ea"
MQ5_FILE = EA_DIR / "GoldHedgerPro_v4.mq5"
SET_FILE = EA_DIR / "GoldHedgerPro_v4.set"

# Standard MetaTrader 5 macOS Wine data paths
SEARCH_DIRS = [
    Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5",
    Path.home() / "Library/Application Support/com.exness.metatrader5",
    Path.home() / "Library/Application Support/MetaTrader 5",
    Path.home() / ".wine/drive_c/Program Files/MetaTrader 5",
    Path.home() / "Library/Application Support/CrossOver/Bottles",
]


def find_mql5_experts_dirs():
    found = []
    for base in SEARCH_DIRS:
        if not base.exists():
            continue
        for root, dirs, _ in os.walk(base):
            if "MQL5" in dirs:
                mql5 = Path(root) / "MQL5"
                experts = mql5 / "Experts"
                presets = mql5 / "Presets"
                experts.mkdir(parents=True, exist_ok=True)
                presets.mkdir(parents=True, exist_ok=True)
                found.append((experts, presets))
    return found


def deploy():
    print("=" * 60)
    print("🚀 GoldHedgerPro v4.03 -> MetaTrader 5 Deployment")
    print("=" * 60)

    if not MQ5_FILE.exists():
        print(f"❌ Error: {MQ5_FILE} not found.", file=sys.stderr)
        return False
    if not SET_FILE.exists():
        print(f"❌ Error: {SET_FILE} not found.", file=sys.stderr)
        return False

    print(f"📄 Source EA:     {MQ5_FILE} ({MQ5_FILE.stat().st_size:,} bytes)")
    print(f"⚙️ Source Preset: {SET_FILE} ({SET_FILE.stat().st_size:,} bytes)")

    targets = find_mql5_experts_dirs()

    if not targets:
        print("\n⚠️  No active MetaTrader 5 MQL5/Experts directories discovered yet.")
        print("    Checking if MetaTrader 5 application bundle exists...")
        # Check /Applications
        app_candidates = [
            Path("/Applications/MetaTrader 5.app"),
            Path("/Applications/MetaTrader5.app"),
            Path.home() / "Applications/MetaTrader 5.app",
        ]
        app_found = next((a for a in app_candidates if a.exists()), None)
        if app_found:
            print(f"    Found MT5 App: {app_found}")
            print(f"    Launching {app_found.name} to initialize data directories...")
            subprocess.run(["open", str(app_found)])
        else:
            print("    MetaTrader 5 app is not yet installed in /Applications.")
        return False

    deployed = 0
    for exp_dir, pres_dir in targets:
        dest_ea = exp_dir / MQ5_FILE.name
        dest_set = pres_dir / SET_FILE.name
        shutil.copy2(MQ5_FILE, dest_ea)
        shutil.copy2(SET_FILE, dest_set)
        # Also put .set in Experts directory for easy access
        shutil.copy2(SET_FILE, exp_dir / SET_FILE.name)
        print(f"\n✅ Deployed to MT5 Experts: {dest_ea}")
        print(f"✅ Deployed to MT5 Presets: {dest_set}")
        deployed += 1

    print(f"\n🎉 Successfully deployed to {deployed} MetaTrader 5 profile(s)!")
    return True


if __name__ == "__main__":
    deploy()
