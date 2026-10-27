"""system-proxy: a unified catalog, not a set of duplicated scrapers."""

from .collect import SOURCES, Collector, Source, normalize_endpoint

__all__ = ["SOURCES", "Collector", "Source", "normalize_endpoint"]
