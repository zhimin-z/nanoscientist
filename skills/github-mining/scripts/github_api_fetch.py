#!/usr/bin/env python3
"""GitHub API data collection utility for empirical research.

Usage:
    python github_api_fetch.py --repo owner/repo --output data/ --collect all
    python github_api_fetch.py --repo owner/repo --output data/ --collect commits issues prs
    python github_api_fetch.py --repo owner/repo --output data/ --collect commits --since 2024-01-01
    python github_api_fetch.py --repo owner/repo --output data/ --collect stats contributors tree

Requires GITHUB_TOKEN environment variable for authenticated access (5,000 req/hour).
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install requests")
    sys.exit(1)

# --- Configuration ---
BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 100


def get_headers(token: str) -> dict:
    """Build request headers with auth and API version."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def rate_limit_wait(response: requests.Response) -> bool:
    """Check if rate-limited and wait if needed. Returns True if should retry."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
    if response.status_code in (403, 429) or remaining == 0:
        reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
        wait = max(reset_ts - time.time(), 1)
        print(f"  Rate limited. Waiting {wait:.0f}s until reset...")
        time.sleep(wait + 1)
        return True
    return False


def paginated_get(url: str, headers: dict, params: dict = None,
                  max_pages: int = None, delay: float = 0.1) -> list:
    """Fetch all pages from a GitHub API endpoint.

    Args:
        url: Full API URL
        headers: Request headers (with auth)
        params: Query parameters
        max_pages: Optional page limit
        delay: Seconds between requests (politeness)

    Returns:
        List of all items across all pages
    """
    params = dict(params or {})
    params["per_page"] = PER_PAGE
    all_items = []
    page_count = 0

    while url:
        if max_pages and page_count >= max_pages:
            break

        resp = requests.get(url, headers=headers, params=params)
        page_count += 1

        # Handle rate limiting
        if rate_limit_wait(resp):
            continue

        # Handle 202 Accepted (stats being computed)
        if resp.status_code == 202:
            print("  Stats being computed, retrying in 3s...")
            time.sleep(3)
            continue

        # Handle 204 No Content
        if resp.status_code == 204:
            break

        if resp.status_code != 200:
            print(f"  Warning: HTTP {resp.status_code} for {url}")
            print(f"  Response: {resp.text[:200]}")
            break

        data = resp.json()

        if isinstance(data, list):
            if len(data) == 0:
                break
            all_items.extend(data)
            print(f"  Page {page_count}: {len(data)} items (total: {len(all_items)})")
        elif isinstance(data, dict) and "items" in data:
            # Search endpoint format
            all_items.extend(data["items"])
            print(f"  Page {page_count}: {len(data['items'])} items (total: {len(all_items)})")
            if len(data["items"]) == 0:
                break
        else:
            all_items.append(data)

        # Parse Link header for next page
        url = None
        params = {}  # Link URLs include params already
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break

        if url:
            time.sleep(delay)

    return all_items


def save_json(data, filepath: Path):
    """Save data as JSON with pretty formatting."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {filepath} ({len(data) if isinstance(data, list) else 1} items)")


def fetch_repo_metadata(owner: str, repo: str, headers: dict, output_dir: Path):
    """Fetch repository metadata."""
    print("\n[1/7] Fetching repository metadata...")
    url = f"{BASE_URL}/repos/{owner}/{repo}"
    resp = requests.get(url, headers=headers)
    if rate_limit_wait(resp):
        resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    save_json(data, output_dir / "repo_metadata.json")
    return data


def fetch_file_tree(owner: str, repo: str, headers: dict, output_dir: Path,
                    branch: str = "main"):
    """Fetch complete recursive file tree."""
    print("\n[2/7] Fetching file tree...")
    url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    resp = requests.get(url, headers=headers)
    if rate_limit_wait(resp):
        resp = requests.get(url, headers=headers)

    if resp.status_code == 404:
        # Try 'master' branch if 'main' not found
        print(f"  Branch '{branch}' not found, trying 'master'...")
        url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/master?recursive=1"
        resp = requests.get(url, headers=headers)

    resp.raise_for_status()
    data = resp.json()

    if data.get("truncated"):
        print("  WARNING: Tree is truncated (>100,000 entries). Some files may be missing.")

    tree_entries = data.get("tree", [])
    save_json(tree_entries, output_dir / "file_tree.json")
    print(f"  Total entries: {len(tree_entries)}, truncated: {data.get('truncated', False)}")
    return tree_entries


