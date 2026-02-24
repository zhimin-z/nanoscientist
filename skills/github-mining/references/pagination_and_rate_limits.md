# Pagination and Rate Limit Handling

Comprehensive guide to paginating through GitHub API results and managing rate limits.

---

## Pagination

### How GitHub Pagination Works

GitHub paginates all list endpoints. The default page size is 30 items; the maximum is 100.

**Key parameters**:
- `per_page` — Number of results per page (1-100, default: 30)
- `page` — Which page to retrieve (default: 1)

### Link Header Navigation

Paginated responses include a `Link` header with URLs for related pages:

```
Link: <https://api.github.com/repos/owner/repo/issues?page=2&per_page=100>; rel="next",
      <https://api.github.com/repos/owner/repo/issues?page=14&per_page=100>; rel="last"
```

**Relationship types**:
| rel value | Meaning |
|-----------|---------|
| `next` | URL of the next page |
| `prev` | URL of the previous page |
| `first` | URL of the first page |
| `last` | URL of the last page |

**Rules**:
- If `rel="next"` is absent, you are on the last page
- `rel="last"` may be absent if total pages cannot be calculated
- Always follow the `Link` header URLs rather than manually constructing page URLs

### Python Pagination Implementation

```python
import requests
import time
import json

def paginated_get(url, token, params=None, max_pages=None):
    """Fetch all pages from a GitHub API endpoint.

    Args:
        url: Full API URL (e.g., https://api.github.com/repos/owner/repo/issues)
        token: GitHub personal access token
        params: Query parameters dict
        max_pages: Optional limit on number of pages to fetch

    Returns:
        List of all items across all pages
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = dict(params or {})
    params["per_page"] = 100
    all_items = []
    page_count = 0

    while url:
        if max_pages and page_count >= max_pages:
            break

        resp = requests.get(url, headers=headers, params=params)
        page_count += 1

        # Handle rate limiting (403 or 429)
        if resp.status_code in (403, 429):
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
            if remaining == 0:
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset_time - time.time(), 1)
                print(f"Rate limited. Waiting {wait:.0f}s until reset...")
                time.sleep(wait + 1)
                continue
            else:
                # Secondary rate limit — exponential backoff
                time.sleep(60)
                continue

        # Handle 202 Accepted (stats being computed)
        if resp.status_code == 202:
            print("Stats being computed, retrying in 3s...")
            time.sleep(3)
            continue

        # Handle 204 No Content
        if resp.status_code == 204:
            break

        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            all_items.extend(data)
            if len(data) == 0:
                break
        else:
            # Some endpoints return objects (e.g., search results)
            if "items" in data:
                all_items.extend(data["items"])
                if len(data["items"]) == 0:
                    break
            else:
                all_items.append(data)

        # Parse Link header for next page
        url = None
        params = {}  # Link header URLs already include query params
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break

    return all_items
```

### Paginating Search Results

Search endpoints have a hard cap of 1,000 results. To collect more:

```python
def paginated_search(query_base, token, date_ranges):
    """Partition search by date ranges to exceed 1,000-result limit.

    Args:
        query_base: Base query string (e.g., "repo:owner/repo is:issue")
        token: GitHub token
        date_ranges: List of (start_date, end_date) tuples as "YYYY-MM-DD"

    Returns:
        All items across all date partitions
    """
    all_items = []
    for start, end in date_ranges:
        query = f"{query_base} created:{start}..{end}"
        url = f"https://api.github.com/search/issues?q={query}"
        items = paginated_get(url, token)
        all_items.extend(items)
        time.sleep(2)  # Respect search rate limits (30/min)
    return all_items
```

---

## Rate Limits

### Primary Rate Limits

| Type | Limit | Scope |
|------|-------|-------|
| Authenticated (token) | 5,000 requests/hour | Core API |
| Unauthenticated | 60 requests/hour | Core API |
| Search (authenticated) | 30 requests/minute | Search endpoints |
| Code search | 10 requests/minute | `/search/code` |

### Checking Current Rate Limit

```bash
# This request does NOT count against your rate limit
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/rate_limit" | python3 -m json.tool
```

Response structure:
```json
{
  "resources": {
    "core": {
      "limit": 5000,
      "remaining": 4987,
      "reset": 1706000000,
      "used": 13
    },
    "search": {
      "limit": 30,
      "remaining": 28,
      "reset": 1706000060,
      "used": 2
    }
  }
}
```

### Response Headers on Every Request

Every API response includes rate-limit headers:
- `X-RateLimit-Limit` — Max requests for this category
- `X-RateLimit-Remaining` — Requests remaining
- `X-RateLimit-Reset` — Unix timestamp when limit resets
- `X-RateLimit-Used` — Requests used in current window
- `X-RateLimit-Resource` — Which category (`core`, `search`, etc.)

### Handling Rate Limit Errors

When you hit the limit, GitHub returns `403 Forbidden` with body:
```json
{"message": "API rate limit exceeded for user ID ..."}
```

**Implementation pattern**:
```python
def wait_for_rate_limit(response):
    """Check response and wait if rate-limited."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
    if remaining == 0 or response.status_code in (403, 429):
        reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
        wait_seconds = max(reset_ts - time.time(), 1)
        print(f"Rate limited. Waiting {wait_seconds:.0f} seconds...")
        time.sleep(wait_seconds + 1)  # +1s buffer
        return True
    return False
```

### Conditional Requests (Save Rate Limit Budget)

Use ETags to avoid counting unchanged responses:

```python
# First request — save the ETag
resp = requests.get(url, headers=headers)
etag = resp.headers.get("ETag")

# Subsequent request — use If-None-Match
headers["If-None-Match"] = etag
resp = requests.get(url, headers=headers)
if resp.status_code == 304:
    # Not modified — use cached data
    # This request does NOT count against rate limit
    pass
```

### Budget Planning

For a typical mining session targeting one large repository:

| Data Type | Estimated Requests | Notes |
|-----------|-------------------|-------|
| Repo metadata | 1 | Single GET |
| File tree | 1 | Single recursive GET |
| Contributors | 1-5 | Depends on contributor count |
| Contributor stats | 1-3 | May need retries for 202 |
| Commits (full history) | N/100 pages | 10,000 commits = 100 requests |
| Issues (all) | N/100 pages | 5,000 issues = 50 requests |
| Issue comments (all) | N/100 per issue | Most expensive — consider sampling |
| Pull requests (all) | N/100 pages | Similar to issues |
| Activity stats | 4 | One per stats endpoint |
| **Typical total** | **200-2,000** | Well within 5,000/hour |

**Warning**: Fetching comments for EVERY issue/PR individually can be very expensive. Consider:
1. Using `GET /repos/{owner}/{repo}/issues/comments` (bulk endpoint) instead
2. Sampling: only fetch comments for a random subset of issues
3. Filtering: only fetch comments for issues matching specific labels
