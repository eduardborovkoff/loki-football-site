# Football Data Pipeline - автономное обновление (T033, S185)

## Цель
Обновлять `stats.db` (matches, odds, fixtures, referee_stats, team_form_ctx)
из открытых источников и складывать в `fb_site_deploy/work/football/`.
БЕЗ ручного участия Эда. Без GitHub-токена в коде.

## Компоненты

### 1. Скрипты ingest
- `fb_site_deploy/football_results_ingest.py` - openfootball raw JSON -> matches (finished)
- `fb_site_deploy/football_odds_ingest.py` - football-data.co.uk CSV -> odds (B365/Avg)
- `tools/football_referee_form_ingest.py` - football-data.co.uk CSV -> referee_stats, team_form_ctx
- `tools/football_fixtures_ingest.py` - football-data.co.uk fixtures.csv -> fixtures (past weekend, нужен openfootball fallback)
- `tools/football_context_ingest.py` - Open-Meteo archive -> match_context (погода)

### 2. Оркестратор
`/data/loki/tools/update_data.py` - CLI:
- `--all` (по умолчанию): ingest results + odds + copy -> deploy
- `--no-ingest`, `--no-odds`, `--no-copy` - отключить шаги
- `--db PATH` - путь к БД

Запуск (одна строка, backslash - продолжение):

    cd /data/loki && FOOTBALL_DB=/data/asgard/work/football/stats.db \\
      /data/loki/.venv/bin/python tools/update_data.py --all

### 3. systemd timer (раз в час)
Файлы в `~/.config/systemd/user/`:
- `loki-football-update.service` - oneshot, запускает `update_data.py --all`
- `loki-football-update.timer` - `OnCalendar=*-*-* *:05:00` (каждый час в :05)

Активация (Эд или S185, ОДИН РАЗ):

    systemctl --user daemon-reload
    systemctl --user enable --now loki-football-update.timer
    systemctl --user list-timers | grep loki-football

Проверка:

    systemctl --user status loki-football-update.timer
    tail -20 /data/loki/logs/football_update.log

### 4. git push на Render (Эд/S185, БЕЗ токена в коде)

**Вариант A (рекомендуется) - SSH-key на сервере:**
1. Один раз: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_render -N ''`
2. Добавить публичный ключ (`~/.ssh/id_ed25519_render.pub`) в GitHub -> Settings -> SSH and GPG keys -> New SSH key.
3. В `fb_site_deploy/.git/config`: `url = git@github.com:<user>/<repo>.git`
4. Проверка: `git -C /data/loki/fb_site_deploy push --dry-run`

**Вариант B - credential helper (токен в ~/.git-credentials, ВНЕ репо):**

    git config --global credential.helper store
    # один раз: git push, ввести токен; сохранится в ~/.git-credentials
    chmod 600 ~/.git-credentials

**Вариант C - ручной push раз в день (самый безопасный):**
Эд/S185 заходит на сервер и делает `git -C /data/loki/fb_site_deploy push`.
Render автоматически обновит сайт (auto-deploy из main).

### 5. Systemd timer для git push (опционально, вариант A/B)
См. отдельную инструкцию в `deploy/README-push.md` (создать по требованию).

## Важные правила
1. `FOOTBALL_DB` env - обязателен при старте fb_server. Иначе CWD=не найден -> SQLite создаст пустую БД (инцидент k1120, 2026-11-10).
2. Никогда не DROP stats.db - только CREATE IF NOT EXISTS / INSERT OR REPLACE.
3. Симлинк `/data/asgard/work/football/stats.db` -> `/data/loki/fb_site_deploy/work/football/stats.db` (уже настроен).
4. Токен НЕ в коде, НЕ в репо. Только `~/.ssh/` или `~/.git-credentials`.
5. Перед активацией timer - ручной прогон `update_data.py --all` и проверка лога.

## Текущий статус (2026-11-10)
- OK: все ingest-скрипты работают (1120 matches, 1015 odds, 30 referees, 2011 form, 2 real cycles)
- OK: update_data.py готов, systemd unit написан
- TODO: ручная активация `systemctl --user enable --now loki-football-update.timer`
- TODO: git push - на Эде/S185 (нет токена в коде)
- BLOCKED: травмы/дисквалификации - FBref/Transfermarkt 403 Cloudflare, нужен Playwright (k1126)
- BLOCKED: fixtures - football-data.co.uk fixtures.csv отдаёт past weekend; openfootball 2026-27 работает без odds (отдельная карточка)
