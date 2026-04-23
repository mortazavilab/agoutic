"""Tests for IGVF helper parameter repair functions."""

from cortex.igvf_helpers import _validate_igvf_params


class TestValidateIgvfParams:
    def test_get_dataset_repairs_dataset_id(self):
        params = {"dataset_id": "IGVFDS4764DLLK"}
        result = _validate_igvf_params(
            "get_dataset",
            params,
            "what is the IGVF dataset IGVFDS4764DLLK ?",
        )
        assert result["accession"] == "IGVFDS4764DLLK"
        assert "dataset_id" not in result

    def test_get_dataset_prefers_user_stated_accession(self):
        params = {"accession": "IGVFDS0000AAAA"}
        result = _validate_igvf_params(
            "get_dataset",
            params,
            "what is the IGVF dataset IGVFDS4764DLLK ?",
        )
        assert result["accession"] == "IGVFDS4764DLLK"

    def test_get_files_for_dataset_repairs_dataset_id(self):
        params = {"dataset_id": "IGVFDS3560WHCX", "file_format": "bam"}
        result = _validate_igvf_params(
            "get_files_for_dataset",
            params,
            "what are the bam files for igvf dataset IGVFDS3560WHCX ?",
        )
        assert result["accession"] == "IGVFDS3560WHCX"
        assert result["file_format"] == "bam"
        assert "dataset_id" not in result

    def test_get_file_download_url_repairs_accession_to_file_accession(self):
        params = {"accession": "IGVFFI6571ANCX"}
        result = _validate_igvf_params(
            "get_file_download_url",
            params,
            "download file IGVFFI6571ANCX from igvf",
        )
        assert result["file_accession"] == "IGVFFI6571ANCX"
        assert "accession" not in result

    def test_get_file_download_url_prefers_user_stated_file_accession(self):
        params = {"file_accession": "IGVFFI0000AAAA"}
        result = _validate_igvf_params(
            "get_file_download_url",
            params,
            "download file IGVFFI6571ANCX from igvf",
        )
        assert result["file_accession"] == "IGVFFI6571ANCX"