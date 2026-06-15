#!/usr/bin/env python3
"""$0 X research automation using Twikit.

This tool does not use paid APIs, X API v2 credentials, OpenAI, or any
subscription services. It searches X with Twikit and summarizes locally by
default. Optional Ollama support is local-only and imported lazily.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from tenacity import (
        AsyncRetrying,
        before_sleep_log,
        retry_if_exception,
        stop_after_attempt,
        wait_fixed,
    )
except ImportError:  # pragma: no cover - fallback for fresh environments.
    AsyncRetrying = None
    before_sleep_log = None
    retry_if_exception = None
    stop_after_attempt = None
    wait_fixed = None

try:
    from twikit import Client
except ImportError:  # pragma: no cover - exercised before dependencies install.
    Client = None  # type: ignore[assignment]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


DEFAULT_COOKIES_PATH = "cookies.json"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_LIMIT = 10
STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "being",
    "from",
    "have",
    "into",
    "just",
    "like",
    "more",
    "over",
    "people",
    "that",
    "their",
    "there",
    "they",
    "this",
    "those",
    "through",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "your",
}


class ErrorSeverity(str, Enum):
    """Severity levels for actionable user-facing errors."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "no_cookies": {
        "severity": ErrorSeverity.WARNING.value,
        "message": "No cookies or login environment variables found. Search may be limited.",
        "fix": "Run with --cookies cookies.json or set X_USERNAME/X_PASSWORD.",
    },
    "rate_limited": {
        "severity": ErrorSeverity.WARNING.value,
        "message": "X rate limited the request. Retrying with a delay.",
        "fix": "Wait before running more searches, lower --limit, or use authenticated cookies.",
    },
    "login_failed": {
        "severity": ErrorSeverity.CRITICAL.value,
        "message": "X login failed.",
        "fix": "Verify X_USERNAME, X_EMAIL if needed, and X_PASSWORD, or use --cookies cookies.json.",
    },
    "cookies_failed": {
        "severity": ErrorSeverity.CRITICAL.value,
        "message": "Cookie loading failed.",
        "fix": "Use convert_cookies.py or provide a JSON object like {\"auth_token\": \"...\"}.",
    },
    "auth_required": {
        "severity": ErrorSeverity.CRITICAL.value,
        "message": "X blocked the search because authentication is required.",
        "fix": "Provide valid cookies with --cookies, convert Chrome cookies, or set X_USERNAME/X_PASSWORD.",
    },
    "search_failed": {
        "severity": ErrorSeverity.CRITICAL.value,
        "message": "X search failed.",
        "fix": "Check cookies, wait if rate limited, and verify Twikit is current.",
    },
    "empty_results": {
        "severity": ErrorSeverity.INFO.value,
        "message": "No tweets were returned for this query.",
        "fix": "Try a broader query or reduce quality thresholds.",
    },
}


@dataclass(slots=True)
class ToolError:
    code: str
    severity: ErrorSeverity
    message: str
    fix: str
    detail: str | None = None

    @classmethod
    def from_code(cls, code: str, detail: str | None = None) -> "ToolError":
        data = ERROR_MESSAGES.get(
            code,
            {
                "severity": ErrorSeverity.WARNING.value,
                "message": "Unexpected issue.",
                "fix": "Review logs and retry.",
            },
        )
        return cls(
            code=code,
            severity=ErrorSeverity(data["severity"]),
            message=data["message"],
            fix=data["fix"],
            detail=detail,
        )


@dataclass(slots=True)
class TweetContext:
    text: str
    author: str = "unknown"
    timestamp: str = "unknown"
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    url: str | None = None

    @property
    def engagement(self) -> int:
        return self.likes + self.replies + self.retweets


@dataclass(slots=True)
class ResearchResult:
    query: str
    synthesized_answer: str
    tweets: list[TweetContext] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)

    def format(self) -> str:
        lines = [
            f"Research query: {self.query}",
            "",
            "Synthesized answer:",
            self.synthesized_answer or "No summary available.",
        ]

        if self.errors:
            lines.extend(["", "Notices:"])
            for error in self.errors:
                detail = f" Detail: {error.detail}" if error.detail else ""
                lines.append(
                    f"- [{error.severity.value}] {error.message} Fix: {error.fix}{detail}"
                )

        lines.extend(["", "Top tweets:"])
        if not self.tweets:
            lines.append("- No tweets available.")
        for index, tweet in enumerate(self.tweets, start=1):
            lines.append(
                f"{index}. @{tweet.author} | {tweet.timestamp} | "
                f"likes={tweet.likes} replies={tweet.replies} retweets={tweet.retweets}"
            )
            lines.append(f"   {tweet.text}")
            if tweet.url:
                lines.append(f"   {tweet.url}")

        return "\n".join(lines)


