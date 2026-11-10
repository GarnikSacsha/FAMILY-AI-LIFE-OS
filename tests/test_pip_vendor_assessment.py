import pytest

from scripts import check_pip_vendor_surface as assessment


def test_changed_pip_version_requires_a_new_assessment(monkeypatch):
    monkeypatch.setattr(assessment.importlib.metadata, "version", lambda name: "26.2")

    with pytest.raises(AssertionError, match="Review VEX"):
        assessment.main()


def test_independent_setuptools_is_not_covered(monkeypatch):
    monkeypatch.setattr(assessment.importlib.metadata, "version", lambda name: "26.3")
    monkeypatch.setattr(assessment.importlib.util, "find_spec", lambda name: object())

    with pytest.raises(AssertionError, match="independent assessment"):
        assessment.main()


def test_package_index_code_invalidates_the_assessment(monkeypatch, tmp_path):
    package = tmp_path / "setuptools"
    package.mkdir()
    (package / "package_index.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(assessment.sysconfig, "get_path", lambda name: str(tmp_path))
    monkeypatch.setattr(assessment.importlib.metadata, "version", lambda name: "26.3")
    monkeypatch.setattr(assessment.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(AssertionError, match="vulnerable PackageIndex"):
        assessment.main()
