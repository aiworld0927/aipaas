from __future__ import annotations

import pytest

from aipaas_pipeline.chunking import InMemoryChunkStore
from aipaas_pipeline.repository import InMemoryParseResultRepository
from aipaas_pipeline.service import ParseService
from aipaas_pipeline.storage import InMemoryArtifactStore


@pytest.fixture()
def service() -> ParseService:
    return ParseService(
        artifacts=InMemoryArtifactStore(),
        chunks=InMemoryChunkStore(),
        results=InMemoryParseResultRepository(),
    )
