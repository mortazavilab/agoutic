import ast
import datetime as dt
from pathlib import Path


_APPUI_STATE_PATH = Path(__file__).resolve().parents[2] / "ui" / "appui_state.py"


def _load_function(name: str, extra_globals: dict | None = None):
    source = _APPUI_STATE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_APPUI_STATE_PATH))
    fn_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace: dict = {}
    if extra_globals:
        namespace.update(extra_globals)
    mod = ast.Module(body=[fn_node], type_ignores=[])
    exec(compile(mod, filename=str(_APPUI_STATE_PATH), mode="exec"), namespace)
    return namespace[name]


class TestLocalHelpIntent:
    def test_recognizes_explicit_local_help_shortcut(self):
        fn = _load_function("_is_help_intent")

        assert fn("show local help") is True

    def test_does_not_steal_bare_help_prompt(self):
        fn = _load_function("_is_help_intent")

        assert fn("help") is False

    def test_does_not_steal_prompt_coach_request(self):
        fn = _load_function("_is_help_intent")

        assert fn("how do i compare reconcile samples") is False


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.markdowns: list[str] = []

    def container(self, **_kwargs):
        return _FakeContext()

    def expander(self, *_args, **_kwargs):
        return _FakeContext()

    def markdown(self, text):
        self.markdowns.append(str(text))

    def divider(self):
        return None


class TestLocalHelpResponse:
    def test_local_help_mentions_haplotype_prompting(self):
        fake_st = _FakeStreamlit()
        fn = _load_function(
            "_render_local_help_response",
            {
                "st": fake_st,
                "section_header": lambda *_args, **_kwargs: None,
                "metadata_row": lambda *_args, **_kwargs: None,
            },
        )

        fn()

        rendered = "\n".join(fake_st.markdowns)
        assert "/help /haplotype" in rendered
        assert "/haplotype <DNA|RNA|cDNA> <workflow> [vcf]" in rendered
        assert "haplotype mouse workflow7 without typing the founder VCF path" in rendered


class TestShareIntent:
    def test_recognizes_share_this_project_request(self):
        fn = _load_function("_is_share_intent")

        assert fn("share this project with someone") is True

    def test_recognizes_add_collaborator_request(self):
        fn = _load_function("_is_share_intent")

        assert fn("add a collaborator") is True

    def test_recognizes_grant_access_request(self):
        fn = _load_function("_is_share_intent")

        assert fn("give alice access to project y") is True

    def test_does_not_match_generic_help(self):
        fn = _load_function("_is_share_intent")

        assert fn("what can you do") is False


class TestListUsersIntent:
    def test_recognizes_list_users_shortcut(self):
        fn = _load_function("_is_list_users_intent")

        assert fn("list users") is True

    def test_recognizes_who_is_in_this_project(self):
        fn = _load_function("_is_list_users_intent")

        assert fn("who is in this project?") is True

    def test_does_not_match_share_intent(self):
        fn = _load_function("_is_list_users_intent")

        assert fn("share this project") is False


class TestProjectRoleHelpers:
    def test_owner_membership_label(self):
        fn = _load_function("_project_membership_label")

        assert fn({"role": "owner"}) == "Owned by me"

    def test_viewer_membership_label(self):
        fn = _load_function("_project_membership_label")

        assert fn({"role": "viewer"}) == "Shared with me · Viewer"

    def test_editor_can_mutate_project(self):
        fn = _load_function("_project_can_mutate")

        assert fn({"role": "editor"}, {"role": "user"}) is True

    def test_viewer_cannot_mutate_project(self):
        fn = _load_function("_project_can_mutate")

        assert fn({"role": "viewer"}, {"role": "user"}) is False

    def test_admin_can_manage_collaborators(self):
        fn = _load_function("_project_can_manage_collaborators")

        assert fn({"role": "viewer"}, {"role": "admin"}) is True

    def test_editor_cannot_manage_collaborators(self):
        fn = _load_function("_project_can_manage_collaborators")

        assert fn({"role": "editor"}, {"role": "user"}) is False


class TestCollaboratorActivityHelpers:
    def test_collaborator_activity_status_marks_recent_user_active(self):
        fn = _load_function("_collaborator_activity_status")
        now = dt.datetime(2026, 5, 27, 21, 30, tzinfo=dt.timezone.utc)

        status, label = fn({"last_accessed": "2026-05-27T21:27:00Z"}, now)

        assert status == "active"
        assert label == "Active now"

    def test_collaborator_activity_status_marks_same_day_idle(self):
        fn = _load_function("_collaborator_activity_status")
        now = dt.datetime(2026, 5, 27, 21, 30, tzinfo=dt.timezone.utc)

        status, label = fn({"last_accessed": "2026-05-27T09:00:00Z"}, now)

        assert status == "idle"
        assert label == "Active today"

    def test_shared_project_activity_warning_mentions_other_active_collaborators(self):
        fn = _load_function("_shared_project_activity_warning")
        now = dt.datetime(2026, 5, 27, 21, 30, tzinfo=dt.timezone.utc)

        warning = fn(
            [
                {"user_id": "me", "email": "me@example.com", "last_accessed": "2026-05-27T21:29:00Z"},
                {"user_id": "u-2", "email": "editor@example.com", "last_accessed": "2026-05-27T21:28:30Z"},
            ],
            "me",
            True,
            now,
        )

        assert "editor@example.com" in warning
        assert "overwrite each other" in warning

    def test_shared_project_activity_warning_ignores_inactive_collaborators(self):
        fn = _load_function("_shared_project_activity_warning")
        now = dt.datetime(2026, 5, 27, 21, 30, tzinfo=dt.timezone.utc)

        warning = fn(
            [
                {"user_id": "u-2", "email": "viewer@example.com", "last_accessed": "2026-05-27T20:00:00Z"},
            ],
            "me",
            True,
            now,
        )

        assert warning is None