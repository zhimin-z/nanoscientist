---
name: github-mining
description: "Mine GitHub repositories using the REST API for empirical software engineering research. Collect commits, issues, pull requests, contributors, file trees, and repository statistics. Supports pagination, rate-limit handling, and structured data collection for quantitative and qualitative analysis of open-source projects."
allowed-tools: [Read, Write, Edit, Bash]
license: MIT license
metadata:
    skill-author: K-Dense Inc.
---

# GitHub Data Mining via REST API

## Overview

This skill enables systematic collection and analysis of GitHub repository data through the REST API for empirical research purposes. It provides structured methods for extracting commits, issues, pull requests, contributor activity, repository structure, and statistics — the building blocks for empirical software engineering studies, ecosystem analysis, and open-source community research.

**Critical Principle:** All data collection must respect GitHub API rate limits and use authenticated requests with a `GITHUB_TOKEN` for higher throughput (5,000 requests/hour vs 60 unauthenticated). Always implement pagination to retrieve complete datasets, and use exponential backoff when approaching rate limits.

## When to Use This Skill

Use this skill when you need:

- **Repository Mining**: Collect commit history, file trees, contributor lists, and language breakdowns from GitHub repositories
- **Issue & PR Analysis**: Extract issues, pull requests, comments, labels, and review data for empirical analysis
- **Contributor Studies**: Analyze contributor patterns, tenure, activity frequency, and collaboration networks
- **Ecosystem Research**: Study open-source project evolution, growth trajectories, and community dynamics
- **Coverage/Structure Mapping**: Map directory structures to domain categories (e.g., mathematical modules in Mathlib)
- **Time-Series Analysis**: Track project metrics over time (commits/month, PR merge rates, issue volumes)
- **Search-Based Studies**: Find repositories, code patterns, or issues matching specific criteria across GitHub

## Visual Enhancement with Scientific Schematics

**When creating documents with this skill, always consider adding scientific diagrams and schematics to enhance visual communication.**

If your document does not already contain schematics or diagrams:
- Use the **scientific-schematics** skill to generate AI-powered publication-quality diagrams
- Simply describe your desired diagram in natural language

**When to add schematics:**
- Data collection pipeline architecture diagrams
- API request flow and pagination logic
- Contributor network visualizations
- Repository growth trajectory charts
- Issue/PR lifecycle state diagrams
- Directory-to-domain mapping diagrams

For detailed guidance on creating schematics, refer to the scientific-schematics skill documentation.

---

## Core Capabilities

### 1. Repository Metadata Collection

Retrieve comprehensive repository information including stars, forks, language, license, creation date, and description.

