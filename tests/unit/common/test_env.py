from pathlib import Path

from common.env import config_directory, load_env_file, require_variable
from common.errors import ConfigurationError


def test_load_env_file_sets_missing_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('ALPHA=1\nQUOTED="two"\nEMPTY=\nBAD LINE\n', encoding="utf-8")
    environ: dict[str, str] = {"ALPHA": "keep"}
    load_env_file(path, environ)
    assert environ["ALPHA"] == "keep"
    assert environ["QUOTED"] == "two"
    assert environ["EMPTY"] == ""


def test_load_env_file_missing_is_noop(tmp_path: Path) -> None:
    load_env_file(tmp_path / "missing.env")


def test_require_variable_rejects_empty() -> None:
    try:
        require_variable({"ALPHA": "  "}, "ALPHA")
    except ConfigurationError as error:
        assert "ALPHA" in str(error)
    else:
        raise AssertionError("expected ConfigurationError")


def test_config_directory_defaults() -> None:
    assert config_directory({}) == Path("config")
    assert config_directory({"CONFIG_DIR": "custom"}) == Path("custom")
