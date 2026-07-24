"""Regression coverage for repeated ENCODE file-list calls."""

from unittest.mock import AsyncMock, patch

import pytest

from cortex.tool_dispatch import execute_tool_calls


@pytest.mark.asyncio
async def test_executes_every_multi_experiment_encode_file_call():
    accessions = ("ENCSR432YKA", "ENCSR476STT", "ENCSR448TJV")
    mock_clients = []
    for index, _ in enumerate(accessions, start=1):
        client = AsyncMock()
        file_accession = f"ENCFF{index:06d}"
        client.call_tool.return_value = {
            "fastq": [{
                "accession": file_accession,
                "file_format": "fastq",
                "file_size": 1_000_000,
                "status": "released",
                "href": f"/files/{file_accession}/@@download/{file_accession}.fastq.gz",
            }]
        }
        mock_clients.append(client)

    calls_by_source = {
        "encode": [
            {"tool": "get_files_by_type", "params": {"accession": accession}}
            for accession in accessions
        ]
    }

    with patch("cortex.tool_dispatch.MCPHttpClient", side_effect=mock_clients), patch(
        "cortex.tool_dispatch.get_service_url", return_value="http://encode:8000"
    ):
        result = await execute_tool_calls(
            calls_by_source,
            user_id="user-1",
            username="user",
            project_id="project-1",
            project_dir_path=None,
            user_message="Download FASTQ files",
            active_skill="download_files",
            needs_approval=False,
            request_id="request-1",
            history_blocks=[],
            is_cancelled=lambda _request_id: False,
            emit_progress=lambda *_args: None,
        )

    assert [client.call_tool.await_count for client in mock_clients] == [1, 1, 1]
    assert [client.call_tool.await_args.kwargs["accession"] for client in mock_clients] == list(accessions)
    assert [entry["params"]["accession"] for entry in result.all_results["encode"]] == list(accessions)
    assert [file["accession"] for file in result.pending_download_files] == [
        "ENCFF000001", "ENCFF000002", "ENCFF000003",
    ]
    assert result.needs_approval is True