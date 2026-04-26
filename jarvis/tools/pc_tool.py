import re
import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus
from urllib.parse import urlparse


COMMAND_WORDS = {
    "включи",
    "включить",
    "открой",
    "открыть",
    "запусти",
    "запустить",
    "покажи",
    "показать",
    "найди",
    "найти",
    "перейди",
    "перейти",
    "на",
    "в",
    "во",
    "сайт",
    "канал",
}
YOUTUBE_WORDS = {"youtube", "ютуб", "ютубе", "ютубчик"}
GOOGLE_WORDS = {"google", "гугл", "гугле"}
URL_RE = re.compile(r"(?P<url>https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)


@dataclass(frozen=True)
class PcTarget:
    name: str
    url: str
    source: str


def normalize_text(text: str) -> str:
    lowered = text.lower().replace("ё", "е")
    cleaned = re.sub(r"[^0-9a-zа-я]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def _safe_url(url: str) -> str:
    url = url.strip()
    if url.startswith("www."):
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only http/https URLs are allowed for /pc")
    return url


def _query_without_noise(text: str, platform_words: set[str]) -> str:
    words = normalize_text(text).split()
    useful = [word for word in words if word not in COMMAND_WORDS and word not in platform_words]
    return " ".join(useful).strip()


def _matches_shortcut(text: str, shortcut: dict) -> tuple[int, str] | None:
    normalized = normalize_text(text)
    candidates = [shortcut.get("name", ""), shortcut.get("slug", ""), *shortcut.get("aliases", [])]
    best_score = 0
    best_alias = ""
    for alias in candidates:
        normalized_alias = normalize_text(str(alias))
        if not normalized_alias:
            continue
        if normalized_alias in normalized:
            score = len(normalized_alias)
            if shortcut.get("kind") != "site":
                score += 100
            if score > best_score:
                best_score = score
                best_alias = normalized_alias
    if best_score == 0:
        return None
    return best_score, best_alias


def resolve_pc_request(text: str, shortcuts: list[dict]) -> PcTarget | None:
    raw = text.strip()
    if not raw:
        return None

    url_match = URL_RE.search(raw)
    if url_match:
        url = _safe_url(url_match.group("url"))
        return PcTarget(name=url, url=url, source="url")

    normalized = normalize_text(raw)
    words = set(normalized.split())

    best_specific = None
    for shortcut in shortcuts:
        if shortcut.get("kind") == "site":
            continue
        match = _matches_shortcut(raw, shortcut)
        if match and (best_specific is None or match[0] > best_specific[0]):
            best_specific = (match[0], shortcut)
    if best_specific:
        shortcut = best_specific[1]
        return PcTarget(name=shortcut["name"], url=_safe_url(shortcut["url"]), source="shortcut")

    if words & YOUTUBE_WORDS:
        query = _query_without_noise(raw, YOUTUBE_WORDS)
        if query:
            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            return PcTarget(name=f"YouTube search: {query}", url=url, source="youtube_search")

    best_site = None
    for shortcut in shortcuts:
        if shortcut.get("kind") != "site":
            continue
        match = _matches_shortcut(raw, shortcut)
        if match and (best_site is None or match[0] > best_site[0]):
            best_site = (match[0], shortcut)
    if best_site:
        shortcut = best_site[1]
        return PcTarget(name=shortcut["name"], url=_safe_url(shortcut["url"]), source="shortcut")

    query = _query_without_noise(raw, GOOGLE_WORDS)
    if query:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        return PcTarget(name=f"Google search: {query}", url=url, source="google_search")
    return None


def open_pc_target(target: PcTarget) -> str:
    if not webbrowser.open(target.url, new=2):
        return f"Не удалось открыть: {target.url}"
    return f"Открываю: {target.name}\n{target.url}"
