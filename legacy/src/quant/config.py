"""配置文件加载：config.yaml + .env"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Config:
    def __init__(self, yaml_path: Path | None = None, env_file: Path | None = None):
        yaml_path = Path(yaml_path or (PROJECT_ROOT / "config.yaml"))
        if not yaml_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {yaml_path}")
        load_dotenv(PROJECT_ROOT / ".env")
        with open(yaml_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        self.source = str(cfg.get("data", {}).get("source", "akshare"))
        self.start = str(cfg.get("data", {}).get("start", "2020-01-01"))
        env_symbols = os.getenv("SYMBOLS") or ""
        env_symbols = [s for s in env_symbols.split(",") if s]
        yaml_symbols = cfg.get("data", {}).get("symbols", env_symbols)
        if not isinstance(yaml_symbols, list):
            yaml_symbols = yaml_symbols.split(",") if isinstance(yaml_symbols, str) else []
        self.symbols = [s for s in (yaml_symbols or env_symbols) if s]
        self.data_dir = Path(cfg.get("data_dir", "data"))
        if not str(self.data_dir).startswith("/"):
            self.data_dir = PROJECT_ROOT / self.data_dir
        self.cache_days = int(cfg.get("data", {}).get("cache_days", 30))

    def __repr__(self):
        return f"Config(source={self.source!r}, start={self.start!r}, symbols={self.symbols}, data_dir={self.data_dir})"
