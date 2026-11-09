# EASYTASK Telegram Bot + Admin Panel

This starter is for legitimate task/proof management only. It does not collect social-media passwords, cookies, session tokens, or facilitate account trading.

## Railway setup
1. Push these files to a GitHub repository and deploy it on Railway.
2. Add a Railway Volume mounted at `/data` so SQLite persists.
3. Add variables:
   - `BOT_TOKEN` = a NEW token generated via @BotFather (revoke the exposed token first)
   - `ADMIN_ID` = `7687242263` (already used as the code default; setting it here is recommended)
   - `PANEL_PASSWORD` = a long unique admin password
   - `SECRET_KEY` = a long random secret
   - `DATABASE_PATH` = `/data/easytask.db`
4. Generate a public domain in Railway.
5. Open `https://YOUR-DOMAIN/login` and sign in with `PANEL_PASSWORD`.
6. Open the Telegram bot and send `/start`. The panel and bot share the same SQLite database.

## Security
- The previously pasted bot token is compromised. Revoke it with @BotFather and use the newly generated token only as Railway's `BOT_TOKEN` variable.
- Never put `BOT_TOKEN` in source code, HTML, GitHub, screenshots, or messages.
- Use only public wallet receive addresses; never request seed phrases/private keys.
- Payments/withdrawals are manual-review placeholders, not automatic blockchain/UPI verification.
- Before public launch, add CSRF protection, rate limiting, backups, and stronger audit controls.
