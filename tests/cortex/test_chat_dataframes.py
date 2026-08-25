"""Regression coverage for ENCODE file dataframe assembly."""

from cortex.chat_dataframes import extract_embedded_dataframes


def test_combines_requested_fastq_rows_from_multiple_experiments():
    all_results = {
        "encode": [
            {
                "tool": "get_files_by_type",
                "params": {"accession": "ENCSR432YKA"},
                "data": {
                    "fastq": [{"accession": "ENCFF000001", "file_size": 10, "status": "released"}],
                },
            },
            {
                "tool": "get_files_by_type",
                "params": {"accession": "ENCSR476STT"},
                "data": {
                    "fastq": [{"accession": "ENCFF000002", "file_size": 20, "status": "released"}],
                },
            },
            {
                "tool": "get_files_by_type",
                "params": {"accession": "ENCSR448TJV"},
                "data": {"fastq": []},
            },
        ],
    }

    dataframes = extract_embedded_dataframes(all_results, "Download all released FASTQ files")

    combined = dataframes["Requested FASTQ files (2)"]
    assert combined["columns"][0] == "Experiment"
    assert [row["Experiment"] for row in combined["data"]] == [
        "ENCSR432YKA",
        "ENCSR476STT",
    ]
    assert combined["metadata"]["visible"] is True
    assert dataframes["ENCSR432YKA fastq files (1)"]["metadata"]["visible"] is False