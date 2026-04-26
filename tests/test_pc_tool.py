from jarvis.tools import pc_tool


SHORTCUTS = [
    {
        "slug": "paradeevich-youtube",
        "name": "Парадеевич на YouTube",
        "url": "https://www.youtube.com/@paradeevich",
        "aliases": ["парадеевич", "парадеевича", "парадеевича на ютубе"],
        "kind": "creator",
    },
    {
        "slug": "youtube",
        "name": "YouTube",
        "url": "https://www.youtube.com/",
        "aliases": ["youtube", "ютуб", "ютубе"],
        "kind": "site",
    },
    {
        "slug": "valorant",
        "name": "VALORANT",
        "url": "app://valorant",
        "aliases": ["valorant", "валорант", "валик"],
        "kind": "app",
    },
    {
        "slug": "ayugram",
        "name": "AyuGram",
        "url": "app://ayugram",
        "aliases": ["ayugram", "аюграм"],
        "kind": "app",
    },
    {
        "slug": "yandex-music",
        "name": "Yandex Music",
        "url": "app://yandex-music",
        "aliases": ["яндекс музыка", "яндекс музыку"],
        "kind": "app",
    },
]


def test_resolve_pc_request_prefers_creator_shortcut():
    target = pc_tool.resolve_pc_request("включи парадеевича на ютубе", SHORTCUTS)

    assert target.name == "Парадеевич на YouTube"
    assert target.url == "https://www.youtube.com/@paradeevich"
    assert target.source == "shortcut"


def test_resolve_pc_request_searches_youtube_when_query_is_not_shortcut():
    target = pc_tool.resolve_pc_request("найди python на ютубе", SHORTCUTS)

    assert target.name == "YouTube search: python"
    assert target.url == "https://www.youtube.com/results?search_query=python"
    assert target.source == "youtube_search"


def test_resolve_pc_request_opens_site_shortcut():
    target = pc_tool.resolve_pc_request("открой ютуб", SHORTCUTS)

    assert target.name == "YouTube"
    assert target.url == "https://www.youtube.com/"


def test_resolve_pc_request_opens_app_shortcut():
    target = pc_tool.resolve_pc_request("запусти валик", SHORTCUTS)

    assert target.name == "VALORANT"
    assert target.url == "app://valorant"
    assert target.kind == "app"
    assert target.slug == "valorant"


def test_resolve_pc_request_opens_plain_url():
    target = pc_tool.resolve_pc_request("открой www.example.com", SHORTCUTS)

    assert target.url == "https://www.example.com"
    assert target.source == "url"


def test_resolve_pc_request_can_disable_google_fallback():
    target = pc_tool.resolve_pc_request("составь план проекта", SHORTCUTS, allow_fallback_search=False)

    assert target is None


def test_open_pc_target_uses_browser(monkeypatch):
    opened = []

    def fake_open(url, new=0):
        opened.append((url, new))
        return True

    monkeypatch.setattr(pc_tool.webbrowser, "open", fake_open)

    out = pc_tool.open_pc_target(pc_tool.PcTarget("Example", "https://example.com", "url"))

    assert opened == [("https://example.com", 2)]
    assert "Example" in out


def test_open_pc_target_uses_configured_app_path(monkeypatch):
    opened = []

    def fake_startfile(path):
        opened.append(path)

    monkeypatch.setenv("PC_APP_AYUGRAM_PATH", r"C:\Apps\AyuGram\AyuGram.exe")
    monkeypatch.setattr(pc_tool.os, "startfile", fake_startfile, raising=False)

    out = pc_tool.open_pc_target(pc_tool.PcTarget("AyuGram", "app://ayugram", "shortcut", "app", "ayugram"))

    assert opened == [r"C:\Apps\AyuGram\AyuGram.exe"]
    assert "AyuGram" in out


def test_open_pc_target_falls_back_to_web_for_yandex_music(monkeypatch):
    opened = []

    def fake_open(url, new=0):
        opened.append((url, new))
        return True

    monkeypatch.delenv("PC_APP_YANDEX_MUSIC_PATH", raising=False)
    monkeypatch.setattr(pc_tool.webbrowser, "open", fake_open)

    out = pc_tool.open_pc_target(
        pc_tool.PcTarget("Yandex Music", "app://yandex-music", "shortcut", "app", "yandex-music")
    )

    assert opened == [("https://music.yandex.ru/home", 2)]
    assert "браузере" in out


def test_open_pc_target_asks_for_path_when_app_has_no_fallback(monkeypatch):
    monkeypatch.delenv("PC_APP_AYUGRAM_PATH", raising=False)

    out = pc_tool.open_pc_target(pc_tool.PcTarget("AyuGram", "app://ayugram", "shortcut", "app", "ayugram"))

    assert "PC_APP_AYUGRAM_PATH" in out
