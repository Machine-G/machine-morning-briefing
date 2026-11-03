#!/usr/bin/env python3
"""Machine Morning Briefing v0.9: RSS collector with normalized keyword matching.

Cloudflare keyword authentication and existing static output are preserved.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

import feedparser

ROOT = pathlib.Path(__file__).resolve().parent
JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(JST)
OUT = ROOT / 'morning.html'
USER_AGENT = 'MachineMorningBriefing/0.9 (+RSS reader)'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def keywords():
    """Prefer authenticated cloud settings; never silently fall back on API errors."""
    endpoint = os.environ.get('MMB_KEYWORDS_API', '').strip()
    if endpoint:
        token = os.environ.get('MMB_COLLECTOR_TOKEN', '').strip()
        if not token or not re.fullmatch(r'[a-f0-9]{64}', token):
            raise RuntimeError('MMB_COLLECTOR_TOKEN missing or invalid')
        if not endpoint.startswith('https://'):
            raise RuntimeError('MMB_KEYWORDS_API must use HTTPS')
        request = urllib.request.Request(endpoint, headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/json', 'User-Agent': USER_AGENT,
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read(100_000))
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise RuntimeError('Cloud keyword response is invalid')
        items = data['items']
        print(f'INFO: loaded {len(items)} keyword records from Cloudflare')
    else:
        path = pathlib.Path(os.environ.get('MMB_KEYWORDS_FILE', str(ROOT / 'keywords.json')))
        if not path.exists():
            print('INFO: local test mode: using keywords.example.json')
            path = ROOT / 'keywords.example.json'
        data = read_json(path)
        items = data.get('items', data.get('keywords', [])) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError('Keyword data must be a list')
    result = []
    for item in items:
        if isinstance(item, str):
            item = {'word': item, 'enabled': True}
        if not isinstance(item, dict):
            continue
        term = str(item.get('word', item.get('keyword', item.get('name', item.get('term', ''))))).strip()
        if term and item.get('enabled', item.get('active', True)) is not False:
            result.append({'term': term, 'category': str(item.get('category', 'その他')),
                           'priority': str(item.get('priority', '通常'))})
    return result[:100]


def clean(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', text or '')).strip()


def normalized_words(value):
    """Normalize Unicode, case, and punctuation to comparable word tokens."""
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    return re.findall(r'[^\W_]+', value, flags=re.UNICODE)


def matches_keyword(term, text_tokens):
    """Match exact token sequences or their joined spelling (NAM-A2 vs NAM A2).

    This intentionally does not use fuzzy matching or synonym guessing.
    """
    tokens = normalized_words(term)
    if not tokens:
        return False
    n = len(tokens)
    joined = ''.join(tokens)
    for i in range(len(text_tokens)):
        if text_tokens[i:i + n] == tokens:
            return True
        # Allow punctuation/space variation within a short sequence, while
        # retaining token boundaries so 'NAM' doesn't match 'DYNAMICS'.
        for span in range(1, min(n + 2, 5)):
            if ''.join(text_tokens[i:i + span]) == joined:
                return True
    return False


def article_date(entry):
    for field in ('published_parsed', 'updated_parsed'):
        stamp = entry.get(field)
        if stamp:
            return dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc).astimezone(JST)
    return None


def fetch_feed(feed):
    try:
        request = urllib.request.Request(feed['url'], headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
        })
        with urllib.request.urlopen(request, timeout=18) as response:
            raw = response.read(2_000_000)
        parsed = feedparser.parse(raw)
        if not parsed.entries:
            print(f"WARN: no entries: {feed['name']} ({feed['url']})", file=sys.stderr)
        return parsed.entries[:40]
    except Exception as exc:
        print(f"WARN: feed unavailable: {feed['name']}: {exc}", file=sys.stderr)
        return []


def normalize_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        params = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
                  if not k.lower().startswith('utm_')]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(),
                                       parts.path.rstrip('/'), urllib.parse.urlencode(params), ''))
    except Exception:
        return url


def collect(feeds, terms):
    articles, seen = [], set()
    for feed in feeds:
        entries = fetch_feed(feed)
        eligible = 0
        matched_count = 0
        for entry in entries:
            title = clean(entry.get('title', ''))
            summary = clean(entry.get('summary', entry.get('description', '')))[:450]
            url = entry.get('link', '')
            if not title or not url.startswith(('https://', 'http://')):
                continue
            published = article_date(entry)
            if published and published < NOW - dt.timedelta(days=7):
                continue
            eligible += 1
            normalized = normalize_url(url)
            if normalized in seen:
                continue
            text_tokens = normalized_words(title + ' ' + summary)
            matches = [kw for kw in terms if matches_keyword(kw['term'], text_tokens)]
            if not matches:
                continue
            matched_count += 1
            seen.add(normalized)
            articles.append({'title': title, 'summary': summary, 'url': url,
                             'source': feed['name'], 'category': feed['category'],
                             'published': published.isoformat() if published else None,
                             'matched': [x['term'] for x in matches],
                             'score': max(2 if x['priority'] in ('最重要', '高') else 1
                                          for x in matches)})
        print(f"INFO: feed {feed['name']}: {len(entries)} entries, "
              f"{eligible} within date window/undated, {matched_count} matched")
    articles.sort(key=lambda x: (x['score'], x['published'] or ''), reverse=True)
    print(f'INFO: total matched {len(articles)}; publishing up to 45')
    return articles[:45]


def render(articles, terms):
    esc = html.escape
    cards = []
    for article in articles:
        date = (dt.datetime.fromisoformat(article['published']).strftime('%m/%d %H:%M JST')
                if article['published'] else '公開日時不明')
        url = esc(article['url'], quote=True)
        tags = ''.join(f'<span class="tag">{esc(x)}</span>' for x in article['matched'][:4])
        cards.append(f'''<article class="card"><div class="meta">{esc(article['category'])} · {esc(article['source'])} · {date}</div>
<h2><a href="{url}" target="_blank" rel="noopener noreferrer">{esc(article['title'])}</a></h2>
<p>{esc(article['summary'][:240]) or '概要は元記事で確認してください。'}</p><div class="tags">{tags}</div>
<a class="source" href="{url}" target="_blank" rel="noopener noreferrer">元記事を読む ↗</a></article>''')
    body = '\n'.join(cards) if cards else '<div class="empty">該当する新着記事はありませんでした。RSS取得先やキーワードを見直してください。</div>'
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Machine Morning Briefing | {NOW:%Y-%m-%d}</title>
<style>
:root{{--bg:#0b101a;--panel:#171f2e;--line:#2b364b;--text:#f4f6fb;--muted:#b8c8df;--gold:#e8bc68}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Yu Gothic",Meiryo,sans-serif;line-height:1.75}}
header,main,footer{{max-width:1160px;margin:auto;padding:24px}}header{{padding-top:42px;border-bottom:1px solid var(--line)}}
.eyebrow{{color:var(--gold);font-size:12px;font-weight:800;letter-spacing:.12em}}h1{{font-size:clamp(29px,4vw,54px);line-height:1.2;margin:12px 0}}.sub{{color:var(--muted)}}
.stats{{margin-top:20px;display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;min-width:0}}
.meta{{font-size:12px;color:var(--gold);font-weight:700}}h2{{font-size:21px;line-height:1.5;margin:12px 0}}a{{color:inherit;text-decoration:none}}h2 a:hover,.source:hover{{text-decoration:underline}}
.card p{{color:var(--muted);font-size:15px;overflow-wrap:anywhere}}.tags{{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}}.tag{{font-size:11px;border:1px solid #47546b;padding:3px 9px;border-radius:99px}}
.source{{font-size:13px;color:var(--gold);font-weight:700}}.empty{{padding:30px;border:1px solid var(--line);border-radius:14px;color:var(--muted)}}footer{{color:var(--muted);font-size:12px;border-top:1px solid var(--line)}}
@media(max-width:680px){{header,main,footer{{padding:22px 17px}}.grid{{grid-template-columns:1fr}}.card{{padding:20px}}h2{{font-size:20px}}.card p{{font-size:16px}}}}
</style></head><body><header><div class="eyebrow">YOUR DAILY INTELLIGENCE</div><h1>MACHINE<br>MORNING BRIEFING</h1>
<div class="sub">{NOW:%Y年%m月%d日} · RSS収集版 v0.9</div><div class="stats"><span>関連記事 {len(articles)} 件</span><span>有効キーワード {len(terms)} 件</span><span>更新 {NOW:%H:%M} JST</span></div></header>
<main><p class="sub">公開RSSの見出し・概要を掲載しています。AIによる要約ではありません。掲載情報は元記事で確認してください。</p>
<div class="grid">{body}</div></main><footer>Machine Morning Briefing · RSS prototype · 公開日時不明の記事を含む場合があります。</footer></body></html>'''


def main():
    terms = keywords()
    feeds = read_json(ROOT / 'feeds.json')['feeds']
    articles = collect(feeds, terms)
    OUT.write_text(render(articles, terms), encoding='utf-8')
    (ROOT / 'articles.json').write_text(json.dumps({'generated_at': NOW.isoformat(), 'articles': articles}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Generated {OUT.name}: {len(articles)} articles, {len(terms)} keywords')


if __name__ == '__main__':
    main()
