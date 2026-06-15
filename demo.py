#!/usr/bin/env python3
"""Quick demo with three pre-built X research queries."""

from __future__ import annotations

import asyncio

from x_automation_free import XResearchAutomation


async def main() -> None:
    bot = XResearchAutomation()

    queries = [
        "latest reactions to Python 3.13",
        "AI coding tools developer feedback",
        "electric vehicle charging reliability",
    ]

    for query in queries:
        print(f"\n{'=' * 60}")
        print(f"RESEARCHING: {query}")
        print(f"{'=' * 60}")
        result = await bot.research(query)
        print(result.format())
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
