from typing import List
from typing import Optional
from typing import TYPE_CHECKING

import pytest
from _pytest import nodes
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.main import Session
from _pytest.reports import TestReport

if TYPE_CHECKING:
    from _pytest.cacheprovider import Cache

STEPWISE_CACHE_DIR = "cache/stepwise"


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("general")
    group.addoption(
        "--sw",
        "--stepwise",
        action="store_true",
        default=False,
        dest="stepwise",
        help="exit on test failure and continue from last failing test next time",
    )
    group.addoption(
        "--sw-skip",
        "--stepwise-skip",
        action="store_true",
        default=False,
        dest="stepwise_skip",
        help="ignore the first failing test but stop on the next failing test.\n"
        "implicitly enables --stepwise.",
    )


@pytest.hookimpl
def pytest_configure(config: Config) -> None:
    if config.option.stepwise_skip:
        # allow --stepwise-skip to work on it's own merits.
        config.option.stepwise = True
    if config.getoption("stepwise"):
        config.pluginmanager.register(StepwisePlugin(config), "stepwiseplugin")


def pytest_sessionfinish(session: Session) -> None:
    if not session.config.getoption("stepwise"):
        assert session.config.cache is not None
        # Clear the list of failing tests if the plugin is not active.
        session.config.cache.set(STEPWISE_CACHE_DIR, [])


class StepwisePlugin:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session: Optional[Session] = None
        self.report_status = ""
        assert config.cache is not None
        self.cache: Cache = config.cache
        self.lastfailed: Optional[str] = self.cache.get(STEPWISE_CACHE_DIR, None)
        self.skip: bool = config.getoption("stepwise_skip")

    def pytest_sessionstart(self, session: Session) -> None:
        self.session = session

    def pytest_collection_modifyitems(
        self, config: Config, items: List[nodes.Item]
    ) -> None:
        if not self.lastfailed:
            self.report_status = "no previously failed tests, not skipping."
            return

        failed_index = next(
            (
                index
                for index, item in enumerate(items)
                if item.nodeid == self.lastfailed
            ),
            None,
        )
        # If the previously failed test was not found among the test items,
        # do not skip any tests.
        if failed_index is None:
            self.report_status = "previously failed test not found, not skipping."
        else:
            self.report_status = f"skipping {failed_index} already passed items."
            deselected = items[:failed_index]
            del items[:failed_index]
            config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report: TestReport) -> None:
        if report.failed:
            if self.skip:
                # Remove test from the failed ones (if it exists) and unset the skip option
                # to make sure the following tests will not be skipped.
                if report.nodeid == self.lastfailed:
                    self.lastfailed = None

                self.skip = False
            else:
                # Mark test as the last failing and interrupt the test session.
                self.lastfailed = report.nodeid
                assert self.session is not None
                self.session.shouldstop = (
                    "Test failed, continuing from this test next run."
                )

        elif report.when == "call" and report.nodeid == self.lastfailed:
            self.lastfailed = None

    def pytest_report_collectionfinish(self) -> Optional[str]:
        if self.config.getoption("verbose") >= 0 and self.report_status:
            return f"stepwise: {self.report_status}"
        return None

    def pytest_sessionfinish(self) -> None:
        self.cache.set(STEPWISE_CACHE_DIR, self.lastfailed)
