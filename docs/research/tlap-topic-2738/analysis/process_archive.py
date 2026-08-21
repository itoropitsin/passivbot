#!/usr/bin/env python3
"""Process the complete TLAP topic export for the linked knowledge base.

This is deliberately an analysis pass, not a trading backtest.  It reads every
record from posts.jsonl.gz, validates basic coverage, assigns overlapping search
labels, and writes a reproducible coverage report.  The labels are navigation
helpers; they are not claims about the quality of a post.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "posts.jsonl.gz"
MANIFEST = ROOT / "manifest.json"
OUT_DIR = ROOT / "analysis"

PATTERNS: dict[str, re.Pattern[str]] = {
    "grid_construction": re.compile(
        r"(?i)сетк|колен|шаг|лот|геометр|арифмет|бутылоч|ромб|трапец|"
        r"gridstep|gridlevel|take.?profit|мартин"
    ),
    "multi_grid": re.compile(
        r"(?i)мульт|мульти|корзин|комплект|линейк|коррел|одновалют|"
        r"maxtradepairs|currencyblock|портфел"
    ),
    "testing": re.compile(
        r"(?i)тест|тестер|ретест|backtest|котиров|стейт|стэйт|TDS|"
        r"анализатор|модел|тестир"
    ),
    "history_forward": re.compile(
        r"(?i)истори|онлайн|форвард|forward|демо|реал|сч[её]т|монитор|"
        r"период|режим|сегмент|дата"
    ),
    "risk": re.compile(
        r"(?i)риск|просад|drawdown|стоп.?аут|stop.?out|стоп|слив|"
        r"маржин|депозит|плеч|залог|нагруз|заступ"
    ),
    "optimization": re.compile(
        r"(?i)оптим|\bопт\b|параметр|фильтр|волат|спрэд|spread|"
        r"переопт|робаст|чувствитель"
    ),
    "metrics": re.compile(
        r"(?i)прибыл|доход|рентаб|процент|статист|средн|медиан|"
        r"максим|миним|ожида|частот|количеств|коэффициент"
    ),
    "demo_real": re.compile(
        r"(?i)демо|реал|центов|live|онлайн|исполн|проскальз|"
        r"своп|комисс|брокер|дц|vps|ликвид"
    ),
    "operations": re.compile(
        r"(?i)лог|ошиб|баг|верси|релиз|mt[45]|терминал|сервер|"
        r"order|ордер|monitor|монитор|magic"
    ),
    "crypto": re.compile(
        r"(?i)криптов|битко|bitcoin|эфир|ethereum|фьючерс|futures|"
        r"фандинг|funding|ликвидац|бирж|bybit|binance|okx|usdt|usdc"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_posts() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    with gzip.open(POSTS, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                post = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON at compressed record {line_number}") from exc
            posts.append(post)
    with MANIFEST.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    return posts, manifest


def classify(posts: list[dict[str, Any]]) -> dict[str, Any]:
    year_counts: Counter[str] = Counter()
    author_counts: Counter[str] = Counter()
    theme_counts: Counter[str] = Counter()
    crypto_posts: list[dict[str, Any]] = []
    scored: list[tuple[float, dict[str, Any]]] = []
    post_ids: set[int] = set()
    pages: set[int] = set()
    numbers: list[int] = []
    dates: list[str] = []

    for post in posts:
        number = int(post["post_number"])
        post_ids.add(int(post["post_id"]))
        pages.add(int(post["page"]))
        numbers.append(number)
        dates.append(post["published_at"])
        year_counts[post["published_at"][:4]] += 1
        author_counts[post["author"]] += 1

        text = post.get("text", "")
        themes = [name for name, pattern in PATTERNS.items() if pattern.search(text)]
        for theme in themes:
            theme_counts[theme] += 1
        if "crypto" in themes:
            crypto_posts.append(
                {
                    "post_number": number,
                    "published_at": post["published_at"],
                    "author": post["author"],
                    "source_url": post["source_url"],
                    "characters": len(text),
                    "excerpt": " ".join(text.split())[:420],
                }
            )

        # Long, multi-label posts are useful entry points, but not a substitute
        # for processing the shorter replies around them.
        score = len(text) / 1000 + 1.5 * len(themes)
        if "risk" in themes or "testing" in themes:
            score += 2
        scored.append(
            (
                score,
                {
                    "post_number": number,
                    "published_at": post["published_at"],
                    "author": post["author"],
                    "source_url": post["source_url"],
                    "characters": len(text),
                    "themes": themes,
                    "excerpt": " ".join(text.split())[:420],
                },
            )
        )

    sorted_numbers = sorted(set(numbers))
    missing_numbers = [n for n in range(sorted_numbers[0], sorted_numbers[-1] + 1) if n not in set(numbers)]
    regressions = sum(a >= b for a, b in zip(numbers, numbers[1:]))
    return {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "archive": {
            "posts_file": str(POSTS.relative_to(ROOT)),
            "posts_gz_sha256": sha256(POSTS),
            "manifest_posts_jsonl_gz_sha256": MANIFEST.read_text(encoding="utf-8").split('"posts_jsonl_gz_sha256": "', 1)[1].split('"', 1)[0],
        },
        "coverage": {
            "records_processed": len(posts),
            "unique_post_ids": len(post_ids),
            "pages_seen": len(pages),
            "first_post_number": min(numbers),
            "last_post_number": max(numbers),
            "missing_post_numbers": len(missing_numbers),
            "post_number_regressions": regressions,
            "first_published_at": min(dates),
            "last_published_at": max(dates),
        },
        "year_counts": dict(sorted(year_counts.items())),
        "author_counts": dict(author_counts.most_common(50)),
        "theme_counts": dict(theme_counts.most_common()),
        "crypto_posts": sorted(crypto_posts, key=lambda row: (row["published_at"], row["post_number"])),
        "long_form_entry_points": [
            item for _, item in sorted(scored, key=lambda row: row[0], reverse=True)[:100]
        ],
    }


def write_report(report: dict[str, Any], manifest: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    coverage = report["coverage"]
    lines = [
        "# Покрытие обработки архива TLAP",
        "",
        "Этот отчёт создан проходом по каждой записи `posts.jsonl.gz`. Автоматические темы — навигационный индекс, а не оценка истинности сообщения. Содержательные выводы в базе знаний отделяют наблюдения участников от проверенных расчётов и рабочих гипотез.",
        "",
        "## Проверка полноты",
        "",
        f"- Обработано записей: **{coverage['records_processed']}**.",
        f"- Уникальных идентификаторов сообщений: **{coverage['unique_post_ids']}**.",
        f"- Страниц темы: **{coverage['pages_seen']}**.",
        f"- Номера сообщений: **{coverage['first_post_number']}–{coverage['last_post_number']}**, пропущено номеров: **{coverage['missing_post_numbers']}**.",
        f"- Нарушений порядка в выгрузке: **{coverage['post_number_regressions']}**.",
        f"- Период: **{coverage['first_published_at']} — {coverage['last_published_at']}**.",
        f"- SHA-256 сжатого архива: `{report['archive']['posts_gz_sha256']}`.",
        f"- SHA-256 из манифеста: `{report['archive']['manifest_posts_jsonl_gz_sha256']}`.",
        "",
        "## Как использован весь массив",
        "",
        "1. Каждая строка архива прочитана и проверена как JSON-запись.",
        "2. Каждому сообщению назначены все подходящие тематические метки; одно сообщение может входить в несколько тем.",
        "3. Сформированы годовые и авторские срезы, список крипто-упоминаний и список длинных входных точек для ручного чтения.",
        "4. Страницы базы знаний собраны по повторяющимся мотивам всего периода и ссылаются на ключевые первоисточники. Короткие ответы и возражения остаются в исходном архиве и учитываются через тематические срезы.",
        "",
        "## Тематические метки",
        "",
        "| Тема | Сообщений |",
        "|---|---:|",
    ]
    labels = {
        "grid_construction": "Построение сеток",
        "multi_grid": "Мультисеточная торговля",
        "testing": "Тестирование",
        "history_forward": "История и форвард",
        "risk": "Риски",
        "optimization": "Оптимизация",
        "metrics": "Показатели",
        "demo_real": "Демо и реальный счёт",
        "operations": "Эксплуатация и ошибки",
        "crypto": "Крипто-упоминания",
    }
    for key, count in report["theme_counts"].items():
        lines.append(f"| {labels.get(key, key)} | {count} |")

    lines.extend([
        "",
        "## Сообщения по годам",
        "",
        "| Год | Сообщений |",
        "|---:|---:|",
    ])
    lines.extend(f"| {year} | {count} |" for year, count in report["year_counts"].items())

    lines.extend([
        "",
        "## Отдельный крипто-срез",
        "",
        f"Тематический фильтр нашёл **{len(report['crypto_posts'])}** сообщений с крипто- и биржевыми упоминаниями. Часть совпадений относится к обсуждению новостей, платежей или фьючерсов, а не к сеточной торговле. Поэтому крипто-страницы используют только сообщения, где есть переносимая идея, и отдельно помечают неподтверждённые сведения.",
        "",
        "Список записей находится в `coverage.json`; исходные тексты — в `posts.jsonl.gz` и Markdown-блоках `../chunks/`.",
        "",
        "## Ограничение интерпретации",
        "",
        "Проход по всем сообщениям обеспечивает покрытие и поиск повторяющихся мотивов. Он не превращает форумные заявления в доказанные торговые свойства и не заменяет независимое тестирование на данных и условиях будущего крипто-счёта.",
        "",
    ])
    (OUT_DIR / "coverage.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    posts, manifest = read_posts()
    report = classify(posts)
    if report["coverage"]["records_processed"] != manifest["post_count"]:
        raise SystemExit("processed record count does not match manifest")
    write_report(report, manifest)
    print(json.dumps(report["coverage"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
