from jarvis.tools import steam_tool


def test_launch_steam_game_opens_rungameid_uri(monkeypatch):
    opened = []

    def fake_startfile(uri):
        opened.append(uri)

    monkeypatch.setattr(steam_tool.os, "startfile", fake_startfile, raising=False)

    out = steam_tool.launch_steam_game("app/548430", "Deep Rock Galactic")

    assert opened == ["steam://rungameid/548430"]
    assert "Deep Rock Galactic (548430)" in out


def test_install_steam_game_opens_install_uri(monkeypatch):
    opened = []

    def fake_startfile(uri):
        opened.append(uri)

    monkeypatch.setattr(steam_tool.os, "startfile", fake_startfile, raising=False)

    out = steam_tool.install_steam_game("app/730")

    assert opened == ["steam://install/730"]
    assert "730" in out
