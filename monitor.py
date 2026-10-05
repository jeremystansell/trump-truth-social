"""
Watch Donald Trump's Truth Social account, log new posts and send notifications.

New posts are appended to monthly JSON Lines files in data/log/ (one post per
line), which keeps every run's git diff small instead of rewriting the full
15 MB archive. A post counts as "new" when its id is not already in
data/truth_archive.json or any data/log/*.jsonl file.

Sources, tried in order until one works:
  1. cnn    - CNN's public archive, refreshed every ~5 minutes (no key needed)
  2. direct - Truth Social's public API
  3. proxy  - Truth Social's API through ScrapeOps (only if SCRAPE_PROXY_KEY is set)

Notification channels (each is used only when configured):
  - GitHub issue mentioning the repo owner (on by default; triggers GitHub email/app notifications)
  - ntfy push notification   NTFY_TOPIC (+ optional NTFY_SERVER, NTFY_TOKEN)
  - Discord webhook          DISCORD_WEBHOOK_URL
  - Slack webhook            SLACK_WEBHOOK_URL
  - Telegram bot             TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

Exits non-zero if new posts were found but every configured channel failed,
so the workflow skips the commit and the posts are retried on the next run.
"""

import glob
import html
import json
import os
import re
import sys

import requests

ARCHIVE_FILE = "./data/truth_archive.json"
LOG_DIR = "./data/log"
CNN_ARCHIVE_URL = "https://ix.cnn.io/data/truth-social/truth_archive.json"
TRUTH_API_URL = "https://truthsocial.com/api/v1/accounts/107780257626128497/statuses"
SCRAPEOPS_ENDPOINT = "https://proxy.scrapeops.io/v1/"
PROFILE_URL = "https://truthsocial.com/@realDonaldTrump"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

MAX_NOTIFY = int(os.getenv("MAX_NOTIFY") or 10)


def env(name):
    value = os.getenv(name, "").strip()
    return value or None


# ---------------------------------------------------------------- normalizing

def clean_text(raw):
    """Strips HTML tags, turning paragraph/line breaks into newlines."""
    text = re.sub(r"<br\s*/?>|</p>\s*<p>", "\n", raw or "")
    text = re.sub(r"<.*?>", "", text)
    return html.unescape(text).strip()


def normalize(post):
    """Maps a post from any source onto the archive's schema."""
    content = post.get("content") or ""
    if not content and isinstance(post.get("reblog"), dict):
        reblog = post["reblog"]
        author = (reblog.get("account") or {}).get("acct", "")
        content = f"RT @{author}: {reblog.get('content') or ''}"

    media = post.get("media")
    if media is None:
        media = [m.get("url", "") for m in post.get("media_attachments") or []]
    elif isinstance(media, str):
        media = [m.strip() for m in media.split(";") if m.strip()]

    post_id = str(post.get("id"))
    return {
        "id": post_id,
        "created_at": post.get("created_at"),
        "content": clean_text(content),
        "url": post.get("url") or f"{PROFILE_URL}/{post_id}",
        "media": media,
        "replies_count": post.get("replies_count", 0),
        "reblogs_count": post.get("reblogs_count", 0),
        "favourites_count": post.get("favourites_count", 0),
    }


# -------------------------------------------------------------------- sources

