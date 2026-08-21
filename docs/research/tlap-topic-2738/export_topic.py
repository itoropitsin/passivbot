#!/usr/bin/env python3
"""Export public posts from TLAP topic 2738 into searchable local context.

The exporter keeps post order, authors, dates, source links, quoted text, and
attachment links. It does not download attachments. Obvious credentials and
personal contact details are redacted before anything is written to disk.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import random
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag


TOPIC_ID = 2738
TOPIC_SLUG = "sovetnik-forex-setka-trader-mod-i-ea-setka"
TOPIC_URL = f"https://tlap.club/topic/{TOPIC_ID}-{TOPIC_SLUG}/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; TLAP-topic-context-export/1.0; "
    "+personal-research; preserves-source-links)"
)
UI_ONLY_LINES = {
    "Показать контент",
    "Скрыть контент",
    "Процитировать",
    "💬 Процитировать",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "passwd",
    "secret",
    "signature",
    "sig",
    "token",
}

SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?P<label>"
    r"инвест(?:орский)?\s+парол(?:ь|я)|парол(?:ь|я)|password|passwd|"
    r"api[\s_-]?key|secret(?:[\s_-]?key)?|access[\s_-]?token|token"
    r")\b(?P<sep>\s*(?:[:=—–-]|\bis\b)\s*)(?P<value>[^\s,;]+)"
)
ACCOUNT_RE = re.compile(
    r"(?i)\b(?P<label>сч[её]т(?:\s*(?:№|#|номер))?)"
    r"(?P<sep>\s*[:=—–-]?\s*)(?P<value>\d{5,})\b"
)
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
IPV4_RE = re.compile(
    r"(?<!\d)(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)"
)
VERSION_RE = re.compile(
    r"(?i)(?:(?:\(?EA\)?\s*[-–—]?\s*Setka|Forex\s+Setka\s+Trader(?:\s+Mod)?|"
    r"\bSetka)\s*(?:version\s*)?v?\s*(\d+(?:\.\d+){1,2})\b|"
    r"\bv(\d+(?:\.\d+){1,2})\b)"
)

THEME_PATTERNS = {
    "Риски, просадка и сохранность депозита": re.compile(
        r"(?i)просад|drawdown|stop\s*out|стоп.?аут|риск|слив|депозит|маржин"
    ),
    "Настройки, сеты и управление капиталом": re.compile(
        r"(?i)\bсет(?:ы|ов|ам|ами|а)?\b|настрой|параметр|finalgridd?ate|"
        r"money.?management|манименеджмент|\bмм\b"
    ),
    "Оптимизация и тестирование": re.compile(
        r"(?i)оптимиз|\bопт\b|бэк.?тест|backtest|тестер|котиров|историческ"
    ),
    "Версии, модификации и исходный код": re.compile(
        r"(?i)верси|релиз|модификац|\bмод\b|исходник|\.mq[45]\b|\.ex[45]\b|"
        r"\bmt[45]\b"
    ),
    "Ордера, лоты и геометрия сетки": re.compile(
        r"(?i)ордер|\bлот|колен|геометри|шаг\s+сет|тейк|стоп.?лосс|трейлинг|"
        r"усреднен|мартин"
    ),
    "Эксплуатация, брокеры и мониторинг": re.compile(
        r"(?i)\bvps\b|брокер|плечо|мониторинг|myfxbook|терминал|\bсервер\b"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for posts.jsonl, indexes, chunks, and manifest.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.20,
        help="Minimum delay in seconds between requests (default: 0.20).",
    )
    parser.add_argument(
        "--pages",
        help="Comma-separated page numbers for a sample export instead of the full topic.",
    )
    parser.add_argument("--resume", action="store_true", help="Resume a sequential full export.")
    parser.add_argument("--max-retries", type=int, default=6)
    return parser.parse_args()


def topic_page_url(page: int) -> str:
    if page == 1:
        return TOPIC_URL
    return f"{TOPIC_URL}page/{page}/"


def normalize_text(raw: str) -> str:
    lines: list[str] = []
    blank_pending = False
    for raw_line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[\t\xa0 ]+", " ", raw_line).strip()
        if line in UI_ONLY_LINES:
            continue
        if not line:
            blank_pending = bool(lines)
            continue
        if blank_pending:
            lines.append("")
            blank_pending = False
        lines.append(line)
    return "\n".join(lines).strip()


def redact_text(text: str) -> tuple[str, list[str]]:
    redactions: list[str] = []

    def redact_secret(match: re.Match[str]) -> str:
        redactions.append("credential")
        return f"{match.group('label')}{match.group('sep')}[REDACTED_CREDENTIAL]"

    def redact_account(match: re.Match[str]) -> str:
        redactions.append("account_number")
        return f"{match.group('label')}{match.group('sep')}[REDACTED_ACCOUNT]"

    text = SECRET_VALUE_RE.sub(redact_secret, text)
    text, email_count = EMAIL_RE.subn("[REDACTED_EMAIL]", text)
    text, ip_count = IPV4_RE.subn("[REDACTED_IP]", text)
    text = ACCOUNT_RE.sub(redact_account, text)
    redactions.extend(["email"] * email_count)
    redactions.extend(["ip_address"] * ip_count)
    return text, redactions


def sanitize_url(raw_url: str) -> tuple[str, list[str]]:
    absolute = urljoin(TOPIC_URL, raw_url.strip())
    if absolute.startswith("mailto:"):
        return "mailto:[REDACTED_EMAIL]", ["email"]
    if absolute.startswith("data:"):
        return "[REDACTED_DATA_URL]", ["embedded_data"]

    parts = urlsplit(absolute)
    redactions: list[str] = []
    hostname = parts.hostname or ""
    netloc = parts.netloc
    if parts.username or parts.password:
        port = f":{parts.port}" if parts.port else ""
        netloc = f"[REDACTED_USERINFO]@{hostname}{port}"
        redactions.append("url_userinfo")

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            query_items.append((key, "[REDACTED]"))
            redactions.append("url_secret")
        else:
            query_items.append((key, value))
    sanitized = urlunsplit(
        (parts.scheme, netloc, parts.path, urlencode(query_items, doseq=True), parts.fragment)
    )
    return sanitized, redactions


def fetch_page(
    session: requests.Session,
    page: int,
    max_retries: int,
) -> str:
    url = topic_page_url(page)
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, timeout=(10, 45))
            response.raise_for_status()
            html = response.text
            if f"topic/{TOPIC_ID}-" not in html or 'id="comment-' not in html:
                raise RuntimeError("response does not contain the expected topic posts")
            return html
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait_seconds = min(30.0, 1.5 * (2 ** (attempt - 1))) + random.random()
            print(
                f"page {page}: attempt {attempt}/{max_retries} failed; "
                f"retrying in {wait_seconds:.1f}s ({exc})",
                flush=True,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"failed to fetch page {page}: {last_error}")


def discover_page_count(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    pages = {1}
    for link in soup.select(f'a[href*="/topic/{TOPIC_ID}-"][href*="/page/"]'):
        match = re.search(r"/page/(\d+)/", link.get("href", ""))
        if match:
            pages.add(int(match.group(1)))
    if max(pages) == 1:
        page_text = soup.get_text(" ", strip=True)
        match = re.search(r"Страница\s+\d+\s+из\s+(\d+)", page_text)
        if match:
            pages.add(int(match.group(1)))
    return max(pages)


def link_kind(link: Tag, url: str) -> str:
    lower_url = url.lower()
    if link.has_attr("download") or "/uploads/" in lower_url or "attachment" in lower_url:
        return "attachment"
    if re.search(r"\.(?:zip|rar|7z|set|mq[45]|ex[45]|pdf|xlsx?|csv)(?:$|[?#])", lower_url):
        return "attachment"
    if link.find("img"):
        return "image_link"
    return "link"


def parse_post(article: Tag, page: int, position_on_page: int) -> dict[str, Any]:
    article_id = article.get("id", "")
    post_id_match = re.fullmatch(r"comment-(\d+)", article_id)
    if not post_id_match:
        raise ValueError(f"unexpected article id: {article_id!r}")
    post_id = int(post_id_match.group(1))

    author_link = article.select_one('aside.post-author a[href*="/profile/"]')
    if author_link is None:
        author_link = article.select_one('aside.post-author a[href*="/profiles/"]')
    author = normalize_text(author_link.get_text(" ", strip=True)) if author_link else "Unknown"
    author, author_redactions = redact_text(author)
    author_profile = None
    if author_link and author_link.get("href"):
        author_profile, _ = sanitize_url(author_link["href"])

    time_tag = article.find("time")
    published_at = time_tag.get("datetime") if time_tag else None
    published_display = normalize_text(time_tag.get_text(" ", strip=True)) if time_tag else None

    number_link = article.select_one(f'a[href="#comment-{post_id}"]')
    post_number = None
    if number_link:
        number_match = re.search(r"(\d+)", number_link.get_text(" ", strip=True))
        if number_match:
            post_number = int(number_match.group(1))

    body = article.select_one(".post-content")
    if body is None:
        raise ValueError(f"post {post_id} has no .post-content element")

    body_copy = BeautifulSoup(str(body), "lxml").select_one(".post-content")
    assert body_copy is not None
    for removable in body_copy.select("script, style, button, noscript"):
        removable.decompose()

    full_text = normalize_text(body_copy.get_text("\n", strip=True))
    full_text, text_redactions = redact_text(full_text)

    quotes: list[str] = []
    for quote in body.select("blockquote"):
        quote_text = normalize_text(quote.get_text("\n", strip=True))
        quote_text, quote_redactions = redact_text(quote_text)
        text_redactions.extend(quote_redactions)
        if quote_text:
            quotes.append(quote_text)

    without_quotes = BeautifulSoup(str(body), "lxml").select_one(".post-content")
    assert without_quotes is not None
    for removable in without_quotes.select("blockquote, script, style, button, noscript"):
        removable.decompose()
    own_text = normalize_text(without_quotes.get_text("\n", strip=True))
    own_text, own_redactions = redact_text(own_text)
    text_redactions.extend(own_redactions)

    links: list[dict[str, str]] = []
    seen_links: set[tuple[str, str]] = set()
    link_redactions: list[str] = []
    for link in body.select("a[href]"):
        href, redactions = sanitize_url(str(link.get("href", "")))
        link_redactions.extend(redactions)
        label = normalize_text(link.get_text(" ", strip=True)) or href
        label, label_redactions = redact_text(label)
        link_redactions.extend(label_redactions)
        key = (label, href)
        if key in seen_links:
            continue
        seen_links.add(key)
        links.append({"text": label, "url": href, "kind": link_kind(link, href)})

    images: list[dict[str, str]] = []
    seen_images: set[str] = set()
    for image in body.select("img"):
        source = image.get("src") or image.get("data-src") or image.get("data-original")
        if not source:
            continue
        image_url, image_redactions = sanitize_url(str(source))
        link_redactions.extend(image_redactions)
        if image_url in seen_images or image_url == "[REDACTED_DATA_URL]":
            continue
        seen_images.add(image_url)
        alt = normalize_text(str(image.get("alt") or ""))
        alt, alt_redactions = redact_text(alt)
        link_redactions.extend(alt_redactions)
        images.append({"alt": alt, "url": image_url})

    source_url = f"{TOPIC_URL}?do=findComment&comment={post_id}"
    redactions = sorted(
        Counter(author_redactions + text_redactions + link_redactions).items()
    )
    return {
        "topic_id": TOPIC_ID,
        "page": page,
        "position_on_page": position_on_page,
        "post_id": post_id,
        "post_number": post_number,
        "author": author,
        "author_profile": author_profile,
        "published_at": published_at,
        "published_display": published_display,
        "source_url": source_url,
        "page_url": topic_page_url(page),
        "text": full_text,
        "own_text": own_text,
        "quotes": quotes,
        "links": links,
        "images": images,
        "redactions": [{"type": kind, "count": count} for kind, count in redactions],
        "content_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
    }


def parse_page(html: str, page: int) -> tuple[str, list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("main h1")
    title = normalize_text(heading.get_text(" ", strip=True)) if heading else "TLAP topic 2738"
    articles = soup.select('article[id^="comment-"]')
    if not articles:
        raise ValueError(f"page {page} contains no posts")
    posts = [parse_post(article, page, index) for index, article in enumerate(articles, 1)]
    post_ids = [post["post_id"] for post in posts]
    if len(post_ids) != len(set(post_ids)):
        raise ValueError(f"page {page} contains duplicate post ids")
    return title, posts


def parse_page_selection(raw: str | None, page_count: int) -> list[int]:
    if raw is None:
        return list(range(1, page_count + 1))
    pages = sorted({int(item.strip()) for item in raw.split(",") if item.strip()})
    if not pages:
        raise ValueError("--pages did not contain any page numbers")
    invalid = [page for page in pages if page < 1 or page > page_count]
    if invalid:
        raise ValueError(f"page numbers outside 1..{page_count}: {invalid}")
    return pages


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                posts.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return posts


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_escape_heading(value: str) -> str:
    return value.replace("\n", " ").replace("#", "№").strip()


def write_chunks(output_dir: Path, posts: list[dict[str, Any]], chunk_pages: int = 50) -> list[Path]:
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for old_chunk in chunks_dir.glob("pages-*.md"):
        old_chunk.unlink()

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        first_page = ((post["page"] - 1) // chunk_pages) * chunk_pages + 1
        last_page = first_page + chunk_pages - 1
        grouped[(first_page, last_page)].append(post)

    written: list[Path] = []
    for (first_page, nominal_last_page), chunk_posts in sorted(grouped.items()):
        actual_last_page = max(post["page"] for post in chunk_posts)
        path = chunks_dir / f"pages-{first_page:04d}-{actual_last_page:04d}.md"
        with path.open("w", encoding="utf-8") as handle:
            handle.write(
                f"# TLAP Setka: страницы {first_page}–{actual_last_page}\n\n"
                f"Источник: [{TOPIC_URL}]({TOPIC_URL})\n\n"
                "Текст сохранён в хронологическом порядке. Очевидные пароли, ключи, "
                "адреса электронной почты, IP-адреса и номера торговых счетов заменены "
                "маркерами `[REDACTED_*]`.\n\n"
            )
            for post in chunk_posts:
                number = post["post_number"] if post["post_number"] is not None else post["post_id"]
                date = post["published_at"] or post["published_display"] or "дата неизвестна"
                author = markdown_escape_heading(post["author"])
                handle.write(f"## Сообщение №{number} — {author} — {date}\n\n")
                handle.write(
                    f"Страница {post['page']} · [Открыть оригинал]({post['source_url']})\n\n"
                )
                handle.write((post["text"] or "[Пустое сообщение]") + "\n\n")
                if post["links"]:
                    handle.write("Ссылки из сообщения:\n\n")
                    for link in post["links"]:
                        label = link["text"].replace("\n", " ") or link["url"]
                        handle.write(f"- {label}: {link['url']}\n")
                    handle.write("\n")
                handle.write("---\n\n")
        written.append(path)
    return written


def write_authors_csv(path: Path, posts: list[dict[str, Any]]) -> None:
    counts = Counter(post["author"] for post in posts)
    profiles: dict[str, str | None] = {}
    first_seen: dict[str, str | None] = {}
    last_seen: dict[str, str | None] = {}
    for post in posts:
        author = post["author"]
        profiles.setdefault(author, post["author_profile"])
        first_seen.setdefault(author, post["published_at"])
        last_seen[author] = post["published_at"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["author", "posts", "first_seen", "last_seen", "profile_url"])
        for author, count in counts.most_common():
            writer.writerow([author, count, first_seen[author], last_seen[author], profiles[author]])


def write_links_csv(path: Path, posts: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["post_number", "post_id", "page", "author", "published_at", "kind", "text", "url"]
        )
        for post in posts:
            for link in post["links"]:
                writer.writerow(
                    [
                        post["post_number"],
                        post["post_id"],
                        post["page"],
                        post["author"],
                        post["published_at"],
                        link["kind"],
                        link["text"],
                        link["url"],
                    ]
                )


def iter_versions(posts: Iterable[dict[str, Any]]) -> Iterable[tuple[str, dict[str, Any]]]:
    for post in posts:
        for match in VERSION_RE.finditer(post["text"]):
            version = match.group(1) or match.group(2)
            if version:
                yield version, post


def write_versions_csv(path: Path, posts: list[dict[str, Any]]) -> Counter[str]:
    rows: list[tuple[str, dict[str, Any]]] = list(iter_versions(posts))
    counts = Counter(version for version, _ in rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["version", "mentions", "first_post", "last_post", "first_date", "last_date"])
        by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for version, post in rows:
            by_version[version].append(post)
        for version, count in counts.most_common():
            version_posts = by_version[version]
            writer.writerow(
                [
                    version,
                    count,
                    version_posts[0]["source_url"],
                    version_posts[-1]["source_url"],
                    version_posts[0]["published_at"],
                    version_posts[-1]["published_at"],
                ]
            )
    return counts


def write_readme(
    output_dir: Path,
    title: str,
    posts: list[dict[str, Any]],
    page_count: int,
    selected_pages: list[int],
    generated_at: str,
    chunks: list[Path],
    version_counts: Counter[str],
) -> None:
    years = Counter(
        post["published_at"][:4]
        for post in posts
        if isinstance(post.get("published_at"), str) and len(post["published_at"]) >= 4
    )
    authors = Counter(post["author"] for post in posts)
    theme_counts = Counter()
    for post in posts:
        for theme, pattern in THEME_PATTERNS.items():
            if pattern.search(post["text"]):
                theme_counts[theme] += 1
    redaction_counts = Counter(
        item["type"]
        for post in posts
        for item in post.get("redactions", [])
        for _ in range(item["count"])
    )

    first_date = next((post["published_at"] for post in posts if post["published_at"]), None)
    last_date = next(
        (post["published_at"] for post in reversed(posts) if post["published_at"]), None
    )
    complete = selected_pages == list(range(1, page_count + 1))
    mode = "полная тема" if complete else "выбранные страницы"

    lines = [
        f"# {title}",
        "",
        f"Источник: [{TOPIC_URL}]({TOPIC_URL})",
        "",
        f"Снимок создан: `{generated_at}`.",
        "",
        "## Покрытие",
        "",
        f"- Режим: {mode}.",
        f"- Страниц в теме на момент выгрузки: {page_count}.",
        f"- Обработано страниц: {len(selected_pages)}.",
        f"- Сохранено сообщений: {len(posts)}.",
        f"- Период сообщений: {first_date or 'неизвестно'} — {last_date or 'неизвестно'}.",
        "- Вложения не скачивались; их названия и ссылки сохранены в записях и индексе ссылок.",
        "- Повторные цитаты сохранены, чтобы не потерять ход разговора.",
        "- Очевидные учётные данные и личные контакты автоматически скрыты.",
        "",
        "## Как пользоваться",
        "",
        "- `posts.jsonl.gz` — сжатое структурированное хранилище, одна запись на сообщение; "
        "его можно распаковать командой `gzip -dk posts.jsonl.gz`.",
        "- `chunks/` — те же сообщения в Markdown, блоками по 50 страниц, удобными для поиска и чтения.",
        "- `indexes/authors.csv` — авторы, число сообщений и период участия.",
        "- `indexes/links.csv` — ссылки и вложения с привязкой к исходному сообщению.",
        "- `indexes/versions.csv` — автоматический указатель упоминаний версий.",
        "- `manifest.json` — техническая проверка полноты и контрольная сумма выгрузки.",
        "",
        "Исходный форум остаётся главным источником: содержание сообщений может редактироваться, "
        "а автоматические индексы могут давать лишние совпадения.",
        "",
        "## Сообщения по годам",
        "",
        "| Год | Сообщений |",
        "|---:|---:|",
    ]
    lines.extend(f"| {year} | {count} |" for year, count in sorted(years.items()))
    lines.extend(["", "## Самые активные участники", "", "| Автор | Сообщений |", "|---|---:|"])
    for author, count in authors.most_common(25):
        safe_author = author.replace("|", "\\|")
        lines.append(f"| {safe_author} | {count} |")
    lines.extend(["", "## Тематическое покрытие", ""])
    lines.append(
        "Числа ниже показывают, в скольких сообщениях встретились слова соответствующей темы; "
        "это указатель для поиска, а не оценка важности."
    )
    lines.extend(["", "| Тема | Сообщений |", "|---|---:|"])
    lines.extend(f"| {theme} | {count} |" for theme, count in theme_counts.most_common())
    lines.extend(["", "## Часто упоминаемые версии", "", "| Версия | Упоминаний |", "|---|---:|"])
    lines.extend(f"| {version} | {count} |" for version, count in version_counts.most_common(25))
    lines.extend(["", "## Markdown-блоки", ""])
    lines.extend(f"- [{path.name}](chunks/{path.name})" for path in chunks)
    lines.extend(["", "## Автоматическое скрытие данных", ""])
    if redaction_counts:
        lines.extend(f"- `{kind}`: {count}" for kind, count in redaction_counts.most_common())
    else:
        lines.append("- Совпадений не найдено.")
    lines.append("")
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gzip_file(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with source.open("rb") as input_handle, temporary.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="posts.jsonl",
            mode="wb",
            fileobj=raw_output,
            compresslevel=9,
            mtime=0,
        ) as output_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(chunk)
    os.replace(temporary, destination)


def validate_posts(posts: list[dict[str, Any]], selected_pages: list[int]) -> dict[str, Any]:
    post_ids = [post["post_id"] for post in posts]
    duplicate_ids = sorted(post_id for post_id, count in Counter(post_ids).items() if count > 1)
    pages_seen = sorted({post["page"] for post in posts})
    missing_pages = sorted(set(selected_pages) - set(pages_seen))
    out_of_order = 0
    last_number: int | None = None
    for post in posts:
        number = post.get("post_number")
        if isinstance(number, int):
            if last_number is not None and number <= last_number:
                out_of_order += 1
            last_number = number
    if duplicate_ids or missing_pages:
        raise ValueError(
            f"validation failed: duplicate_ids={duplicate_ids[:10]}, missing_pages={missing_pages[:10]}"
        )
    return {
        "duplicate_post_ids": len(duplicate_ids),
        "missing_selected_pages": len(missing_pages),
        "posts_without_number": sum(post["post_number"] is None for post in posts),
        "posts_without_date": sum(not post["published_at"] for post in posts),
        "non_increasing_post_numbers": out_of_order,
        "pages_seen": pages_seen,
    }


def export_topic(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "chunks").mkdir(exist_ok=True)
    (output_dir / "indexes").mkdir(exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})

    bootstrap_html = fetch_page(session, 1, args.max_retries)
    page_count = discover_page_count(bootstrap_html)
    selected_pages = parse_page_selection(args.pages, page_count)
    if args.resume and args.pages:
        raise ValueError("--resume is only supported for the full sequential export")

    partial_path = output_dir / "posts.jsonl.partial"
    state_path = output_dir / "export_state.json"
    completed_pages: set[int] = set()
    if args.resume and partial_path.exists() and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed_through = int(state.get("completed_through_page", 0))
        completed_pages = set(range(1, completed_through + 1))
        partial_posts = read_jsonl(partial_path)
        retained = [post for post in partial_posts if post["page"] <= completed_through]
        with partial_path.open("w", encoding="utf-8") as handle:
            for post in retained:
                handle.write(json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n")
        file_mode = "a"
        print(f"resuming after page {completed_through} ({len(retained)} posts retained)", flush=True)
    else:
        file_mode = "w"

    title = "TLAP Setka topic 2738"
    fetched_pages = 0
    fetched_posts = 0
    started_at = datetime.now(timezone.utc).isoformat()
    with partial_path.open(file_mode, encoding="utf-8") as handle:
        for sequence, page in enumerate(selected_pages, 1):
            if page in completed_pages:
                continue
            html = bootstrap_html if page == 1 else fetch_page(session, page, args.max_retries)
            page_title, page_posts = parse_page(html, page)
            title = page_title
            for post in page_posts:
                handle.write(json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fetched_pages += 1
            fetched_posts += len(page_posts)

            if args.pages is None:
                write_json(
                    state_path,
                    {
                        "topic_id": TOPIC_ID,
                        "completed_through_page": page,
                        "page_count_at_start": page_count,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            if sequence == 1 or sequence % 20 == 0 or sequence == len(selected_pages):
                print(
                    f"page {page}/{page_count}: {len(page_posts)} posts "
                    f"({fetched_pages} pages fetched this run)",
                    flush=True,
                )
            if sequence != len(selected_pages):
                time.sleep(max(0.0, args.delay) + random.uniform(0.0, 0.08))

    final_posts_path = output_dir / "posts.jsonl"
    os.replace(partial_path, final_posts_path)
    if state_path.exists():
        state_path.unlink()

    posts = read_jsonl(final_posts_path)
    validation = validate_posts(posts, selected_pages)
    chunks = write_chunks(output_dir, posts)
    write_authors_csv(output_dir / "indexes" / "authors.csv", posts)
    write_links_csv(output_dir / "indexes" / "links.csv", posts)
    version_counts = write_versions_csv(output_dir / "indexes" / "versions.csv", posts)

    generated_at = datetime.now(timezone.utc).isoformat()
    write_readme(
        output_dir,
        title,
        posts,
        page_count,
        selected_pages,
        generated_at,
        chunks,
        version_counts,
    )
    posts_jsonl_sha256 = file_sha256(final_posts_path)
    posts_gzip_path = output_dir / "posts.jsonl.gz"
    gzip_file(final_posts_path, posts_gzip_path)
    posts_jsonl_bytes = final_posts_path.stat().st_size
    final_posts_path.unlink()

    manifest = {
        "schema_version": 1,
        "topic_id": TOPIC_ID,
        "topic_url": TOPIC_URL,
        "topic_title": title,
        "started_at": started_at,
        "generated_at": generated_at,
        "page_count_at_export": page_count,
        "selected_pages": selected_pages,
        "selected_page_count": len(selected_pages),
        "post_count": len(posts),
        "first_post_number": posts[0]["post_number"] if posts else None,
        "last_post_number": posts[-1]["post_number"] if posts else None,
        "first_published_at": posts[0]["published_at"] if posts else None,
        "last_published_at": posts[-1]["published_at"] if posts else None,
        "attachments_downloaded": False,
        "redaction_enabled": True,
        "posts_jsonl_sha256": posts_jsonl_sha256,
        "posts_jsonl_uncompressed_bytes": posts_jsonl_bytes,
        "posts_jsonl_gz_sha256": file_sha256(posts_gzip_path),
        "posts_jsonl_gz_bytes": posts_gzip_path.stat().st_size,
        "validation": validation,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(
        f"complete: {len(posts)} posts from {len(selected_pages)} pages; "
        f"content_sha256={manifest['posts_jsonl_sha256']}",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    export_topic(args)


if __name__ == "__main__":
    main()
