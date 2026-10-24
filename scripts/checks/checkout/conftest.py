"""`make check STEP=n`: the checks for steps 1..n, run against your checkout/ code."""

import importlib

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


def pytest_addoption(parser):
    parser.addoption("--step", type=int, default=None, help="only steps 1..STEP")


def pytest_collection_modifyitems(config, items):
    step = config.getoption("--step")
    if step is not None:
        keep = [item for item in items if item.get_closest_marker("step").args[0] <= step]
        config.hook.pytest_deselected(items=[item for item in items if item not in keep])
        items[:] = keep


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    import config

    monkeypatch.setattr(config, "STEP_DELAY_S", 0)


@pytest_asyncio.fixture
async def env():
    # Temporal's test server: timers and retry backoff are skipped, not waited for.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


def need(module: str, name: str, step: int):
    """Something step N creates, or a failure that says which step to look at."""
    try:
        found = importlib.import_module(module)
    except ModuleNotFoundError:
        pytest.fail(f"Step {step}: there is no checkout/{module}.py yet. See the README, step {step}.")
    if not hasattr(found, name):
        pytest.fail(f"Step {step}: {module}.py has no `{name}` yet. See the README, step {step}.")
    return getattr(found, name)
