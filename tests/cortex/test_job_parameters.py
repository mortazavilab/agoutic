"""Tests for reusable DOGME job-parameter contracts."""

from cortex.job_parameters import normalize_dogme_batch_params


class TestNormalizeDogmeBatchParams:
    def test_normalizes_batch_samples_and_shared_parameters(self):
        normalized, errors = normalize_dogme_batch_params({
            "batch_id": "batch-1",
            "batch_samples": [
                {"sample_name": "tumor", "input_directory": " /data/tumor "},
                {"sample_id": "normal-1", "sample_name": "normal", "input_directory": "/data/normal"},
            ],
            "shared_params": {"mode": "DNA", "reference_genome": ["GRCh38"]},
            "requested_max_parallel": "3",
        })

        assert errors == []
        assert normalized == {
            "batch_id": "batch-1",
            "batch_samples": [
                {
                    "sample_id": "1",
                    "sample_name": "tumor",
                    "input_directory": "/data/tumor",
                    "status": "PENDING",
                    "run_uuid": None,
                    "execution_block_id": None,
                    "error": None,
                },
                {
                    "sample_id": "normal-1",
                    "sample_name": "normal",
                    "input_directory": "/data/normal",
                    "status": "PENDING",
                    "run_uuid": None,
                    "execution_block_id": None,
                    "error": None,
                },
            ],
            "shared_params": {"mode": "DNA", "reference_genome": ["GRCh38"]},
            "requested_max_parallel": 3,
            "retry_of_batch_id": None,
        }

    def test_allows_repeated_paths_but_rejects_duplicate_ids(self):
        normalized, errors = normalize_dogme_batch_params({
            "batch_samples": [
                {"sample_id": "repeat-a", "sample_name": "first", "input_directory": "/data/shared"},
                {"sample_id": "repeat-a", "sample_name": "second", "input_directory": "/data/shared"},
            ],
        })

        assert normalized["batch_samples"][1]["input_directory"] == "/data/shared"
        assert errors == ["Batch sample ID 'repeat-a' is duplicated."]

    def test_rejects_missing_samples_and_invalid_parallelism(self):
        normalized, errors = normalize_dogme_batch_params({
            "batch_samples": [{"sample_name": "", "input_directory": ""}],
            "requested_max_parallel": 0,
        })

        assert normalized["requested_max_parallel"] == 0
        assert errors == [
            "Requested batch parallelism must be a positive integer.",
            "Batch sample 1 needs a sample name.",
            "Batch sample 1 needs an input directory.",
        ]