**Endpoint**: `GET /repos/{owner}/{repo}`

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/{owner}/{repo}"
```

**Key fields returned**: `full_name`, `description`, `stargazers_count`, `forks_count`, `open_issues_count`, `language`, `license`, `created_at`, `updated_at`, `pushed_at`, `size`, `default_branch`, `topics`.

### 2. Commit History Mining

Collect full commit history with author, date, message, and file-change metadata.

**Endpoint**: `GET /repos/{owner}/{repo}/commits`

**Key parameters**:
- `sha` — Branch name or commit SHA to start from
- `since` / `until` — ISO 8601 timestamps for date range filtering
- `author` — GitHub username or email to filter by author
- `path` — Filter commits touching a specific file/directory
- `per_page` — Up to 100 results per page (default: 30)
- `page` — Page number for pagination

```bash
# Fetch commits in a date range, 100 per page
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/commits?since=2023-01-01T00:00:00Z&until=2024-01-01T00:00:00Z&per_page=100&page=1"
```

**Data extractable per commit**: `sha`, `commit.message`, `commit.author.name`, `commit.author.email`, `commit.author.date`, `commit.committer.date`, `author.login` (GitHub username), `parents` (for merge detection), `stats` (additions/deletions — requires per-commit GET).

### 3. Issue Mining

Collect issues with their labels, state, creator, assignees, comments, and timestamps.

**Endpoint**: `GET /repos/{owner}/{repo}/issues`

**Key parameters**:
- `state` — `open`, `closed`, or `all`
- `labels` — Comma-separated label names
- `sort` — `created`, `updated`, or `comments`
- `direction` — `asc` or `desc`
- `since` — ISO 8601 timestamp (filters by last update)
- `creator` — Filter by issue author username
- `milestone` — Milestone number, `*` (any), or `none`
- `per_page` / `page` — Pagination (max 100 per page)

**Important**: This endpoint returns both issues AND pull requests. Filter by checking that `pull_request` key is absent for pure issues.

```bash
# Fetch all closed issues, sorted by creation date
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/issues?state=all&per_page=100&page=1"
```

**Issue comments endpoint**: `GET /repos/{owner}/{repo}/issues/{issue_number}/comments`

### 4. Pull Request Mining

Collect PRs with their state, merge status, reviewers, changed files, and review comments.

**Endpoint**: `GET /repos/{owner}/{repo}/pulls`

**Key parameters**:
- `state` — `open`, `closed`, or `all`
- `sort` — `created`, `updated`, `popularity`, `long-running`
- `direction` — `asc` or `desc`
- `head` — Filter by head branch (`user:branch-name`)
- `base` — Filter by base branch
- `per_page` / `page` — Pagination (max 100 per page)

**Sub-endpoints per PR**:
- `GET /repos/{owner}/{repo}/pulls/{pull_number}/commits` — Commits in the PR
- `GET /repos/{owner}/{repo}/pulls/{pull_number}/files` — Changed files with diffs
- `GET /repos/{owner}/{repo}/pulls/{pull_number}/comments` — Review comments

```bash
# Fetch all PRs (open + closed + merged)
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/pulls?state=all&per_page=100&page=1"
```

**Key fields**: `number`, `title`, `state`, `created_at`, `updated_at`, `closed_at`, `merged_at`, `user.login`, `labels`, `requested_reviewers`, `additions`, `deletions`, `changed_files`, `mergeable`, `merged_by`.

### 5. Contributor Analysis

Retrieve contributor lists with commit counts and detailed weekly statistics.

**Endpoints**:
- `GET /repos/{owner}/{repo}/contributors` — List contributors sorted by commit count
- `GET /repos/{owner}/{repo}/stats/contributors` — Detailed weekly addition/deletion/commit stats per contributor

```bash
# List all contributors with commit counts
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/contributors?per_page=100&page=1"
```

**Stats endpoint returns per contributor**: `author` (login, id, avatar), `total` (total commits), `weeks[]` (array of `{w: unix_timestamp, a: additions, d: deletions, c: commits}`).

**Caching note**: Stats endpoints may return `202 Accepted` while GitHub computes results. Retry after 2-3 seconds. For repositories with 10,000+ commits, addition/deletion counts may return 0.

### 6. Repository File Tree

Map the complete directory structure of a repository for module-level analysis.

**Endpoint**: `GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1`

```bash
# Get full recursive file tree of default branch
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
```

**Returns**: Array of tree entries, each with `path`, `mode`, `type` (blob/tree), `sha`, `size`. Limited to 100,000 entries and 7 MB response size. Check `truncated` field — if `true`, use non-recursive requests to traverse subdirectories.

### 7. Repository Statistics

Pre-computed aggregate statistics for activity analysis.

**Endpoints**:
| Endpoint | Returns |
|----------|---------|
| `GET /repos/{owner}/{repo}/stats/commit_activity` | Weekly commit counts for the last year (52 weeks), with daily breakdown |
| `GET /repos/{owner}/{repo}/stats/code_frequency` | Weekly additions and deletions |
| `GET /repos/{owner}/{repo}/stats/participation` | Weekly commit counts split by owner vs all contributors |
| `GET /repos/{owner}/{repo}/stats/punch_card` | Hourly commit distribution [day, hour, count] |
| `GET /repos/{owner}/{repo}/stats/contributors` | Per-contributor weekly stats |

**Important**: All statistics exclude merge commits. Code frequency is limited to repos with fewer than 10,000 commits. Stats responses may be `202` (computing) — retry with delay.

### 8. Search API

Find repositories, issues, code, commits, and users matching complex queries.

**Endpoints**:
| Endpoint | Sort options | Rate limit |
|----------|-------------|------------|
| `GET /search/repositories?q=...` | `stars`, `forks`, `updated` | 30/min |
| `GET /search/issues?q=...` | `comments`, `reactions`, `created`, `updated` | 30/min |
| `GET /search/commits?q=...` | `author-date`, `committer-date` | 30/min |
| `GET /search/code?q=...` | — | 10/min |
| `GET /search/users?q=...` | `followers`, `repositories`, `joined` | 30/min |

**Query syntax** (the `q` parameter supports qualifiers):
```
# Search issues in a specific repo with label
q=repo:leanprover-community/mathlib4+label:bug+state:closed

# Search repos by language and stars
q=language:lean+stars:>100

