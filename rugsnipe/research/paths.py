"""All data paths are relative to the rugsnipe directory (override with RUGSNIPE_DATA)."""
import os
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("RUGSNIPE_DATA", HOME / "data"))
BF = str(DATA / "bf") + "/"
LIVE = str(DATA / "live") + "/"
REC = str(DATA / "rec") + "/"
