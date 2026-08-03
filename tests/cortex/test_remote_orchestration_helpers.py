from cortex.remote_orchestration import (
    _extract_remote_execution_request,
    _inject_launchpad_context_params,
)


class TestInjectLaunchpadContextParams:
    def test_rewrites_example_user_and_project_ids_for_defaults_lookup(self):
        params = _inject_launchpad_context_params(
            "get_slurm_defaults",
            {
                "user_id": "user_1234",
                "project_id": "proj_5678",
                "profile_nickname": "hpc3",
            },
            user_id="u-real",
            project_id="proj-real",
        )

        assert params["user_id"] == "u-real"
        assert params["project_id"] == "proj-real"

    def test_preserves_real_scope_ids_when_already_present(self):
        params = _inject_launchpad_context_params(
            "list_ssh_profiles",
            {"user_id": "u-real"},
            user_id="u-real",
            project_id="proj-real",
        )

        assert params["user_id"] == "u-real"
        assert "project_id" not in params

    def test_hydrates_placeholder_scope_tokens(self):
        params = _inject_launchpad_context_params(
            "submit_dogme_job",
            {"user_id": "<user_id>", "project_id": "{project_id}"},
            user_id="u-real",
            project_id="proj-real",
        )

        assert params["user_id"] == "u-real"
        assert params["project_id"] == "proj-real"


class TestExtractRemoteExecutionRequest:
    def test_detects_profile_when_prompt_continues_after_nickname(self):
        request = _extract_remote_execution_request(
            "run dogme rna on hpc3 using staged sample igvfr_698-04 with mm39"
        )

        assert request == {
            "ssh_profile_nickname": "hpc3",
            "stage_only": False,
        }

    def test_detects_profile_before_biological_qualifiers(self):
        request = _extract_remote_execution_request(
            "stage on hpc3 human CDNA fastq sample ENCFF801EBI using /data/ENCFF801EBI.fastq.gz"
        )

        assert request == {
            "ssh_profile_nickname": "hpc3",
            "stage_only": True,
        }