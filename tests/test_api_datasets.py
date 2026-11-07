import dataclasses

from decision_diff import api


def _dataset(p, n):
    for side in ("BEFORE_MASTER", "AFTER_BETA"):
        (p / "RESULTS" / side).mkdir(parents=True)
        for i in range(n):
            (p / "RESULTS" / side / f"A{i}.json").write_text("{}")


def test_listing_hides_other_users_uploads_and_skips_response_files(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "settings", dataclasses.replace(api.settings, datasets_dir=tmp_path))
    _dataset(tmp_path / "shared" / "RUN-1", 3)
    _dataset(tmp_path / "uploads" / "u1" / "abc", 2)
    _dataset(tmp_path / "uploads" / "u2" / "def", 1)
    found = api._find_datasets({"user_id": "u1"})
    assert [(d["path"], d["before"], d["after"]) for d in found] == [("shared/RUN-1", 3, 3), ("uploads/u1/abc", 2, 2)]


def test_archive_entry_count_and_total_size_are_capped(tmp_path, monkeypatch):
    import zipfile

    import pytest
    from fastapi import HTTPException

    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for i in range(3):
            f.writestr(f"RESULTS/BEFORE/A{i}.json", "0" * 1000)
    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setattr(api, "MAX_ENTRIES", 2)
    with pytest.raises(HTTPException, match="файлов"):
        api._safe_extract(z, dest)
    monkeypatch.setattr(api, "MAX_ENTRIES", 10)
    monkeypatch.setattr(api, "MAX_UPLOAD", 2500)
    with pytest.raises(HTTPException, match="допустимый размер"):
        api._safe_extract(z, dest)
    monkeypatch.setattr(api, "MAX_UPLOAD", 3000)
    api._safe_extract(z, dest)
    assert len(list((dest / "RESULTS" / "BEFORE").iterdir())) == 3