# Search commits by author and date
q=repo:owner/repo+author:username+author-date:>2023-01-01
```

**Constraints**: Maximum 1,000 results per search. Query limited to 256 characters (excluding operators). Max 5 AND/OR/NOT operators per query. Max 100 results per page.

## Pagination Strategy

**All list endpoints are paginated.** You MUST paginate to collect complete datasets.

### How Pagination Works

1. GitHub returns a `Link` header with `rel="next"` and `rel="last"` URLs
2. Use `per_page=100` (maximum) to minimize total requests
3. Continue fetching while `rel="next"` exists in the response headers
4. Stop when `rel="next"` is absent

### Python Pagination Pattern

```python
import requests
import time

def paginated_get(url, token, params=None):
    """Fetch all pages from a GitHub API endpoint."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = params or {}
    params["per_page"] = 100
    all_items = []

    while url:
        resp = requests.get(url, headers=headers, params=params)

        # Handle rate limiting
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset_time - time.time(), 1)
            print(f"Rate limited. Waiting {wait:.0f}s...")
            time.sleep(wait)
            continue

        # Handle 202 (stats being computed)
        if resp.status_code == 202:
            time.sleep(3)
            continue

        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            all_items.extend(data)
        else:
            all_items.append(data)

        # Follow Link header for next page
        url = None
        params = {}  # URL in Link header already includes params
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")

    return all_items
```

### curl Pagination (bash)

```bash
#!/bin/bash
# Paginate through all results
PAGE=1
while true; do
  RESPONSE=$(curl -s -w "\n%{http_code}" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/{owner}/{repo}/issues?state=all&per_page=100&page=$PAGE")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  # Check for empty array (no more results)
  if [ "$BODY" = "[]" ] || [ "$HTTP_CODE" != "200" ]; then
    break
  fi

  echo "$BODY" >> all_issues.json
  PAGE=$((PAGE + 1))
  sleep 0.5  # Be polite to the API
done
```

## Rate Limit Management

### Rate Limit Overview

| Category | Authenticated | Unauthenticated |
|----------|--------------|-----------------|
| Core API | 5,000 /hour | 60 /hour |
| Search API | 30 /minute | 10 /minute |
| Code Search | 10 /minute | N/A |

### Monitoring Rate Limits

Check current usage via response headers on every request:
- `X-RateLimit-Limit` — Maximum requests allowed
- `X-RateLimit-Remaining` — Requests remaining in current window
- `X-RateLimit-Reset` — Unix timestamp when the limit resets

Or query the dedicated endpoint (does NOT count against your limit):
```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/rate_limit"
```

### Best Practices for Rate Limits

1. **Always authenticate** — Use `GITHUB_TOKEN` for 5,000 req/hour vs 60
2. **Check remaining** — Read `X-RateLimit-Remaining` header before bursts
3. **Exponential backoff** — On 403/429, wait and retry with increasing delays
4. **Batch efficiently** — Use `per_page=100` to minimize total requests
5. **Cache responses** — Save raw JSON to disk; avoid re-fetching unchanged data
6. **Conditional requests** — Use `If-Modified-Since` or `If-None-Match` (ETag) headers for 304 responses that don't count against limits
7. **Stagger search queries** — Search API is limited to 30/minute; add 2s delays between searches

## Data Collection Workflow

### Step 1: Repository Overview
```
GET /repos/{owner}/{repo}
→ Extract: stars, forks, language, created_at, description, topics
```

### Step 2: File Tree Mapping
```
GET /repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1
→ Extract: complete directory structure for domain/module mapping
```

### Step 3: Contributor Census
```
GET /repos/{owner}/{repo}/contributors?per_page=100 (paginate)
GET /repos/{owner}/{repo}/stats/contributors
→ Extract: contributor list with commit counts, weekly activity
```

### Step 4: Commit History
```
GET /repos/{owner}/{repo}/commits?per_page=100&since=...&until=... (paginate)
→ Extract: author, date, message, SHA for each commit
→ Segment by time period for growth analysis
```

### Step 5: Issue Corpus
```
GET /repos/{owner}/{repo}/issues?state=all&per_page=100 (paginate)
→ Filter: exclude items with pull_request key for pure issues
→ Extract: title, body, labels, state, creator, created_at, closed_at, comments
→ For each issue: GET .../issues/{n}/comments for discussion text
```

### Step 6: Pull Request Data
```
GET /repos/{owner}/{repo}/pulls?state=all&per_page=100 (paginate)
→ Extract: title, state, created_at, merged_at, user, labels, additions, deletions
→ For key PRs: GET .../pulls/{n}/files for changed-file analysis
```

### Step 7: Activity Statistics
```
GET /repos/{owner}/{repo}/stats/commit_activity
GET /repos/{owner}/{repo}/stats/code_frequency
GET /repos/{owner}/{repo}/stats/participation
GET /repos/{owner}/{repo}/stats/punch_card
→ Extract: weekly/daily aggregate activity patterns
```

## Output Data Formats

### Recommended Output Structure
```
output_dir/
├── repo_metadata.json          # Repository overview
├── file_tree.json              # Complete directory structure
├── contributors.json           # Contributor list with stats
├── commits/
│   ├── commits_2023.json       # Commits segmented by year
│   └── commits_2024.json
├── issues/
│   ├── issues_all.json         # All issues with metadata
│   └── issue_comments/         # Comments per issue
│       ├── issue_1.json
│       └── issue_2.json
├── pull_requests/
│   ├── prs_all.json            # All PRs with metadata
│   └── pr_files/               # Changed files per PR
├── stats/
│   ├── commit_activity.json
│   ├── code_frequency.json
│   ├── participation.json
│   └── punch_card.json
└── analysis/
    ├── summary_stats.csv       # Computed metrics
    └── time_series.csv         # Monthly aggregates
```

## Analysis Patterns

### Growth Trajectory Analysis
- Aggregate commits by month/quarter/year
- Count distinct contributors per time period
- Track PR merge rate and issue close rate over time
- Compute year-over-year growth rates

### Contributor Segmentation
- Identify first-commit date per contributor from commit history
- Classify as newcomer (<30 days since first commit) vs established
- Compare issue types and PR acceptance rates across cohorts
- Analyze response times to newcomer vs established contributor issues

### Issue Taxonomy (Open Coding)
- Extract issue titles and first N comments
- Categorize by labels and by content analysis
- Build frequency distribution of challenge types
- Cross-reference with contributor tenure for friction analysis

### Domain Coverage Mapping
- Parse file tree into directory hierarchy
- Map top-level directories to subject domains
- Count files, commits, and PR activity per domain
- Identify underrepresented domains with low activity

### Collaboration Network
- Build co-authorship graph from commits
- Build co-participation graph from shared issue/PR discussions
- Compute network centrality metrics
- Identify community clusters and bridge contributors

## Quality Standards

All data collection must meet these criteria:

1. **Completeness**: Always paginate to collect ALL items, not just the first page
2. **Accuracy**: Validate response codes and handle edge cases (empty repos, 404s, rate limits)
3. **Reproducibility**: Save raw API responses; document all collection parameters and date ranges
4. **Rate-limit compliance**: Never exceed API limits; implement proper backoff and waiting
5. **Ethical use**: Only access public data; respect repository visibility and contributor privacy
6. **Data integrity**: Check for `truncated` responses (trees), `incomplete_results` (search), and 202 retries (stats)
7. **Proper authentication**: Always use `GITHUB_TOKEN` when available for higher rate limits and access

## Common Mistakes to Avoid

1. **Not paginating** — First page returns only 30 items by default; large repos have thousands of issues/commits
2. **Confusing issues and PRs** — The `/issues` endpoint returns BOTH; filter by absence of `pull_request` key for pure issues
3. **Ignoring 202 responses** — Stats endpoints return 202 while computing; must retry after delay
4. **Exceeding search limits** — Search returns max 1,000 total results; use date-range partitioning for larger datasets
5. **Forgetting rate limits** — 5,000 req/hour sounds generous but disappears quickly when fetching comments for thousands of issues
6. **Not caching** — Re-running collection without caching wastes rate-limit budget; save raw JSON
7. **Missing merge commits in stats** — GitHub statistics endpoints exclude merge commits by design
8. **Truncated trees** — Trees with >100,000 entries are truncated; check the `truncated` field

## Resources

- `references/api_endpoints_reference.md` — Complete endpoint reference with all parameters
- `references/pagination_and_rate_limits.md` — Detailed pagination and rate-limit handling guide
- `references/search_query_syntax.md` — GitHub search query qualifiers and examples
- `references/data_analysis_patterns.md` — Common analysis patterns for empirical research

## Integration with Other Skills

| Task | Skill |
|------|-------|
| Analyze collected data statistically | **statistical-analysis** |
| Visualize trends and distributions | **data-visualization** |
| Write up findings as a research paper | **scientific-writing** |
| Look up background literature on the project | **research-lookup** |
| Generate architecture/flow diagrams | **scientific-schematics** |
| Formulate hypotheses from patterns | **hypothesis-generation** |
| Manage citations for the write-up | **citation-management** |
