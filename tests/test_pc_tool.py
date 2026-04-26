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


def test_resolve_pc_request_opens_plain_url():
    target = pc_tool.resolve_pc_request("открой www.example.com", SHORTCUTS)

    assert target.url == "https://www.example.com"
    assert target.source == "url"


def test_open_pc_target_uses_browser(monkeypatch):
    opened = []

    def fake_open(url, new=0):
        opened.append((url, new))
        return True

    monkeypatch.setattr(pc_tool.webbrowser, "open", fake_open)

    out = pc_tool.open_pc_target(pc_tool.PcTarget("Example", "https://example.com", "url"))

    assert opened == [("https://example.com", 2)]
    assert "Example" in out
