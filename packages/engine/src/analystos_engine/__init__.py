"""AnalystOS analytical engine.

Pure library (no web framework imports): ingestion, the DuckDB workspace store,
profiling, data quality, relationship discovery, the semantic layer and its
grain-safe compiler, read-only SQL enforcement, query validation, analytical
algorithms (comparison, contribution, decomposition, anomaly, forecast, stats,
segmentation, correlation), connectors and the Python sandbox.
"""

__version__ = "0.1.0"
ENGINE_VERSION = __version__

__all__ = ["__version__", "ENGINE_VERSION"]
