# Copyright European Organization for Nuclear Research (CERN) since 2012
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from unittest.mock import MagicMock

import pytest

from rucio.core import distance as distance_core
from rucio.core import replica as replica_core
from rucio.core import rule as rule_core
from rucio.core.request import list_and_mark_transfer_requests_and_source_replicas
from rucio.core.topology import Topology
from rucio.core.transfer import ProtocolFactory, build_transfer_paths
from rucio.db.sqla.session import get_session
from rucio.transfertool.fts3 import FTS3Transfertool, build_job_params
from rucio.transfertool.fts3_plugins import FTS3TapeMetadataPlugin

mock_session = get_session()

MAX_POLL_WAIT_SECONDS = 60
TEST_FTS_HOST = "https://fts:8446"


def _make_transfer_path(
        did,
        rse_factory,
        root_account,
        src_rse_is_tape: bool = False,
        dst_rse_is_tape: bool = True
):
    _, src_rse_id = rse_factory.make_mock_rse(rse_type="TAPE" if src_rse_is_tape else "DISK")
    dst_rse, dst_rse_id = rse_factory.make_mock_rse(rse_type="TAPE" if dst_rse_is_tape else "DISK")
    all_rses = [src_rse_id, dst_rse_id]
    distance_core.add_distance(src_rse_id, dst_rse_id, distance=1)

    topology = Topology(rse_ids=all_rses)
    file = {"scope": did["scope"], "name": did["name"], "bytes": 1}
    replica_core.add_replicas(rse_id=src_rse_id, files=[file], account=root_account)
    rule_core.add_rule(
        dids=[did],
        account=root_account,
        copies=1,
        rse_expression=dst_rse,
        grouping="ALL",
        weight=None,
        lifetime=None,
        locked=False,
        subscription_id=None,
    )

    requests_by_id = list_and_mark_transfer_requests_and_source_replicas(
        rse_collection=topology, rses=[src_rse_id, dst_rse_id]
    )
    requests, *_ = build_transfer_paths(
        topology=topology,
        protocol_factory=ProtocolFactory(),
        requests_with_sources=requests_by_id.values(),
    )
    _, [transfer_path] = next(iter(requests.items()))

    return transfer_path


@pytest.mark.parametrize("file_config_mock", [
    {
        "overrides": [
            ('tape_priority', 'User Subscriptions', '100'),
            ("transfers", "fts3tape_metadata_plugins", "activity")
        ]
    }
], indirect=True)
def test_scheduling_hints(file_config_mock, did_factory, rse_factory, root_account):

    """Transfer with an installed plugin for scheduling based on transfer activity"""

    # Produce a new DID
    mock_did = did_factory.random_file_did()

    # Make the transfer path
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

    # Mock Transfer Tool
    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )
    # Get the job params used for each transfer
    job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)

    # Extract the hints
    assert "archive_metadata" in job_params
    assert len(job_params["archive_metadata"].keys()) == 1
    generated_scheduling_hints = job_params["archive_metadata"]["scheduling_hints"]

    expected_scheduling_hints = {"priority": 100}
    assert expected_scheduling_hints == generated_scheduling_hints


@pytest.mark.parametrize("file_config_mock", [
    {
        "overrides": [
            ("transfers", "fts3tape_metadata_plugins", "activity")
        ]
    }
], indirect=True)
def test_activity_missing(file_config_mock, did_factory, rse_factory, root_account):
    """Ensure default is selected when the activity is not listed in the config, but is in the schema"""
    # Do not add config section for priority, but do add the FTS3
    # Produce a new DID
    mock_did = did_factory.random_file_did()

    # Make the transfer path
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

    # Mock Transfer Tool
    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    # Get the job params used for each transfer
    transfer_params = transfer_path[0]
    transfer_params.rws.activity = "Not A Real Activity"

    job_params = fts3_tool._file_from_transfer(transfer_params, job_params)

    # Extract the hints
    assert "archive_metadata" in job_params
    generated_scheduling_hints = job_params["archive_metadata"]["scheduling_hints"]

    expected_scheduling_hints = {"priority": 20}
    assert expected_scheduling_hints == generated_scheduling_hints


