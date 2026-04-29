# Financial Newsletter — Setup Guide

Daily morning briefing delivered to your inbox at 7 AM (Argentina time), Monday–Friday.
Covers S&P 500, Nasdaq, Dow Jones, Gold, Oil, EUR/USD, GBP/USD, USD/JPY with AI analysis.

---

## Step 1 — Get your API keys

### Anthropic (Claude AI)
1. Go to https://console.anthropic.com/
2. Create an account and add a payment method (credit card)
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`)
5. Estimated cost: **< $0.05/month** using Claude Haiku

### Gmail App Password
1. Make sure **2-Step Verification** is enabled on your Google account
2. Go to: https://myaccount.google.com/apppasswords
3. App name: `Newsletter` → Click **Create**
4. Copy the 16-character password shown (e.g. `abcd efgh ijkl mnop`)

---

## Step 2 — Create a GitHub repository

1. Go to https://github.com/new
2. Create a **private** repository (e.g. `financial-newsletter`)
3. Push this folder to the repo:
   ```bash
   git init
   git add newsletter.py requirements.txt .github/
   git commit -m "Initial newsletter setup"
   git remote add origin https://github.com/YOUR_USER/financial-newsletter.git
   git push -u origin main
   ```
   > Do NOT commit `.env` — only commit `.env.example`

---

## Step 3 — Add GitHub Secrets

In your GitHub repo go to **Settings → Secrets and variables → Actions → New repository secret**

Add these 4 secrets:

| Secret name        | Value                          |
|--------------------|-------------------------------|
| `ANTHROPIC_API_KEY` | Your Anthropic key            |
| `GMAIL_USER`        | your.address@gmail.com        |
| `GMAIL_APP_PASSWORD`| The 16-char app password      |
| `RECIPIENT_EMAIL`   | martin@frayleon.com           |

---

## Step 4 — Test it manually

1. Go to your repo on GitHub
2. Click **Actions** → **Daily Financial Newsletter**
3. Click **Run workflow** → **Run workflow**
4. Watch the logs — in ~30 seconds you should receive the email

---

## Step 5 — It runs automatically

GitHub Actions will run the script every weekday at **10:00 UTC = 7:00 AM Argentina time**.

To change the schedule, edit `.github/workflows/newsletter.yml`:
```yaml
- cron: '0 10 * * 1-5'   # minute hour day month weekday
```
Use https://crontab.guru to build custom schedules.

---

## Local testing (optional)

```bash
pip install -r requirements.txt

# Create a .env file with your keys
copy .env.example .env
# Edit .env and fill in your real values

# Run with environment variables loaded
set -a && source .env && set +a   # Linux/Mac
# Or on Windows PowerShell:
# Get-Content .env | ForEach-Object { $k,$v=$_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k,$v) }

python newsletter.py
```

---

## Customization

- **Add tickers**: Edit the `TICKERS` dict in `newsletter.py`
- **Change delivery time**: Edit the cron in `.github/workflows/newsletter.yml`
- **Change model**: Replace `claude-haiku-4-5-20251001` with `claude-sonnet-4-6` for higher quality (costs ~10x more, still < $0.50/month)
- **Add weekends**: Change `1-5` to `*` in the cron expression (note: markets are closed on weekends, data will be Friday's close)
