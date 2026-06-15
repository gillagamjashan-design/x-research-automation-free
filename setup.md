# X Research Automation Setup ($0)

This project uses Twikit to search X without X API v2 credentials. It does not
use OpenAI, paid APIs, subscriptions, or required cloud LLM services.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verify the install:

```bash
python3 -c "import twikit; print(f'twikit: {getattr(twikit, \"__version__\", \"installed\")}')"
python3 -m py_compile x_automation_free.py demo.py
python3 x_automation_free.py --help
```

## Most Reliable Login: Export X Cookies

X usually requires a logged-in session for search. Cookies are the most reliable
free method.

1. Log in to `https://x.com` in Chrome or Edge.
2. Open DevTools with `Ctrl+Shift+I`.
3. Go to `Application -> Cookies -> https://x.com`.
4. Copy the cookies and convert them to JSON with a local or browser-based
   cookie export workflow. One simple option is a cookies-to-JSON converter.
5. Save the result as `cookies.json` in this project directory.
6. Run:

```bash
python3 x_automation_free.py "AI coding tools developer feedback" --cookies cookies.json
```

Keep `cookies.json` private. It can grant access to your X session.

## Alternative: Twikit Direct Login

Twikit can also log in and save cookies automatically. This is free and uses no
X API key.

```bash
export X_USERNAME='your_email@example.com'
export X_PASSWORD='your_password'
# Optional if X asks for a second identifier:
export X_EMAIL='your_email@example.com'

python3 x_automation_free.py "latest reactions to Python 3.13"
```

If login succeeds, the script saves `cookies.json` for later runs.

## Run Research Queries

```bash
python3 x_automation_free.py "latest reactions to Python 3.13"
python3 x_automation_free.py "AI coding tools developer feedback" --limit 10
python3 x_automation_free.py "electric vehicle charging reliability" --min-likes 1 --min-replies 0
```

Run the demo:

```bash
python3 demo.py
```

## Optional Local LLM Summary

The default summarizer is local keyword/statistical processing, so no LLM is
required. If you already run Ollama locally, you can use a local model:

```bash
pip install ollama
ollama pull phi3
python3 x_automation_free.py "AI coding tools developer feedback" --ollama-model phi3
```

This still costs $0 because it runs on your machine.

## Troubleshooting

- `No cookies found`: add `--cookies cookies.json` or set `X_USERNAME` and
  `X_PASSWORD`.
- `X login failed`: verify credentials, handle any X security checkpoint in your
  browser, then export cookies instead.
- `Rate limited`: wait before retrying, lower `--limit`, or use authenticated
  cookies.
- `No tweets returned`: broaden the query or lower quality thresholds with
  `--min-likes 0 --min-replies 0`.

## $0 Guarantee

This tool does not require:

- X API v2 credentials
- OpenAI API keys
- paid LLM APIs
- subscriptions
- paid scraping services

Twikit depends on X's web/internal behavior, so availability can change if X
changes its site or anti-scraping controls.
