# Dadbot

Dadbot is a single-community Discord engagement bot with durable scheduling and a mildly
corny disposition. It posts YouTube and livestream alerts, rotates engagement content,
manages suggestions and reminders, and lets moderators feature community highlights.

## Features

- YouTube Atom upload monitoring with restart-safe duplicate suppression
- Separate YouTube and Twitch livestream state tracking (`scheduled`, `live`, `ended`)
- Randomized dad jokes every 2–4 days and showcase prompts every 7–12 days
- Weekly questions and Discord-native polls in configurable daytime windows
- Manual lightweight challenges
- Persistent event reminders with any positive minute offsets (defaults: 24h and 1h)
- `/suggest` with durable Support, Accept, and Decline buttons
- Moderator message-context nomination and `/highlight post`
- `/bot status` and protected `/dev` test controls
- Rotating logs, SQLite persistence, graceful shutdown, and safe backups

## Architecture and layout

```text
content/                 JSON engagement pools
data/                    runtime SQLite database and backups (ignored)
deploy/                  sample macOS launchd plist
logs/                    rotating application/launchd logs (ignored)
scripts/run.sh            launch entry point
src/dadbot/
  bot.py                 lifecycle, Discord posting, monitor loops
  config.py              typed environment configuration
  db.py                  deterministic SQLite schema/data access
  content.py             persistent content rotation
  scheduler.py           APScheduler timing and missed-run policy
  services/              YouTube upload and livestream monitors
  cogs/                  commands, components, suggestions, events, highlights
  backup.py/manage.py    local operations
tests/                   isolated automated tests
```

Features share only `Settings`, `Database`, and small typed records. External-service and
scheduled-job failures are caught and logged at their boundaries so Discord remains connected.

