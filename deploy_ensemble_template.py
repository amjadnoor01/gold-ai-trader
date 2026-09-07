"""
deploy_ensemble_template.py — Generates and deploys high-tech MT5 Chart Template across all open MT5 charts.
"""
import os
from pathlib import Path

MT5_BASE = Path("/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5")
TEMPLATES_DIR = MT5_BASE / "MQL5" / "Profiles" / "Templates"
CHARTS_DIR    = MT5_BASE / "MQL5" / "Profiles" / "Charts" / "Default"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE_CONTENT = """<chart>
id=132500000000000000
symbol=XAUUSD
period_type=1
period_size=5
digits=2
scale=4
mode=1
fore=0
grid=1
volume=3
scroll=1
shift=1
shift_size=20.000000
background_color=0
foreground_color=16777215
barup_color=65280
bardown_color=255
bullcandle_color=65280
bearcandle_color=255
chartline_color=65280
volumes_color=3329330
grid_color=2105376
bidline_color=65280
askline_color=255
lastline_color=49152
stops_color=255
windows_total=3

<window>
height=60
<indicator>
name=Main
</indicator>
<indicator>
name=Moving Average
period=50
ma_method=1
apply=0
color=16711935
style=0
weight=2
</indicator>
<indicator>
name=Parabolic SAR
step=0.020000
maximum=0.200000
color=65535
style=0
weight=1
</indicator>
</window>

<window>
height=20
<indicator>
name=Relative Strength Index
period=14
apply=0
color=65280
style=0
weight=1
</indicator>
</window>

<window>
height=20
<indicator>
name=MACD
fast=12
slow=26
signal=9
apply=0
color=16776960
style=0
weight=1
</indicator>
</window>

<expert>
name=AiMultiRobotEnsemble
path=Experts\\Advisors\\AiMultiRobotEnsemble.ex5
expertmode=1
<inputs>
InpWeightAiBrain=0.35
InpWeightDOM=0.20
InpWeightMACD=0.15
InpWeightPSAR=0.15
InpWeightRSI=0.15
InpBaseMagicNumber=999100
InpTranche1Lots=0.02
InpTranche1TP_Pips=18
InpTranche1Trail=true
InpTranche2Lots=0.02
InpTranche2TP_Pips=32
InpTranche3Lots=0.01
InpTranche3TP_Pips=55
InpEnsembleThreshold=35.0
InpPollIntervalSec=2
</inputs>
</expert>
</chart>
"""

def write_utf16le(filepath: Path, content: str):
    with open(filepath, "wb") as f:
        f.write(content.encode("utf-16le"))

def main():
    print("Writing AiMultiRobotEnsemble.tpl & default.tpl...")
    write_utf16le(TEMPLATES_DIR / "AiMultiRobotEnsemble.tpl", TEMPLATE_CONTENT)
    write_utf16le(TEMPLATES_DIR / "default.tpl", TEMPLATE_CONTENT)
    print("✅ Templates written successfully.")

    # Update all active chart files
    expert_block = """<expert>
name=AiMultiRobotEnsemble
path=Experts\\Advisors\\AiMultiRobotEnsemble.ex5
expertmode=1
<inputs>
InpWeightAiBrain=0.35
InpWeightDOM=0.20
InpWeightMACD=0.15
InpWeightPSAR=0.15
InpWeightRSI=0.15
InpBaseMagicNumber=999100
InpTranche1Lots=0.02
InpTranche1TP_Pips=18
InpTranche1Trail=true
InpTranche2Lots=0.02
InpTranche2TP_Pips=32
InpTranche3Lots=0.01
InpTranche3TP_Pips=55
InpEnsembleThreshold=35.0
InpPollIntervalSec=2
</inputs>
</expert>
"""
    chr_files = list(CHARTS_DIR.glob("*.chr"))
    print(f"Updating {len(chr_files)} active chart files in {CHARTS_DIR}...")
    for chr_path in chr_files:
        try:
            raw = chr_path.read_bytes().decode("utf-16le", errors="ignore")
            if "<expert>" in raw and "</expert>" in raw:
                start = raw.find("<expert>")
                end   = raw.find("</expert>") + len("</expert>\n")
                new_raw = raw[:start] + expert_block + raw[end:]
            else:
                pos = raw.rfind("</chart>")
                if pos > 0:
                    new_raw = raw[:pos] + expert_block + "\n</chart>\n"
                else:
                    new_raw = raw + "\n" + expert_block

            write_utf16le(chr_path, new_raw)
            print(f"  - Updated {chr_path.name}")
        except Exception as e:
            print(f"  - Failed to update {chr_path.name}: {e}")

    print("🚀 All MT5 charts configured with AiMultiRobotEnsemble EA!")

if __name__ == "__main__":
    main()
