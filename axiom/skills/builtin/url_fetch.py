"""HTTP GET tool — fetch a URL and return its text content.

Useful when the agent needs to read API documentation, check a website,
or download a plain-text resource.
"""

import urllib.request

from axiom.tools.base import Tool


class UrlFetchTool(Tool):
    name = "url_fetch"
    description = (
        "Fetch a URL and return its text content. "
        "Works with http/https URLs. Returns the first 100 KB of content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch (http/https)",
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str) -> str:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            if len(content) > 100_000:
                content = content[:100_000] + "\n... (truncated at 100 KB)"
            return content
        except Exception as e:
            return f"Error fetching {url}: {e}"


def create_tool() -> Tool:
    return UrlFetchTool()