## Setup

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m dadbot.manage init-db
python -m dadbot
```

Startup creates the database schema safely if it does not exist. Repeated initialization does
not destroy data. Stop with `Ctrl-C`; the scheduler, HTTP session, database, and Discord client
close cleanly.

## Configuration

`.env` is ignored by Git. Never paste its token or API key into logs or commits.

Required:

- `DISCORD_BOT_TOKEN`: Discord application bot token.
- `DISCORD_GUILD_ID`: target guild ID. For compatibility, `DISCORD_TEST_GUILD_ID` is a fallback.
- `DISCORD_DEFAULT_CHANNEL_ID`: channel for engagement and external notifications. The existing
  `DISCORD_TEST_CHANNEL_ID` is a fallback.

Recommended:

- `DISCORD_YOUTUBE_UPLOAD_CHANNEL_ID`: YouTube upload destination; otherwise uses the default.
- `DISCORD_LIVE_NOTIFICATION_CHANNEL_ID`: YouTube and Twitch live destination; otherwise uses the
  default.
- `DISCORD_SUGGESTION_CHANNEL_ID`, `DISCORD_HIGHLIGHT_CHANNEL_ID`: otherwise use the default.
- `DISCORD_OWNER_IDS`: comma-separated developer user IDs. Discord application owners and
  members with Manage Server also pass `/dev` authorization.
- `BOT_TIMEZONE`: IANA zone, default `America/Indiana/Indianapolis`.
- `DEVELOPMENT_MODE`: enables protected `/dev` commands.
- `DISCORD_SYNC_COMMANDS`: sync commands into the configured guild at startup.

External monitors:

- `YOUTUBE_CHANNEL_ID`: enables quota-free Atom upload checks.
- `YOUTUBE_API_KEY`: additionally enables YouTube Data API livestream checks.
- `YOUTUBE_POLL_MINUTES`, `LIVESTREAM_POLL_MINUTES`: defaults 15 and 5.
- `TWITCH_USER_LOGIN`: Twitch login name without `@`.
- `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`: credentials from a dedicated Twitch developer app.
- `TWITCH_POLL_MINUTES`: live-status check cadence, default 2 minutes.

Scheduling/storage settings and their defaults are documented in [.env.example](.env.example).
Weekdays are Monday `0` through Sunday `6`. Scheduled windows use the configured server timezone.

## Discord application setup

Invite the application with the `bot` and `applications.commands` scopes. The bot needs View
Channels, Send Messages, Embed Links, Read Message History, Create Public Threads if discussions
will use threads, and Send Polls for native weekly polls. No privileged gateway intents are
required. Voice warnings from `discord.py` are harmless; Dadbot has no voice feature.

Commands are guild-synced on startup and normally appear immediately:

- `/bot status`
- `/suggest text:<idea>`
- `/event create|list|cancel`
- `/highlight post`
- Message context menu → **Apps** → **Nominate for highlight**
- `/dev trigger`, `/dev youtube`, `/dev livestream`, `/dev scheduler`

Accept/Decline, event management, and highlight actions enforce moderator permissions on the
server. Hiding a button or command is never the authorization mechanism.

## Scheduling and downtime

Jokes and showcase prompts receive a new randomized daytime run after startup and after each run;
stale executions are not dumped. Weekly jobs may catch up later on their intended day, but not in
a later week. Reminder polling sends a missed reminder only while its event is still upcoming.
External IDs, stream state, announcements, content history, reminders, views, and job metadata are
stored in SQLite, preventing restart duplicates.

Content pools live in `content/*.json`. IDs must be unique within a pool. Dadbot cycles through
least-used entries before favoring repeats. Malformed pools fail startup visibly instead of
silently posting bad content.

## YouTube and livestreams

Uploads use YouTube's Atom feed and announce only the newest unseen item after downtime, avoiding
a backlog flood. Livestream checks use the official YouTube Data API and model state separately;
only a transition to an unannounced live stream posts. An ended stream is never announced as newly
live. Twitch uses an app access token and the official Helix Streams endpoint with the same durable
state/duplicate guarantees. Leaving the relevant variables blank disables that monitor without
affecting other features.

## Development and testing

```sh
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/pytest -q
```

With the dedicated test guild configured and `DEVELOPMENT_MODE=true`, protected developer commands
exercise scheduled posts and external state immediately. A comprehensive one-shot live test is:

```sh
.venv/bin/python -m dadbot.live_smoke
```

It posts real messages, a poll, persistent suggestion UI, a highlight and reminder; simulates
upload/livestream transitions; verifies duplicates; checks registered commands; then disconnects.
It intentionally leaves test artifacts. Run it twice to verify restart persistence.

## Database, logs, and backups

The default database is `data/dadbot.sqlite3`; WAL mode and foreign keys are enabled. Useful local
commands:

```sh
python -m dadbot.manage init-db
python -m dadbot.manage check-db
python -m dadbot.manage backup
python -m dadbot.manage backup --retention 30
```

Backups use SQLite's online backup API and are timestamped under `data/backups/`; retention defaults
to 14. Back up `.env` separately in a secure secret store. To restore while Dadbot is stopped,
retain the current database, place the selected backup at `DATABASE_PATH`, then run `check-db`.

Application logs rotate at 5 MB with five retained files at `logs/dadbot.log`. Logs contain IDs and
operational state but never credentials. Check monitor errors, channel permissions, guild IDs, and
command-sync messages first when troubleshooting.

## macOS launchd deployment

The repository includes [deploy/com.dadmannwalking.dadbot.plist.example](deploy/com.dadmannwalking.dadbot.plist.example).
Replace every `__PROJECT_PATH__` with the absolute checkout path, then copy the resulting plist to
`~/Library/LaunchAgents/com.dadmannwalking.dadbot.plist`. Installing outside the project is an
explicit administrator/user action and is not performed by this project.

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dadmannwalking.dadbot.plist
launchctl kickstart -k gui/$(id -u)/com.dadmannwalking.dadbot
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.dadmannwalking.dadbot.plist
tail -f logs/dadbot.log logs/launchd.err.log
```

The plist runs `scripts/run.sh` from the project directory, starts at login, and restarts after a
crash. The Mac user must be logged in for a LaunchAgent. Use a LaunchDaemon only if operation before
login is genuinely required and after reviewing its different ownership/security requirements.

## Assumptions

This deployment targets one configured guild, one YouTube channel, and one Twitch broadcaster. The
existing test channel is used as the fallback for posting channels. Weekly engagement uses polls
while challenges remain available as manual prompts.
