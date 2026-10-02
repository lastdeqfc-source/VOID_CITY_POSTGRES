# VOID CITY — PostgreSQL build

## Что изменено
- Данные игроков хранятся в PostgreSQL, а не в локальном SQLite.
- Игрок определяется по Telegram ID.
- После выхода и повторного входа прогресс сохраняется.
- Подходит для Render Postgres.
- Реальные деньги, ставки и азартные механики отключены.

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`uvicorn app:app --host 0.0.0.0 --port $PORT`

Environment:
- `BOT_TOKEN` = токен Telegram-бота
- `DATABASE_URL` = Internal Database URL от Render Postgres
- `DEVELOPER` = `@hiddenvoicer`
- `APP_URL` = адрес сервиса Render

После первого запуска таблицы создаются автоматически.
