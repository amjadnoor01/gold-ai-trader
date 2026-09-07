# macOS Wine Setup Guide

This guide details how MetaTrader 5 and launchd daemons are deployed on macOS.

## 1. Directory Structure
- **MT5 Installation Path:** `/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5`
- **MQL5 IPC Path:** `.../MQL5/Files`
- **LaunchAgents Path:** `~/Library/LaunchAgents/`

## 2. Launchd Daemon Control
To start, stop, or inspect launchd services:

```bash
# Check running status
launchctl list | grep antigravity

# Load AI Brain Microservice
launchctl load ~/Library/LaunchAgents/com.antigravity.mt5brain.plist

# Load Autonomy Loop Daemon
launchctl load ~/Library/LaunchAgents/com.antigravity.mt5autonomy.plist

# Inspect logs
tail -f ~/Documents/Desktop/2026\ April/GES/TradingBot/logs/mt5autonomy_stderr.log
```

## 3. Compiling MQL5 EA via Wine MetaEditor
```bash
cd "/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5"
WINEPREFIX="/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5" "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine" "MetaEditor64.exe" /compile:"MQL5\Experts\AiBridgeEA.mq5" /log:"MQL5\Experts\compile_aibridge.log"
```
