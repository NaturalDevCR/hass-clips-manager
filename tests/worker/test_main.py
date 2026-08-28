from pathlib import Path

from cinema_collections_worker import main as worker_main


def test_app_main_defaults_to_supervisor_options_json(monkeypatch) -> None:
    loaded: list[Path] = []

    def fake_load(path: Path):
        loaded.append(path)
        raise RuntimeError("stop after resolving options")

    monkeypatch.delenv("CINEMA_COLLECTIONS_OPTIONS", raising=False)
    monkeypatch.setattr(worker_main.Settings, "load", fake_load)

    try:
        worker_main.main()
    except RuntimeError as error:
        assert str(error) == "stop after resolving options"
    else:
        raise AssertionError("startup continued past the options-path seam")

    assert loaded == [Path("/data/options.json")]
