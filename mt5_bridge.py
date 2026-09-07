"""
mt5_bridge.py — High-Speed Native IPC Bridge between Gold AI Platform and MetaTrader 5

Bypasses Windows DLL restrictions on macOS Wine by using high-speed shared file IPC:
- Writes trading signals (BUY, SELL, CLOSE_ALL) to MQL5/Files/ai_signals.json
- Reads real-time MT5 terminal telemetry from MQL5/Files/mt5_state.json
- Synchronizes real MT5 account balance ($100,000.00), equity, and open positions
- Exposes CLI commands and background daemon for continuous execution
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MT5Bridge")

MT5_FILES_DIR = Path(
    "/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
)
CMD_FILE   = MT5_FILES_DIR / "ai_signals.json"
STATE_FILE = MT5_FILES_DIR / "mt5_state.json"
LOCAL_STATE_FILE = Path("logs/paper_state.json")


class MT5Bridge:
    def __init__(self, symbol: str = "XAUUSD"):
        self.symbol = symbol
        MT5_FILES_DIR.mkdir(parents=True, exist_ok=True)

    def send_order(self, action: str, volume: float = 0.01, sl: float = 0.0,
                   tp: float = 0.0, comment: str = "AI_Quant_Ensemble") -> dict:
        """Sends an immediate execution signal to MetaTrader 5."""
        action = action.upper()
        if action not in ("BUY", "SELL", "CLOSE_ALL", "FLATTEN"):
            raise ValueError(f"Unknown action: {action}")

        payload = {
            "action":    "CLOSE_ALL" if action in ("CLOSE_ALL", "FLATTEN") else action,
            "symbol":    self.symbol,
            "volume":    round(volume, 2),
            "sl":        round(sl, 2),
            "tp":        round(tp, 2),
            "comment":   comment,
            "timestamp": int(time.time()),
        }

        # Write atomically via temp file
        tmp = CMD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(CMD_FILE)

        logger.info(f"⚡ [MT5Bridge] Sent {action} order to MetaTrader 5: {payload}")
        return payload

    def read_mt5_state(self) -> dict:
        """Reads live account telemetry written by GoldHedgerPro_v4 in MT5."""
        if not STATE_FILE.exists():
            return {"connected": False, "status": "WAITING_FOR_MT5_TICK"}

        try:
            data = json.loads(STATE_FILE.read_text())
            data["connected"] = True
            return data
        except Exception as e:
            return {"connected": False, "error": str(e)}

    def sync_to_platform(self):
        """Merges live MT5 account into local dashboard state."""
        mt5_data = self.read_mt5_state()
        if not mt5_data.get("connected"):
            return

        if LOCAL_STATE_FILE.exists():
            try:
                state = json.loads(LOCAL_STATE_FILE.read_text())
                state["mt5_live"] = mt5_data
                if "balance" in mt5_data:
                    state["mt5_account"] = {
                        "account_id": mt5_data.get("account_id"),
                        "server": mt5_data.get("server"),
                        "balance": mt5_data.get("balance"),
                        "equity": mt5_data.get("equity"),
                        "free_margin": mt5_data.get("free_margin"),
                        "profit": mt5_data.get("profit"),
                        "positions": mt5_data.get("positions", [])
                    }
                LOCAL_STATE_FILE.write_text(json.dumps(state, indent=2))
            except Exception:
                pass

    def run_daemon(self, poll_interval: float = 2.0):
        """Continuous bridge loop synchronizing AI signals and MT5 executions."""
        logger.info(f"🚀 [MT5Bridge] Daemon started. Monitoring {MT5_FILES_DIR}...")
        while True:
            try:
                # 1. Check local dashboard command queue
                local_cmd = Path("data/command_queue.json")
                if local_cmd.exists():
                    try:
                        cmds = json.loads(local_cmd.read_text())
                        if not isinstance(cmds, list):
                            cmds = [cmds]
                        local_cmd.unlink(missing_ok=True)
                        for c in cmds:
                            cmd_type = c.get("command")
                            if cmd_type == "manual_trade":
                                side = c.get("direction", "BUY")
                                self.send_order(side, volume=0.01, comment="Manual_Dashboard")
                            elif cmd_type in ("close_all", "flatten"):
                                self.send_order("CLOSE_ALL", comment="Manual_Flatten")
                    except Exception as ex:
                        logger.error(f"[MT5Bridge] Error processing local command queue: {ex}")

                # 2. Sync state
                self.sync_to_platform()

            except Exception as e:
                logger.error(f"[MT5Bridge] Loop error: {e}")

            time.sleep(poll_interval)


def main():
    bridge = MT5Bridge()
    args = sys.argv[1:]

    if not args:
        print("Usage: python mt5_bridge.py [buy|sell|flatten|status|daemon] [volume]")
        return

    cmd = args[0].lower()
    vol = float(args[1]) if len(args) > 1 else 0.01

    if cmd == "buy":
        bridge.send_order("BUY", volume=vol)
    elif cmd == "sell":
        bridge.send_order("SELL", volume=vol)
    elif cmd in ("flatten", "close_all"):
        bridge.send_order("CLOSE_ALL")
    elif cmd == "status":
        s = bridge.read_mt5_state()
        print(json.dumps(s, indent=2))
    elif cmd == "daemon":
        bridge.run_daemon()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