def fetch_contributors(owner: str, repo: str, headers: dict, output_dir: Path):
    """Fetch contributor list and stats."""
    print("\n[3/7] Fetching contributors...")

    # Basic contributor list
    url = f"{BASE_URL}/repos/{owner}/{repo}/contributors"
    contributors = paginated_get(url, headers)
    save_json(contributors, output_dir / "contributors.json")

    # Detailed contributor stats (weekly data)
    print("  Fetching detailed contributor stats...")
    url = f"{BASE_URL}/repos/{owner}/{repo}/stats/contributors"
    stats = paginated_get(url, headers)
    save_json(stats, output_dir / "contributor_stats.json")

    return contributors


def fetch_commits(owner: str, repo: str, headers: dict, output_dir: Path,
                  since: str = None, until: str = None):
    """Fetch commit history."""
    print("\n[4/7] Fetching commits...")
    url = f"{BASE_URL}/repos/{owner}/{repo}/commits"
    params = {}
    if since:
        params["since"] = f"{since}T00:00:00Z"
    if until:
        params["until"] = f"{until}T23:59:59Z"

    commits = paginated_get(url, headers, params=params)
    save_json(commits, output_dir / "commits.json")
    return commits


def fetch_issues(owner: str, repo: str, headers: dict, output_dir: Path,
                 fetch_comments: bool = False, max_comment_issues: int = 500):
    """Fetch all issues (excluding PRs)."""
    print("\n[5/7] Fetching issues...")
    url = f"{BASE_URL}/repos/{owner}/{repo}/issues"
    params = {"state": "all", "sort": "created", "direction": "asc"}

    all_items = paginated_get(url, headers, params=params)

    # Separate issues from PRs
    issues = [i for i in all_items if "pull_request" not in i]
    prs_from_issues = [i for i in all_items if "pull_request" in i]
    print(f"  Pure issues: {len(issues)}, PRs (filtered out): {len(prs_from_issues)}")

    save_json(issues, output_dir / "issues.json")

    # Optionally fetch comments for issues
    if fetch_comments and issues:
        print(f"  Fetching comments for up to {max_comment_issues} issues...")
        comments_dir = output_dir / "issue_comments"
        comments_dir.mkdir(parents=True, exist_ok=True)

        for i, issue in enumerate(issues[:max_comment_issues]):
            if issue.get("comments", 0) == 0:
                continue
            comment_url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue['number']}/comments"
            comments = paginated_get(comment_url, headers, delay=0.2)
            if comments:
                save_json(comments, comments_dir / f"issue_{issue['number']}.json")
            if (i + 1) % 50 == 0:
                print(f"    Progress: {i + 1}/{min(len(issues), max_comment_issues)} issues")

    return issues


def fetch_pull_requests(owner: str, repo: str, headers: dict, output_dir: Path):
    """Fetch all pull requests."""
    print("\n[6/7] Fetching pull requests...")
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
    params = {"state": "all", "sort": "created", "direction": "asc"}

    prs = paginated_get(url, headers, params=params)
    save_json(prs, output_dir / "pull_requests.json")
    return prs