def fetch_cnn():
    response = requests.get(CNN_ARCHIVE_URL, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        data = data.get("posts") or data.get("data") or []
    return data


TRUTH_PARAMS = {"exclude_replies": "true", "only_replies": "false", "with_muted": "true", "limit": "40"}


def fetch_direct():
    response = requests.get(
        TRUTH_API_URL,
        params=TRUTH_PARAMS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Referer": PROFILE_URL},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_proxy():
    key = env("SCRAPE_PROXY_KEY")
    if not key:
        raise RuntimeError("SCRAPE_PROXY_KEY not set")
    query = "&".join(f"{k}={v}" for k, v in TRUTH_PARAMS.items())
    response = requests.get(
        SCRAPEOPS_ENDPOINT,
        params={"api_key": key, "url": f"{TRUTH_API_URL}?{query}", "bypass": "cloudflare_level_1"},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


SOURCES = {"cnn": fetch_cnn, "direct": fetch_direct, "proxy": fetch_proxy}


def fetch_latest():
    order = (env("SOURCES") or "cnn,direct,proxy").split(",")
    errors = []
    for name in (s.strip() for s in order):
        if name == "proxy" and not env("SCRAPE_PROXY_KEY"):
            continue
        try:
            posts = SOURCES[name]()
            if not isinstance(posts, list) or not posts:
                raise RuntimeError("returned no posts")
            print(f"Fetched {len(posts)} posts from '{name}'.")
            return [normalize(p) for p in posts if p.get("id")]
        except Exception as e:  # noqa: BLE001 - fall through to the next source
            print(f"Source '{name}' failed: {e}")
            errors.append(f"{name}: {e}")
    raise SystemExit("All sources failed:\n  " + "\n  ".join(errors))


# -------------------------------------------------------------------- storage

def load_seen_ids():
    seen = set()
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, encoding="utf-8") as f:
            seen.update(str(p["id"]) for p in json.load(f))
    for path in glob.glob(os.path.join(LOG_DIR, "*.jsonl")):
        with open(path, encoding="utf-8") as f:
            seen.update(str(json.loads(line)["id"]) for line in f if line.strip())
    return seen


def log_posts(posts):
    """Appends posts (oldest first) to data/log/YYYY-MM.jsonl by post month."""
    os.makedirs(LOG_DIR, exist_ok=True)
    for post in sorted(posts, key=lambda p: p["created_at"] or ""):
        month = (post["created_at"] or "unknown")[:7]
        with open(os.path.join(LOG_DIR, f"{month}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")


# -------------------------------------------------------------- notifications

def describe(post, limit=None):
    text = post["content"] or "(no text)"
    if post["media"]:
        text += f"\n[{len(post['media'])} media attachment(s)]"
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def build_message(posts, bootstrap):
    """Returns (title, plain-text body, newest post) for a batch of new posts."""
    newest = posts[0]
    if bootstrap:
        title = f"Truth Social monitor active ({len(posts)} posts backfilled)"
        body = "Monitoring has started. Latest post:\n\n" + describe(newest, 1000) + f"\n{newest['url']}"
        return title, body, newest

    shown = posts[:MAX_NOTIFY]
    title = "New Trump Truth Social post" if len(posts) == 1 else f"{len(posts)} new Trump Truth Social posts"
    parts = [f"{describe(p, 1000)}\n{p['url']}" for p in shown]
    if len(posts) > len(shown):
        parts.append(f"...and {len(posts) - len(shown)} more (see data/log/).")
    return title, "\n\n---\n\n".join(parts), newest


def send_github_issue(title, posts, bootstrap):
    if (env("NOTIFY_GITHUB_ISSUE") or "true").lower() in ("false", "0", "no", "off"):
        return None
    token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    owner = env("NOTIFY_GITHUB_USER") or repo.split("/")[0]

    shown = posts[:1] if bootstrap else posts[:MAX_NOTIFY]
    sections = []
    for p in shown:
        quoted = "\n".join("> " + line for line in describe(p).splitlines())
        sections.append(f"**{p['created_at']}** · [view on Truth Social]({p['url']})\n\n{quoted}")
    if len(posts) > len(shown):
        sections.append(f"_…and {len(posts) - len(shown)} more, logged in `data/log/`._")
    body = f"@{owner}\n\n" + "\n\n---\n\n".join(sections)

    api = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    response = requests.post(api, headers=headers, json={"title": title, "body": body[:65000]}, timeout=30)
    response.raise_for_status()
    number = response.json()["number"]
    # Close straight away: the notification has already gone out, and this keeps the issue list tidy.
    if (env("GITHUB_ISSUE_KEEP_OPEN") or "false").lower() not in ("true", "1", "yes", "on"):
        requests.patch(f"{api}/{number}", headers=headers, json={"state": "closed"}, timeout=30)
    return f"issue #{number}"


def send_ntfy(title, body, newest):
    topic = env("NTFY_TOPIC")
    if not topic:
        return None
    server = (env("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Authorization": f"Bearer {env('NTFY_TOKEN')}"} if env("NTFY_TOKEN") else {}
    payload = {"topic": topic, "title": title, "message": body[:3900], "click": newest["url"], "tags": ["loudspeaker"]}
    requests.post(server, json=payload, headers=headers, timeout=30).raise_for_status()
    return "ntfy"


def send_discord(title, body, newest):
    url = env("DISCORD_WEBHOOK_URL")
    if not url:
        return None
    requests.post(url, json={"content": f"**{title}**\n\n{body}"[:2000]}, timeout=30).raise_for_status()
    return "discord"


def send_slack(title, body, newest):
    url = env("SLACK_WEBHOOK_URL")
    if not url:
        return None
    requests.post(url, json={"text": f"*{title}*\n\n{body}"[:39000]}, timeout=30).raise_for_status()
    return "slack"


def send_telegram(title, body, newest):
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return None
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": f"{title}\n\n{body}"[:4096], "disable_web_page_preview": True},
        timeout=30,
    ).raise_for_status()
    return "telegram"


def notify(posts, bootstrap):
    title, body, newest = build_message(posts, bootstrap)
    channels = [
        lambda: send_github_issue(title, posts, bootstrap),
        lambda: send_ntfy(title, body, newest),
        lambda: send_discord(title, body, newest),
        lambda: send_slack(title, body, newest),
        lambda: send_telegram(title, body, newest),
    ]
    sent, failed = [], 0
    for send in channels:
        try:
            name = send()
            if name:
                sent.append(name)
        except Exception as e:  # noqa: BLE001 - one failing channel shouldn't block the others
            failed += 1
            print(f"::warning::Notification failed: {e}")
    if sent:
        print(f"Notified via: {', '.join(sent)}")
    elif failed:
        raise SystemExit("Every notification channel failed; not logging posts so they are retried.")
    else:
        print("::warning::No notification channels configured.")


# ----------------------------------------------------------------------- main

def main():
    seen = load_seen_ids()
    bootstrap = not glob.glob(os.path.join(LOG_DIR, "*.jsonl"))
    latest = fetch_latest()

    new_posts = [p for p in latest if p["id"] not in seen]
    new_posts.sort(key=lambda p: p["created_at"] or "", reverse=True)
    if not new_posts:
        print("No new posts.")
        return

    print(f"Found {len(new_posts)} new post(s):")
    for p in new_posts[:MAX_NOTIFY]:
        print(f"  {p['created_at']}  {p['url']}  {describe(p, 120)!r}")

    notify(new_posts, bootstrap)
    log_posts(new_posts)


if __name__ == "__main__":
    sys.exit(main())