class XResearchAutomation:
    """Search X via Twikit and synthesize findings with $0 dependencies."""

    def __init__(
        self,
        cookies_path: str | None = None,
        language: str = DEFAULT_LANGUAGE,
        max_tweets: int = DEFAULT_LIMIT,
        min_likes: int = 3,
        min_replies: int = 1,
        ollama_model: str | None = None,
    ) -> None:
        if Client is None:
            raise RuntimeError(
                "twikit is not installed. Install dependencies with: "
                "pip install -r requirements.txt"
            )

        self.client = Client(language)
        self.cookies_path_provided = cookies_path is not None
        self.cookies_path = Path(cookies_path) if cookies_path else Path(DEFAULT_COOKIES_PATH)
        self.language = language
        self.max_tweets = max_tweets
        self.min_likes = min_likes
        self.min_replies = min_replies
        self.ollama_model = ollama_model
        self.errors: list[ToolError] = []
        self._initialized = False
        logger.info("Cookie path set to: %s", self.cookies_path)
        logger.info("Cookie absolute path: %s", self.cookies_path.absolute())
        logger.info("Cookie file exists: %s", self.cookies_path.exists())

    async def initialize(self) -> None:
        """Load cookies or perform free Twikit login if credentials are present."""
        if self._initialized:
            return

        logger.info("Checking cookies path argument: %s", self.cookies_path)
        logger.info("Checking cookies absolute path: %s", self.cookies_path.absolute())
        logger.info("Checking cookies file exists: %s", self.cookies_path.exists())

        if self.cookies_path.exists():
            logger.info("Loading cookies from %s", self.cookies_path)
            try:
                cookies = load_twikit_cookies(self.cookies_path)
                logger.info("Final cookies dict has %s entries", len(cookies))
                logger.info(
                    "First cookie key: %s",
                    next(iter(cookies.keys()), "NONE"),
                )
                self.client.set_cookies(cookies)
                logger.info("Cookies loaded successfully")
            except Exception as exc:  # noqa: BLE001 - cookie formats vary.
                logger.error("Cookie loading failed: %s", exc)
                self.errors.append(ToolError.from_code("cookies_failed", str(exc)))
            self._initialized = True
            return

        if self.cookies_path_provided:
            logger.error("Cookies file not found: %s", self.cookies_path)
            logger.error("Absolute path checked: %s", self.cookies_path.absolute())
            logger.warning(
                "Fix: check the file path or use an absolute path like --cookies %s",
                self.cookies_path.absolute(),
            )

        username = os.getenv("X_USERNAME")
        email = os.getenv("X_EMAIL")
        password = os.getenv("X_PASSWORD")

        if username and password:
            logger.info("No cookies found. Attempting Twikit login for %s", username)
            try:
                await self.client.login(
                    auth_info_1=username,
                    auth_info_2=email,
                    password=password,
                )
                self.client.save_cookies(str(self.cookies_path))
                logger.info("Saved cookies to %s", self.cookies_path)
            except Exception as exc:  # noqa: BLE001 - Twikit exceptions vary by version.
                logger.error("Login failed: %s", exc)
                self.errors.append(ToolError.from_code("login_failed", str(exc)))
            finally:
                self._initialized = True
            return

        logger.warning(ERROR_MESSAGES["no_cookies"]["message"])
        logger.warning(
            "X search usually requires authentication. Fix: use --cookies /full/path/to/cookies.json, "
            "set X_USERNAME/X_PASSWORD, or run convert_cookies.py for Chrome cookies."
        )
        self.errors.append(ToolError.from_code("no_cookies"))
        self._initialized = True

    async def research(self, query: str) -> ResearchResult:
        """Search X for a query and return a synthesized result."""
        self.errors = []
        await self.initialize()

        logger.info("Searching X for: %s", query)
        try:
            raw_tweets = await self.search_tweets_with_retry(query, self.max_tweets)
        except Exception as exc:  # noqa: BLE001 - Twikit exceptions vary by version.
            logger.error("Search failed: %s", exc)
            if _looks_like_auth_required(exc):
                logger.error(
                    "This usually means X blocked unauthenticated search. Fix: provide valid cookies "
                    "with --cookies, convert Chrome cookies with convert_cookies.py, or set "
                    "X_USERNAME/X_PASSWORD."
                )
                error_code = "auth_required"
            else:
                error_code = "rate_limited" if _looks_like_rate_limit(exc) else "search_failed"
            self.errors.append(ToolError.from_code(error_code, str(exc)))
            return ResearchResult(
                query=query,
                synthesized_answer=(
                    "X search requires authentication. Provide valid cookies or set "
                    "X_USERNAME/X_PASSWORD."
                    if error_code == "auth_required"
                    else "Search failed before tweets could be collected."
                ),
                tweets=[],
                errors=list(self.errors),
            )

        tweets = [self._extract_tweet_context(tweet) for tweet in _as_list(raw_tweets)]
        tweets = [tweet for tweet in tweets if tweet.text.strip()]
        tweets = _deduplicate_tweets(tweets)
        filtered = self.filter_quality_tweets(tweets, self.min_likes, self.min_replies)
        selected = filtered if filtered else tweets
        selected = sorted(
            selected,
            key=lambda tweet: (tweet.engagement, _query_overlap(query, tweet.text)),
            reverse=True,
        )[: self.max_tweets]

        if not selected:
            self.errors.append(ToolError.from_code("empty_results"))

        synthesized = await self._synthesize(query, selected)
        return ResearchResult(
            query=query,
            synthesized_answer=synthesized,
            tweets=selected,
            errors=list(self.errors),
        )

    async def search_tweets_with_retry(self, query: str, num_results: int) -> Any:
        """Search with automatic retry on likely rate limits or transient failures."""
        if AsyncRetrying and retry_if_exception and stop_after_attempt and wait_fixed:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_looks_like_retryable_error),
                wait=wait_fixed(60),
                stop=stop_after_attempt(3),
                before_sleep=before_sleep_log(logger, logging.WARNING)
                if before_sleep_log
                else None,
                reraise=True,
            ):
                with attempt:
                    return await self._search_tweets(query, num_results)

        last_error: BaseException | None = None
        for attempt in range(1, 4):
            try:
                return await self._search_tweets(query, num_results)
            except Exception as exc:  # noqa: BLE001 - Twikit exceptions vary.
                last_error = exc
                if attempt == 3 or not _looks_like_retryable_error(exc):
                    raise
                logger.warning("Retryable search error, waiting 60s: %s", exc)
                await asyncio.sleep(60)

        raise RuntimeError(f"Search retry failed: {last_error}")

    async def _search_tweets(self, query: str, num_results: int) -> Any:
        search_tweets = getattr(self.client, "search_tweets", None)
        if search_tweets is not None:
            return await search_tweets(query, num_results=num_results)

        search_tweet = getattr(self.client, "search_tweet", None)
        if search_tweet is not None:
            return await search_tweet(query, product="Top", count=num_results)

        raise AttributeError(
            "Installed Twikit client has neither search_tweets nor search_tweet."
        )

    @staticmethod
    def filter_quality_tweets(
        tweets: list[TweetContext],
        min_likes: int = 3,
        min_replies: int = 1,
    ) -> list[TweetContext]:
        """Filter out spam-like and low-engagement tweets."""
        filtered = []
        for tweet in tweets:
            if len(tweet.text) > 500:
                continue
            if tweet.text.count("@") > 3:
                continue
            if _looks_like_spam(tweet.text):
                continue
            if tweet.likes < min_likes and tweet.replies < min_replies:
                continue
            filtered.append(tweet)
        return filtered

    def _extract_tweet_context(self, tweet: Any) -> TweetContext:
        text = _first_present(tweet, "text", "full_text", "content") or ""
        author_obj = _first_present(tweet, "user", "author")
        author = (
            _first_present(author_obj, "screen_name", "username", "name")
            if author_obj is not None
            else None
        )
        timestamp = _first_present(
            tweet,
            "created_at",
            "created_at_datetime",
            "date",
            "timestamp",
        )
        tweet_id = _first_present(tweet, "id", "tweet_id")
        likes = _metric(tweet, "likes", "favorite_count", "like_count")
        replies = _metric(tweet, "replies", "reply_count")
        retweets = _metric(tweet, "retweets", "retweet_count")
        url = _first_present(tweet, "url")
        if not url and tweet_id:
            username = author or "i"
            url = f"https://x.com/{username}/status/{tweet_id}"

        return TweetContext(
            text=_clean_text(str(text)),
            author=str(author or "unknown"),
            timestamp=str(timestamp or "unknown"),
            likes=likes,
            replies=replies,
            retweets=retweets,
            url=str(url) if url else None,
        )

    async def _synthesize(self, query: str, tweets: list[TweetContext]) -> str:
        if not tweets:
            return "No usable tweets were found for this query."

        if self.ollama_model:
            ollama_answer = await self._synthesize_with_ollama(query, tweets)
            if ollama_answer:
                return ollama_answer

        return _synthesize_locally(query, tweets)

    async def _synthesize_with_ollama(
        self,
        query: str,
        tweets: list[TweetContext],
    ) -> str | None:
        try:
            import ollama  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("Ollama package is not installed. Falling back to local summary.")
            return None

        prompt_tweets = "\n".join(
            f"- @{tweet.author}: {tweet.text}" for tweet in tweets[: self.max_tweets]
        )
        prompt = (
            "Synthesize the X posts below into a concise research answer. "
            "Do not invent facts beyond the posts. Include uncertainty when the posts disagree.\n\n"
            f"Query: {query}\n\nTweets:\n{prompt_tweets}"
        )

        try:
            response = await asyncio.to_thread(
                ollama.chat,
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001 - optional dependency failure.
            logger.warning("Ollama synthesis failed: %s", exc)
            return None


def _as_list(raw_tweets: Any) -> list[Any]:
    if raw_tweets is None:
        return []
    if isinstance(raw_tweets, list):
        return raw_tweets
    if isinstance(raw_tweets, tuple):
        return list(raw_tweets)
    if hasattr(raw_tweets, "__iter__") and not isinstance(raw_tweets, (str, bytes, dict)):
        return list(raw_tweets)
    return [raw_tweets]


def load_twikit_cookies(cookies_path: Path) -> dict[str, str]:
    """Load Chrome-exported or Twikit-format cookies as a Twikit cookie dict."""
    with cookies_path.open("r", encoding="utf-8") as cookie_file:
        cookies_raw = json.load(cookie_file)

    if isinstance(cookies_raw, list):
        cookies = _chrome_cookies_to_dict(cookies_raw)
        logger.info("Converted %s cookies from Chrome format", len(cookies))
        return cookies

    if isinstance(cookies_raw, dict):
        cookies = {
            str(name): str(value)
            for name, value in cookies_raw.items()
            if name and value is not None
        }
        logger.info("Loaded %s cookies in Twikit format", len(cookies))
        return cookies

    raise ValueError(
        f"Invalid cookies format: expected list or dict, got {type(cookies_raw).__name__}"
    )


def _chrome_cookies_to_dict(cookies_raw: list[Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in cookies_raw:
        if (
            isinstance(cookie, dict)
            and "name" in cookie
            and "value" in cookie
            and cookie["name"]
            and cookie["value"] is not None
        ):
            cookies[str(cookie["name"])] = str(cookie["value"])

    if not cookies:
        raise ValueError("Chrome cookie list did not contain any name/value pairs")

    return cookies


def _first_present(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    for name in names:
        if isinstance(obj, dict) and name in obj:
            value = obj[name]
        else:
            value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _metric(tweet: Any, *names: str) -> int:
    metrics = _first_present(tweet, "public_metrics", "metrics")
    for name in names:
        value = _first_present(tweet, name)
        if value is None and metrics is not None:
            value = _first_present(metrics, name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_'-]{2,}", text.lower())
        if token not in STOP_WORDS and not token.startswith("http")
    ]


def _query_overlap(query: str, text: str) -> int:
    query_terms = set(_tokenize(query))
    text_terms = set(_tokenize(text))
    return len(query_terms & text_terms)


def _deduplicate_tweets(tweets: list[TweetContext]) -> list[TweetContext]:
    seen: set[str] = set()
    unique = []
    for tweet in tweets:
        normalized = re.sub(r"https?://\S+", "", tweet.text.lower())
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(tweet)
    return unique


def _looks_like_spam(text: str) -> bool:
    lowered = text.lower()
    spam_phrases = (
        "airdrop",
        "giveaway",
        "limited offer",
        "click here",
        "dm me",
        "100x",
    )
    if any(phrase in lowered for phrase in spam_phrases):
        return True
    urls = len(re.findall(r"https?://", lowered))
    hashtags = lowered.count("#")
    return urls > 3 or hashtags > 8


def _looks_like_rate_limit(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "rate" in text and "limit" in text or "429" in text or "too many" in text


def _looks_like_auth_required(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    auth_markers = (
        "key_byte",
        "couldn't get",
        "could not get",
        "unauthorized",
        "authentication",
        "forbidden",
        "login",
    )
    return any(marker in text for marker in auth_markers)


def _looks_like_retryable_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    retry_markers = (
        "rate limit",
        "429",
        "too many",
        "timeout",
        "temporarily",
        "connection",
        "503",
        "502",
    )
    return any(marker in text for marker in retry_markers)


def _synthesize_locally(query: str, tweets: list[TweetContext]) -> str:
    all_tokens = []
    for tweet in tweets:
        all_tokens.extend(_tokenize(tweet.text))

    common = [word for word, _ in Counter(all_tokens).most_common(8)]
    highest_engagement = max(tweets, key=lambda tweet: tweet.engagement)
    total_engagement = sum(tweet.engagement for tweet in tweets)

    answer = [
        f"Based on {len(tweets)} relevant X posts about '{query}', the discussion centers on "
        f"{', '.join(common[:5]) if common else 'the queried topic'}.",
        f"The strongest signal came from @{highest_engagement.author}, whose post had "
        f"{highest_engagement.engagement} combined likes, replies, and retweets.",
    ]

    if total_engagement:
        answer.append(
            f"Across the returned posts, total visible engagement was {total_engagement}. "
            "Treat this as directional social signal, not a verified factual source."
        )
    else:
        answer.append(
            "The returned posts had little or no visible engagement, so confidence is limited."
        )

    representative = sorted(tweets, key=lambda tweet: tweet.engagement, reverse=True)[:3]
    answer.append("Representative points:")
    for tweet in representative:
        answer.append(f"- @{tweet.author}: {_shorten(tweet.text, 220)}")

    return "\n".join(answer)


def _shorten(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="$0 X research automation using Twikit scraping.",
    )
    parser.add_argument("query", nargs="?", help="Research query to search for on X.")
    parser.add_argument("--limit", type=_positive_int, default=DEFAULT_LIMIT)
    parser.add_argument("--cookies")
    parser.add_argument("--no-summary", action="store_true")
    parser.add_argument("--min-likes", type=int, default=3)
    parser.add_argument("--min-replies", type=int, default=1)
    parser.add_argument(
        "--ollama-model",
        help="Optional local Ollama model name, for example phi3 or llama3.",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if not args.query:
        build_parser().print_help()
        return 2

    if args.cookies:
        cookies_file = Path(args.cookies)
        if not cookies_file.exists():
            logger.error("Cookies file not found: %s", args.cookies)
            logger.error("Absolute path: %s", cookies_file.absolute())
            logger.error("Fix: check the file path and use an absolute path if needed.")
            logger.error(
                "Example: python3 x_automation_free.py 'query' --cookies %s",
                cookies_file.absolute(),
            )
            return 1
        logger.info("Cookies file found: %s", cookies_file.absolute())

    bot = XResearchAutomation(
        cookies_path=args.cookies,
        max_tweets=args.limit,
        min_likes=args.min_likes,
        min_replies=args.min_replies,
        ollama_model=args.ollama_model,
    )
    result = await bot.research(args.query)
    if args.no_summary:
        result.synthesized_answer = "Summary disabled by --no-summary."
    print(result.format())
    return 1 if any(error.severity == ErrorSeverity.CRITICAL for error in result.errors) else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
