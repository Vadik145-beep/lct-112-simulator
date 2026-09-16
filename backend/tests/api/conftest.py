"""Training API tests need the reference tables and the seed scenarios."""

import pytest

from app.importers import organizers
from app.seed import seed
from tests.conftest import BACKEND_DIR

DATA_DIR = BACKEND_DIR.parent / "data"


@pytest.fixture(scope="package", autouse=True)
async def dataset() -> None:
    """Reference tables from the dataset, then the seed again so scenarios link to tickets."""
    await organizers.run(DATA_DIR)
    await seed(DATA_DIR)
