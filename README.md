# Telegram Resume Bot

This bot automates the entire resume intake process. It receives a resume file (PDF or image) via Telegram, uses AI to extract structured data, adds a new row to a Google Sheet, and creates or updates a contact in Constant Contact.

## Features

- **Telegram Integration**: Responds to `/start` and processes uploaded files (PDF, JPG, PNG).
- **AI Data Extraction**: Uses OpenAI Vision to parse resume content and extract key information.
- **Google Sheets**: Automatically appends a new row for each candidate with 20+ fields.
- **Constant Contact**: Creates or updates a contact, mapping standard and custom fields.
- **OAuth2 Ready**: Includes web endpoints to handle the initial token generation for both Google and Constant Contact.
- **Webhook-Based**: Built on Flask and Gunicorn for efficient, scalable deployment.
- **Deploy-Ready**: Includes a `render.yaml` for one-click deployment on [Render](https://render.com/).

---

## Setup & Deployment Guide

Follow these steps to get your bot running in about 15 minutes.

### Step 1: Prerequisites

Before you begin, make sure you have accounts for:

- **Telegram**: To create the bot.
- **OpenAI**: To get an API key for data extraction.
- **Google Cloud**: To create OAuth credentials for Google Sheets.
- **Constant Contact**: To get API credentials.
- **Render**: To host the bot (or any other hosting provider).
- **GitHub**: To host the code for Render to access.

### Step 2: Gather Credentials

You will need to collect the following secrets. It's best to save them in a temporary text file.

1.  **OpenAI API Key**:
    - Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
    - Create a new secret key. Copy it.

2.  **Telegram Bot Token**:
    - Open the [@BotFather](https://t.me/BotFather) chat in Telegram.
    - Send `/newbot` and follow the prompts to create your bot.
    - Copy the **HTTP API token** it gives you.

3.  **Google Sheet ID**:
    - Create a new Google Sheet.
    - The ID is in the URL: `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`
    - Copy the ID.

4.  **Google OAuth Credentials**:
    - Go to the [Google Cloud Console](https://console.cloud.google.com/).
    - Create a new project.
    - Go to **APIs & Services > Enabled APIs & Services** and enable the **Google Sheets API**.
    - Go to **APIs & Services > OAuth consent screen**. Choose **External** and fill in the required app details (app name, user support email, developer contact).
    - Go to **APIs & Services > Credentials**. Click **Create Credentials > OAuth client ID**. Select **Web application**. Add a Redirect URI: `https://your-app-name.onrender.com/google_callback` (you can update this later). Download the JSON file. From this file, you need the `client_id` and `client_secret`.

5.  **Constant Contact Credentials**:
    - Go to the [V3 API Applications](https://app.constantcontact.com/pages/dma/v3-api-applications) page.
    - Create a new application.
    - Note the **Client ID** and generate a **Client Secret**.
    - Add a Redirect URI: `https://your-app-name.onrender.com/cc_callback`.
    - Find your **List ID** by navigating to your contacts list on the Constant Contact website. The ID is in the URL.

### Step 3: Deploy to Render

1.  **Push to GitHub**:
    - Create a new private repository on GitHub.
    - Upload all the files from this project (`bot.py`, `requirements.txt`, `render.yaml`, etc.) to the repository.

2.  **Create Render App**:
    - Go to your [Render Dashboard](https://dashboard.render.com/).
    - Click **New > Blueprint Instance**.
    - Connect your GitHub account and select the repository you just created.
    - Render will automatically detect the `render.yaml` file.
    - Give your service a name (e.g., `telegram-resume-bot`).
    - Under **Environment**, click **Add Secret File** and upload your `client_secret_...json` file from Google, naming it `credentials.json`.
    - Under **Environment**, add all the secret keys you collected in Step 2 as environment variables (e.g., `OPENAI_API_KEY`, `TELEGRAM_TOKEN`, etc.).
    - Click **Apply**.

3.  **Initial Deploy**:
    - Render will start building and deploying your application. This may take a few minutes.
    - Once live, your app will be available at `https://your-app-name.onrender.com`.

### Step 4: Initial OAuth Authentication

This is a one-time setup to generate the initial `token.json` files for Google and Constant Contact.

1.  **Connect Google Sheets**:
    - Open your browser and go to: `https://your-app-name.onrender.com/google_auth`
    - You will be redirected to a Google consent screen. Choose your account and grant access.
    - You will be redirected back to a success page. The bot now has a `token.json` file for Google Sheets.

2.  **Connect Constant Contact**:
    - Open your browser and go to: `https://your-app-name.onrender.com/cc_auth`
    - You will be redirected to the Constant Contact login and consent screen. Grant access.
    - You will be redirected back to a success page. The bot now has a `cc_token.json` file.

### Step 5: Set the Telegram Webhook

Finally, tell Telegram where to send updates.

- Open your browser and go to: `https://your-app-name.onrender.com/set_webhook`
- You should see a success message: `{"ok":true,"result":true,"description":"Webhook was set"}`.

**Your bot is now live!** Open your Telegram bot chat, send `/start`, and upload a resume to test it.
