from install_traider import DEFAULT_REF, archive_url


def test_installer_downloads_canonical_main_branch_by_default() -> None:
    assert DEFAULT_REF == "main"


def test_archive_url_encodes_ref_special_characters_but_preserves_slashes() -> None:
    assert archive_url("owner/repository", "feature/name #1") == (
        "https://github.com/owner/repository/archive/refs/heads/"
        "feature/name%20%231.zip"
    )