class TestCollocationHints:
    class MockCollocationPlugin(FTS3TapeMetadataPlugin):
        def __init__(self) -> None:
            self.register(
                'test',
                func=lambda x: self._test_collocation(x))
            super().__init__('test')

        def _test_collocation(self, hints: dict[str, str]) -> dict[str, dict]:
            return {"collocation_hints": {"0": "", "1": "", "2": "", "3": ""}}

    MockCollocationPlugin()

    @pytest.mark.parametrize("file_config_mock", [
        {
            "overrides": [
                ("transfers", "fts3tape_metadata_plugins", "test")
            ]
        }
    ], indirect=True)
    def test_collocation_hints(self, file_config_mock, did_factory, rse_factory, root_account):
        """For a mock collocation algorithm, it can produce the 4 levels of hints required for each DID"""

        mock_did = did_factory.random_file_did()
        transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

        # Mock Transfer Tool
        fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

        job_params = build_job_params(
            transfer_path=transfer_path,
            bring_online=None,
            default_lifetime=None,
            archive_timeout_override=None,
            max_time_in_queue=None,
            logger=logging.log,
        )

        # Get the job params used for each transfer
        job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)

        expected_collocation_hints = {
            "collocation_hints": {
                "0": "",
                "1": "",
                "2": "",
                "3": "",
            }
        }

        assert "archive_metadata" in job_params
        generated_collocation_hints = job_params["archive_metadata"]["collocation_hints"]

        assert (
            expected_collocation_hints["collocation_hints"] == generated_collocation_hints
        )

    @pytest.mark.parametrize("file_config_mock", [
        {
            "overrides": [
                ("transfers", "fts3tape_metadata_plugins", "activity, test")
            ]
        }
    ], indirect=True)
    def test_multiple_plugin_concat(self, file_config_mock, did_factory, rse_factory, root_account):
        """When multiple plugins are used (like priority and collocation), both logics are applied"""

        mock_did = did_factory.random_file_did()
        transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

        # Mock Transfer Tool
        fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

        job_params = build_job_params(
            transfer_path=transfer_path,
            bring_online=None,
            default_lifetime=None,
            archive_timeout_override=None,
            max_time_in_queue=None,
            logger=logging.log,
        )

        # Get the job params used for each transfer
        job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)
        expected_hints = {
            "scheduling_hints": {"priority": 20},
            "collocation_hints": {"0": "", "1": "", "2": "", "3": ""},
        }
        assert "archive_metadata" in job_params

        generated_collocation_hints = job_params["archive_metadata"]["collocation_hints"]
        assert expected_hints["collocation_hints"] == generated_collocation_hints

        generated_scheduling_hints = job_params["archive_metadata"]["scheduling_hints"]
        assert expected_hints["scheduling_hints"] == generated_scheduling_hints


@pytest.mark.parametrize("file_config_mock", [
    {
        "overrides": [
            ("transfers", "metadata_byte_limit", "4"),
            ("transfers", "fts3tape_metadata_plugins", "def")

        ]
    }
], indirect=True)
def test_transfer_over_limit(file_config_mock, did_factory, rse_factory, root_account):
    mock_did = did_factory.random_file_did()
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

    # Mock Transfer Tool
    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    from rucio.common.exception import InvalidRequest

    with pytest.raises(InvalidRequest):
        job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)


def test_include_none_by_default(did_factory, rse_factory, root_account):
    # When there is not plugin specified in the config, do not include any archive metadata

    mock_did = did_factory.random_file_did()
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account)

    # Mock Transfer Tool
    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)
    assert "archive_metadata" not in job_params


def test_no_metadata_if_dest_is_not_tape(did_factory, rse_factory, root_account):
    """
    If the destination RSE is not tape,
    no tape metadata plugin should run,
    and no archive metadata should be included.
    """

    mock_did = did_factory.random_file_did()
    mock_metadata_plugin = MagicMock()
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account, dst_rse_is_tape=False)

    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    fts3_tool.tape_metadata_plugins = [mock_metadata_plugin]  # type: ignore

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    job_params = fts3_tool._file_from_transfer(transfer_path[0], job_params)

    assert not mock_metadata_plugin.called
    assert "archive_metadata" not in job_params


def test_staging_metadata_if_src_is_tape(did_factory, rse_factory, root_account):
    """
    If the source RSE is tape,
    staging metadata should be included.
    """

    mock_did = did_factory.random_file_did()
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account, src_rse_is_tape=True)

    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    # Get the job params used for each transfer
    transfer_params = transfer_path[0]
    transfer_params.rws.activity = "Not A Real Activity"

    job_params = fts3_tool._file_from_transfer(transfer_params, job_params)

    actual_staging_metadata = job_params.get("staging_metadata", None)
    assert actual_staging_metadata == {"default": {"activity": "Not A Real Activity"}}


def test_no_staging_metadata_if_src_is_not_tape(did_factory, rse_factory, root_account):
    """
    If the source RSE is not tape,
    staging metadata should not be included.
    """

    mock_did = did_factory.random_file_did()
    transfer_path = _make_transfer_path(mock_did, rse_factory, root_account, src_rse_is_tape=False)

    fts3_tool = FTS3Transfertool(TEST_FTS_HOST)

    job_params = build_job_params(
        transfer_path=transfer_path,
        bring_online=None,
        default_lifetime=None,
        archive_timeout_override=None,
        max_time_in_queue=None,
        logger=logging.log,
    )

    # Get the job params used for each transfer
    transfer_params = transfer_path[0]
    transfer_params.rws.activity = "Not A Real Activity"

    job_params = fts3_tool._file_from_transfer(transfer_params, job_params)

    assert "staging_metadata" not in job_params
