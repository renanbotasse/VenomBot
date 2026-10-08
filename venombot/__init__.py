"""VenomBot — multilingual AML/KYC web crawler for MENA + US sources."""

from venombot.crawler import MultilingualWebCrawler
from venombot.models import AMLFinding, AMLSource, Snapshot, TranslatedSnapshot
from venombot.reporter import AMLReporter
from venombot.sources import AMLSourcesRegistry
from venombot.storage import SnapshotDatabase
from venombot.translator import SimpleTranslator

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "AMLFinding",
    "AMLReporter",
    "AMLSource",
    "AMLSourcesRegistry",
    "MultilingualWebCrawler",
    "SimpleTranslator",
    "Snapshot",
    "SnapshotDatabase",
    "TranslatedSnapshot",
]
