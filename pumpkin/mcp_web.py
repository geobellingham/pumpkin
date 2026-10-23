"""Built-in MCP server that gives the model web search and page reading, with no API key.

llama-server starts it as `python3 -m pumpkin.mcp_web` and talks JSON-RPC over stdin/stdout.
Search uses DuckDuckGo's HTML page by default. Set PUMPKIN_SEARXNG to a SearXNG URL, or
BRAVE_API_KEY, to use those instead."""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MAX_CHARS = int(os.environ.get("PUMPKIN_PAGE_CHARS", "12000"))

TOOLS = [
    {"name": "web_search", "description": "Search the web. Read the best results with read_webpage before relying on them.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                     "required": ["query"]}},
    {"name": "read_webpage", "description": "Get a web page's text. Use start to continue a long page.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "start": {"type": "integer"}},
                     "required": ["url"]}},
]


def _get(url, headers=None, data=None, timeout=20):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(4_000_000)
        ctype = r.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w-]+)", ctype)
        enc = m.group(1) if m else "utf-8"
        return raw.decode(enc, "replace"), ctype, r.geturl()


def search(query, n=6):
    n = max(1, min(int(n or 6), 10))
    sx = os.environ.get("PUMPKIN_SEARXNG")
    if sx:
        body, _, _ = _get(sx.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"}))
        res = json.loads(body).get("results", [])[:n]
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content", "")} for r in res]
    brave = os.environ.get("BRAVE_API_KEY")
    if brave:
        body, _, _ = _get("https://api.search.brave.com/res/v1/web/search?" +
                          urllib.parse.urlencode({"q": query, "count": n}),
                          {"X-Subscription-Token": brave, "Accept": "application/json"})
        res = json.loads(body).get("web", {}).get("results", [])[:n]
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": _strip(r.get("description", ""))}
                for r in res]
    body, _, _ = _get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)'
                         r'(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|</div>)', body, re.S):
        href = html.unescape(m.group(1))
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0])
        if "duckduckgo.com/y.js" in href:
            continue                                   # ads
        out.append({"title": _strip(m.group(2)), "url": href, "snippet": _strip(m.group(4) or "")})
        if len(out) >= n:
            break
    if not out:
        out = _ddg_lite(query, n)
    return out


def _ddg_lite(query, n):
    """Fallback: the text-only DuckDuckGo page."""
    body, _, _ = _get("https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query}))
    out = []
    links = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*class=[\"']result-link[\"'][^>]*>(.*?)</a>", body, re.S)
    links += re.findall(r"<a[^>]+class=[\"']result-link[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", body, re.S)
    snippets = re.findall(r"class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", body, re.S)
    for i, (href, title) in enumerate(links[:n]):
        href = html.unescape(href)
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": _strip(title), "url": href, "snippet": _strip(snippets[i]) if i < len(snippets) else ""})
    return out


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe", "button"}
    BLOCK = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
             "blockquote", "table", "ul", "ol", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif not self.skip:
            if tag in ("h1", "h2", "h3", "h4"):
                self.out.append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag == "li":
                self.out.append("\n- ")
            elif tag in ("td", "th"):
                self.out.append(" | ")
            elif tag in self.BLOCK:
                self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK and tag != "li" and not self.skip:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r" *\n *", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def read(url, start=0):
    if not re.match(r"https?://", url or ""):
        raise ValueError("Only http and https URLs can be read.")
    body, ctype, final = _get(url)
    if "html" not in ctype and "<html" not in body[:2000].lower():
        text, title = body, ""
    else:
        p = _Text()
        p.feed(body)
        text, title = p.text(), p.title.strip()
    start = max(0, int(start or 0))
    chunk = text[start:start + MAX_CHARS]
    more = len(text) - (start + len(chunk))
    head = f"# {title}\nURL: {final}\n\n" if title else f"URL: {final}\n\n"
    tail = f"\n\n[{more} more characters. Call read_webpage with start={start + len(chunk)} to continue.]" \
        if more > 0 else ""
    return head + chunk + tail


def call(name, args):
    if name == "web_search":
        res = search(args.get("query", ""), args.get("max_results", 6))
        if not res:
            return "No results. Try different words."
        return "\n\n".join(f"{i}. {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(res, 1))
    if name == "read_webpage":
        return read(args.get("url", ""), args.get("start", 0))
    raise ValueError(f"Unknown tool {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue                                         # notifications need no reply
        try:
            if method == "initialize":
                result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "pumpkin-web", "version": "0.1"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params") or {}
                try:
                    text = call(p.get("name"), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
                    result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                raise LookupError(method)
            reply = {"jsonrpc": "2.0", "id": mid, "result": result}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
