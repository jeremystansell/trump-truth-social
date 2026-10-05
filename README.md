# Trump Truth Social archive scraper

This repository contains a Python script that scrapes posts from Donald Trump's Truth Social account and stores them in JSON and CSV formats. The scraper runs hourly via a GitHub Actions workflow and updates an S3 archive to keep a historical record of the posts. 

**This workflow was disabled on Oct. 26, 2025, and the data outputs will no longer be updated here.**

**UPDATE:** You can access an archive, updated every five minutes, here: [https://ix.cnn.io/data/truth-social/truth_archive.json](https://ix.cnn.io/data/truth-social/truth_archive.json). (Change `.json` to `.csv` or `.parquet` if you prefer those formats). 

## New-post monitor and notifications (this fork)

`.github/workflows/truth-social-monitor.yml` runs `monitor.py` every 10 minutes. It:

1. Fetches the latest posts (CNN's public archive first, then Truth Social's API directly, then via ScrapeOps if `SCRAPE_PROXY_KEY` is set). No secrets are required.
2. Appends any post not already in `data/truth_archive.json` or `data/log/` to `data/log/YYYY-MM.jsonl` (one JSON post per line, same fields as below) and commits it.
3. Sends a notification. By default it opens (and immediately closes) a GitHub issue that @mentions the repo owner, which triggers GitHub's email / mobile-app notifications. The first run backfills everything since the old archive ended and sends a single "monitor active" notice.

### Setup

- **Enable Actions** on the fork (Actions tab → "I understand my workflows, go ahead and enable them"). Scheduled workflows only run from the default branch.
- **Enable Issues** (Settings → General → Features → Issues) for the default GitHub notification. Forks have Issues turned off by default.
- Optional extra channels, added under Settings → Secrets and variables → Actions → Secrets:
  - `NTFY_TOPIC`: push notifications to your phone via the free [ntfy](https://ntfy.sh) app. Pick a long, hard-to-guess topic name and subscribe to it in the app.
  - `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, or `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- Optional variables (Settings → Secrets and variables → Actions → Variables): `NOTIFY_GITHUB_ISSUE=false` to turn off issue notifications, `GITHUB_ISSUE_KEEP_OPEN=true` to leave issues open, `MAX_NOTIFY` (posts per notification, default 10), `SOURCES` (default `cnn,direct,proxy`).

If every configured channel fails, the run fails (GitHub emails you about failed runs) and the posts are retried on the next run instead of being silently logged.

## How it works

### Fetching data from Truth Social API

The script (`scraper.py`) fetches posts directly from the Truth Social API using a proxy service (`ScrapeOps`) to ensure successful requests.

- **Pagination support:** It requests up to 100 new posts in batches of 20.
- **Media extraction:** Any images or videos in a post are extracted and stored as an array of URLs.
- **Duplicate handling:** Before adding new posts, the script checks an existing archive to avoid duplicates.

## Data output format

The scraper outputs posts in JSON format with the following structure:

```json
[
  {
    "id": "114132050804394743",
    "created_at": "2025-03-09T10:41:28.605Z",
    "content": "Will be interviewed by Maria Bartiromo on Sunday Morning Futures at 10:00amET, enjoy! <span class=\"h-card\"><a href=\"https://truthsocial.com/@FoxNews\" class=\"u-url mention\">@<span>FoxNews</span></a></span>",
    "url": "https://truthsocial.com/@realDonaldTrump/114132050804394743",
    "media": [
      "https://static-assets-1.truthsocial.com/tmtg:prime-ts-assets/media_attachments/files/114/132/050/631/878/172/original/f0e7d14a580b0bc6.mp4"
    ],
    "replies_count": 925,
    "reblogs_count": 2938,
    "favourites_count": 13166
  },
  {
    "id": "114130744626893259",
    "created_at": "2025-03-09T05:09:17.893Z",
    "content": "",
    "url": "https://truthsocial.com/@realDonaldTrump/114130744626893259",
    "media": [
      "https://static-assets-1.truthsocial.com/tmtg:prime-ts-assets/media_attachments/files/114/130/744/449/958/273/original/56b8a2c4e789ede9.jpg"
    ],
    "replies_count": 2451,
    "reblogs_count": 3833,
    "favourites_count": 16848
  },
]
```

### Field descriptions

- **`id`** → The unique identifier for the post
- **`created_at`** → Timestamp when the post was made
- **`content`** → The text content of the post
- **`url`** → Direct link to the post on Truth Social
- **`media`** → An array of image and video URLs if the post contains media
- **`replies_count`** → Number of replies to Trump post
- **`reblogs_count`** → Number of re-posts, or re-truths, to Trump post
- **`favourites_count`** → Number of favorites to Trump post

## GitHub Actions automation

The scraper runs every four hours at 47 minutes past. It's using a GitHub Actions workflow and environment secrets for AWS and ScrapeOps. In addition to fetching the data, the workflow also copies it to a designated S3 bucket. 

*Note: I'm considering strategies now for periodically rehydrating the archive with updated engeagement analytics (re-posts, replies, etc.) so that we capture changes over time for popular posts.*

### Workflow steps

1. Clone the repository
2. Set up Python and install required dependencies
3. Run `scraper.py` to fetch the latest posts
4. Save new posts and update `truth_archive.json`
5. Upload the updated JSON file to an S3 bucket (`stilesdata.com/trump-truth-social-archive/`)
6. Commit and push changes back to GitHub

## Installation and running locally

To run the scraper manually on your machine:

### Install dependencies

```bash
pip install -r requirements.txt
```

### Set environment variables

You'll need to export your ScrapeOps API key and AWS credentials:

```bash
export SCRAPE_PROXY_KEY="your_scrapeops_api_key"
export AWS_ACCESS_KEY_ID="your_aws_key"
export AWS_SECRET_ACCESS_KEY="your_aws_secret"
```

### Run the scraper

```bash
python scraper.py
```

This will fetch new posts and update `truth_archive.json` and `truth_archive.csv`.

## Deployment

The script is fully automated via GitHub Actions. To update or change the workflow:

- Modify `.github/workflows/trump-truth-social-archive.yml`
- Push changes to GitHub
- The workflow will execute on the next scheduled run

## Data storage and access

- Historical posts are stored in an S3 bucket:
  - [`truth_archive.json`](https://stilesdata.com/trump-truth-social-archive/truth_archive.json)
  - [`truth_archive.csv`](https://stilesdata.com/trump-truth-social-archive/truth_archive.csv)

- The latest version of these files is also stored in this repo and updated regularly.

## Notes

This project is for archival and research purposes only. Use responsibly. It is not affiliated with my employer.

### Next steps and improvements:

- Better flags for original posts vs. retweets
- Better handling of media-only posts
- Improve media handling (support for more formats)
- Implement error logging for better monitoring
- Better analytics: Keywords, classification, etc. 
- Consider front-end display or Slack integration to help news teams monitor posts
