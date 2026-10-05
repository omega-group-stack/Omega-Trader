"""Historical data acquisition.

Getting *real* data is the single biggest upgrade you can make to a backtest,
so Omega ships readers for the formats brokers actually hand out:

``hst``
    MetaTrader 4 history files (both the old 400 and the modern 401 layouts).
    This is what the ``History Center`` exports and what most public FX
    datasets are published as.
``csv``
    Anything tabular — broker exports, Dukascopy, HistData, TrueFX.

The :class:`HstReader` is pure stdlib + numpy, so it also works offline on a
file you downloaded yourself.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import struct
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .base import resample, sanitize
from .timeframes import TIMEFRAME_MINUTES, normalize

log = logging.getLogger(__name__)

USER_AGENT = "Omega-Trader/1.0 (+historical data fetch)"

#: Public MetaTrader history sets, keyed by symbol. Each repository publishes
#: one ``.hst.gz`` per timeframe named ``<SYMBOL><minutes>.hst.gz``.
GITHUB_HST_SETS: Dict[str, str] = {
    "EURUSD": "FX-Data/FX-Data-EURUSD-DS",
    "GBPUSD": "FX-Data/FX-Data-GBPUSD-DS",
    "USDJPY": "FX-Data/FX-Data-USDJPY-DS",
    "USDCHF": "FX-Data/FX-Data-USDCHF-DS",
    "AUDUSD": "FX-Data/FX-Data-AUDUSD-DS",
    "USDCAD": "FX-Data/FX-Data-USDCAD-DS",
    "NZDUSD": "FX-Data/FX-Data-NZDUSD-DS",
}


# --------------------------------------------------------------------------- #
# MetaTrader .hst
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class HstHeader:
    version: int
    copyright: str
    symbol: str
    period_minutes: int
    digits: int

    @property
    def timeframe(self) -> str:
        for name, minutes in TIMEFRAME_MINUTES.items():
            if minutes == self.period_minutes:
                return name
        return f"M{self.period_minutes}"


class HstReader:
    """Parser for MetaTrader 4 ``.hst`` history files.

    Layout (little-endian)::

        header  148 bytes: int32 version, char[64] copyright, char[12] symbol,
                           int32 period, int32 digits, int32 timesign,
                           int32 last_sync, int32[13] unused

        v400 record  44 bytes: int32  time, double open, double low,
                               double high, double close, double volume
        v401 record  60 bytes: int64  time, double open, double high,
                               double low, double close, int64 tick_volume,
                               int32 spread, int64 real_volume

    Note the open/low/high vs open/high/low ordering difference — getting that
    wrong silently produces plausible-looking but corrupt candles.
    """

    HEADER_SIZE = 148
    V400_RECORD = 44
    V401_RECORD = 60

    def __init__(self, data: bytes) -> None:
        if len(data) < self.HEADER_SIZE:
            raise ValueError(f"file too small to be a .hst ({len(data)} bytes)")
        self.data = data
        self.header = self._read_header()

    @classmethod
    def from_path(cls, path: str | Path) -> "HstReader":
        p = Path(path)
        raw = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
        return cls(raw)

    def _read_header(self) -> HstHeader:
        version, copyright_, symbol, period, digits = struct.unpack(
            "<i64s12sii", self.data[:88]
        )
        return HstHeader(
            version=version,
            copyright=copyright_.split(b"\x00")[0].decode("latin-1", "ignore").strip(),
            symbol=symbol.split(b"\x00")[0].decode("latin-1", "ignore").strip(),
            period_minutes=period,
            digits=digits,
        )

    def to_frame(self) -> pd.DataFrame:
        """Decode every bar into the standard OHLCV frame."""
        body = self.data[self.HEADER_SIZE:]
        version = self.header.version

        if version == 401:
            size = self.V401_RECORD
            count = len(body) // size
            dtype = np.dtype([
                ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                ("close", "<f8"), ("volume", "<i8"), ("spread", "<i4"),
                ("real_volume", "<i8"),
            ])
            records = np.frombuffer(body[: count * size], dtype=dtype)
            frame = pd.DataFrame({
                "time": pd.to_datetime(records["time"], unit="s", utc=True),
                "open": records["open"], "high": records["high"],
                "low": records["low"], "close": records["close"],
                "volume": records["volume"].astype(float),
                "spread": records["spread"].astype(float),
            })
        elif version == 400:
            size = self.V400_RECORD
            count = len(body) // size
            dtype = np.dtype([
                ("time", "<i4"), ("open", "<f8"), ("low", "<f8"), ("high", "<f8"),
                ("close", "<f8"), ("volume", "<f8"),
            ])
            records = np.frombuffer(body[: count * size], dtype=dtype)
            frame = pd.DataFrame({
                "time": pd.to_datetime(records["time"], unit="s", utc=True),
                "open": records["open"], "high": records["high"],
                "low": records["low"], "close": records["close"],
                "volume": records["volume"].astype(float),
                "spread": 0.0,
            })
        else:
            raise ValueError(f"unsupported .hst version {version}")

        log.info(
            "Parsed %s %s: %d bars (v%d, %d digits)",
            self.header.symbol, self.header.timeframe, len(frame), version,
            self.header.digits,
        )
        return sanitize(frame)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _http_get(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def list_github_releases(repo: str) -> List[dict]:
    import json

    raw = _http_get(f"https://api.github.com/repos/{repo}/releases?per_page=20", 60)
    return json.loads(raw.decode("utf-8"))


#: Repository holding one directory per pair, each with a CSV per timeframe.
_SNOWGURU = "TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data"

#: Every pair that repository publishes. The seven USD majors are not enough
#: on their own for cross-sectional work: in a basket of USD majors each
#: non-USD currency appears in exactly one pair, so its "strength" is just
#: that pair's own return. The crosses are what make the cross-section real.
SNOWGURU_PAIRS: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "AUDCAD", "AUDCHF",
)

#: Curated public OHLCV sets held directly in a git tree (symbol -> repo/path).
GITHUB_CSV_SETS: Dict[str, tuple[str, str]] = {
    sym: (_SNOWGURU, f"forex/{sym.lower()}/{sym}_{{tf}}.csv")
    for sym in SNOWGURU_PAIRS
}


def download_github_csv(
    symbol: str,
    timeframe: str = "M15",
    out_dir: str | Path = "data",
    repo: Optional[str] = None,
    path_template: Optional[str] = None,
    token: Optional[str] = None,
) -> Path:
    """Fetch an OHLCV CSV that lives inside a GitHub repository.

    Uses the **Git blob API** rather than ``raw.githubusercontent.com``: the
    blob endpoint handles files up to 100 MB, needs no extra host, and works
    from locked-down networks where only ``api.github.com`` is reachable.
    """
    import base64
    import json
    import os

    sym = symbol.upper()
    tf = normalize(timeframe)

    if repo is None or path_template is None:
        known = GITHUB_CSV_SETS.get(sym)
        if known is None:
            raise ValueError(
                f"No public CSV set registered for {sym}; pass repo= and "
                f"path_template= explicitly."
            )
        repo, path_template = known
    file_path = path_template.format(tf=tf, symbol=sym, sym=sym)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def api(url: str) -> dict:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    meta = api(f"https://api.github.com/repos/{repo}/contents/{file_path}")
    log.info("Fetching %s/%s (%.1f MB)", repo, file_path, meta.get("size", 0) / 1e6)
    blob = api(f"https://api.github.com/repos/{repo}/git/blobs/{meta['sha']}")
    raw = base64.b64decode(blob["content"])

    frame = _read_tabular(BytesIO(raw))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym}_{tf}.csv"
    frame.to_csv(out_path, index=True, index_label="time")
    log.info("Wrote %s — %d bars, %s → %s",
             out_path, len(frame), frame.index[0].date(), frame.index[-1].date())
    return out_path


def _read_tabular(buffer) -> pd.DataFrame:
    """Read a CSV/TSV OHLCV export, sniffing the delimiter."""
    head = buffer.read(4096)
    buffer.seek(0)
    text = head.decode("utf-8", "ignore") if isinstance(head, bytes) else head
    separator = "\t" if text.count("\t") > text.count(",") else ","
    frame = pd.read_csv(buffer, sep=separator)
    return sanitize(frame)


def download_hst(
    symbol: str,
    timeframe: str = "M15",
    out_dir: str | Path = "data",
    repo: Optional[str] = None,
    release: Optional[str] = None,
) -> Path:
    """Download one MetaTrader history file and store it as CSV.

    Returns the path of the written CSV, ready for ``data.source: csv``.
    """
    sym = symbol.upper()
    tf = normalize(timeframe)
    minutes = TIMEFRAME_MINUTES[tf]
    repo = repo or GITHUB_HST_SETS.get(sym)
    if not repo:
        raise ValueError(
            f"No public dataset registered for {sym}. Pass repo=... or export "
            f"the history yourself from MetaTrader (File ▸ Open Data Folder ▸ history)."
        )

    releases = list_github_releases(repo)
    if not releases:
        raise RuntimeError(f"{repo} publishes no releases")
    chosen = None
    if release:
        chosen = next((r for r in releases if r["tag_name"] == release), None)
        if chosen is None:
            raise ValueError(
                f"release {release!r} not found — available: "
                f"{', '.join(r['tag_name'] for r in releases)}"
            )
    else:
        chosen = max(releases, key=lambda r: r["tag_name"])

    asset_name = f"{sym}{minutes}.hst.gz"
    asset = next((a for a in chosen["assets"] if a["name"] == asset_name), None)
    if asset is None:
        available = sorted(
            a["name"] for a in chosen["assets"] if a["name"].endswith(".hst.gz")
        )
        raise ValueError(
            f"{asset_name} not in release {chosen['tag_name']}. Available: {available}"
        )

    log.info("Downloading %s (%s, %.1f MB) from %s",
             asset_name, chosen["tag_name"], asset["size"] / 1e6, repo)
    blob = _http_get(asset["browser_download_url"])
    frame = HstReader(gzip.decompress(blob)).to_frame()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sym}_{tf}.csv"
    frame.to_csv(out_path, index=True, index_label="time")
    log.info(
        "Wrote %s — %d bars, %s → %s",
        out_path, len(frame), frame.index[0].date(), frame.index[-1].date(),
    )
    return out_path


def build_timeframes(
    source_csv: str | Path,
    timeframes: Iterable[str],
    out_dir: str | Path = "data",
    symbol: Optional[str] = None,
) -> List[Path]:
    """Derive higher timeframes from one fine-grained CSV."""
    source = Path(source_csv)
    base = sanitize(pd.read_csv(source))
    sym = symbol or source.stem.split("_")[0].upper()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for tf in timeframes:
        frame = resample(base, tf)
        path = out_dir / f"{sym}_{normalize(tf)}.csv"
        frame.to_csv(path, index=True, index_label="time")
        log.info("Wrote %s — %d bars", path, len(frame))
        written.append(path)
    return written


def import_zip(archive: str | Path, out_dir: str | Path = "data") -> List[Path]:
    """Unpack a broker/HistData ZIP of CSVs into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            target = out_dir / Path(name).name
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(target)
            log.info("Extracted %s", target)
    return written


def describe(csv_path: str | Path) -> dict:
    """Quick quality report for a data file — run this before trusting it."""
    frame = sanitize(pd.read_csv(csv_path))
    if frame.empty:
        return {"path": str(csv_path), "bars": 0}

    gaps = frame.index.to_series().diff().dropna()
    typical = gaps.median()
    weekday_gaps = gaps[gaps > typical * 3]

    return {
        "path": str(csv_path),
        "bars": len(frame),
        "start": str(frame.index[0]),
        "end": str(frame.index[-1]),
        "years": round((frame.index[-1] - frame.index[0]).days / 365.25, 2),
        "median_gap": str(typical),
        "large_gaps": int(len(weekday_gaps)),
        "zero_volume_bars": int((frame["volume"] <= 0).sum()),
        "has_spread": bool((frame["spread"] > 0).any()),
        "duplicate_times": int(frame.index.duplicated().sum()),
        "price_range": [round(float(frame["low"].min()), 5),
                        round(float(frame["high"].max()), 5)],
    }