def fetch_stats(owner: str, repo: str, headers: dict, output_dir: Path):
    """Fetch repository activity statistics."""
    print("\n[7/7] Fetching repository statistics...")
    stats_dir = output_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)

    endpoints = {
        "commit_activity": f"{BASE_URL}/repos/{owner}/{repo}/stats/commit_activity",
        "code_frequency": f"{BASE_URL}/repos/{owner}/{repo}/stats/code_frequency",
        "participation": f"{BASE_URL}/repos/{owner}/{repo}/stats/participation",
        "punch_card": f"{BASE_URL}/repos/{owner}/{repo}/stats/punch_card",
    }

    for name, url in endpoints.items():
        print(f"  Fetching {name}...")
        retries = 0
        while retries < 5:
            resp = requests.get(url, headers=headers)
            if resp.status_code == 202:
                retries += 1
                print(f"    Computing... retry {retries}/5")
                time.sleep(3)
                continue
            if resp.status_code == 204:
                print(f"    No data available for {name}")
                break
            if resp.status_code == 422:
                print(f"    Repo too large for {name} (10,000+ commits)")
                break
            if rate_limit_wait(resp):
                continue
            resp.raise_for_status()
            save_json(resp.json(), stats_dir / f"{name}.json")
            break
        time.sleep(0.5)


def check_rate_limit(headers: dict):
    """Print current rate limit status."""
    resp = requests.get(f"{BASE_URL}/rate_limit", headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        core = data["resources"]["core"]
        search = data["resources"]["search"]
        print(f"Rate limits — Core: {core['remaining']}/{core['limit']} | "
              f"Search: {search['remaining']}/{search['limit']}")
    else:
        print("Could not check rate limits (are you authenticated?)")


def main():
    parser = argparse.ArgumentParser(
        description="GitHub API data collection for empirical research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", "-r", required=True,
                        help="Repository in owner/repo format")
    parser.add_argument("--output", "-o", default="github_data",
                        help="Output directory (default: github_data/)")
    parser.add_argument("--collect", "-c", nargs="+",
                        choices=["all", "metadata", "tree", "contributors",
                                 "commits", "issues", "prs", "stats"],
                        default=["all"],
                        help="What data to collect (default: all)")
    parser.add_argument("--since", help="Start date for commits (YYYY-MM-DD)")
    parser.add_argument("--until", help="End date for commits (YYYY-MM-DD)")
    parser.add_argument("--with-comments", action="store_true",
                        help="Also fetch issue comments (slow for large repos)")
    parser.add_argument("--max-comment-issues", type=int, default=500,
                        help="Max issues to fetch comments for (default: 500)")

    args = parser.parse_args()

    # Parse repo
    parts = args.repo.split("/")
    if len(parts) != 2:
        print("ERROR: --repo must be in owner/repo format")
        sys.exit(1)
    owner, repo = parts

    # Get token
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("WARNING: GITHUB_TOKEN not set. Rate limit: 60 req/hour (vs 5,000 authenticated)")

    headers = get_headers(token)
    output_dir = Path(args.output) / f"{owner}_{repo}"
    output_dir.mkdir(parents=True, exist_ok=True)

    collect = set(args.collect)
    if "all" in collect:
        collect = {"metadata", "tree", "contributors", "commits", "issues", "prs", "stats"}

    print(f"GitHub Data Collection")
    print(f"Repository: {owner}/{repo}")
    print(f"Output: {output_dir}")
    print(f"Collecting: {', '.join(sorted(collect))}")
    print(f"Authenticated: {'Yes' if token else 'No'}")
    check_rate_limit(headers)
    print("=" * 50)

    start_time = time.time()

    if "metadata" in collect:
        fetch_repo_metadata(owner, repo, headers, output_dir)

    if "tree" in collect:
        fetch_file_tree(owner, repo, headers, output_dir)

    if "contributors" in collect:
        fetch_contributors(owner, repo, headers, output_dir)

    if "commits" in collect:
        fetch_commits(owner, repo, headers, output_dir,
                      since=args.since, until=args.until)

    if "issues" in collect:
        fetch_issues(owner, repo, headers, output_dir,
                     fetch_comments=args.with_comments,
                     max_comment_issues=args.max_comment_issues)

    if "prs" in collect:
        fetch_pull_requests(owner, repo, headers, output_dir)

    if "stats" in collect:
        fetch_stats(owner, repo, headers, output_dir)

    elapsed = time.time() - start_time
    print(f"\nDone! Elapsed: {elapsed:.1f}s")
    print(f"Data saved to: {output_dir}")
    check_rate_limit(headers)


if __name__ == "__main__":
    main()
