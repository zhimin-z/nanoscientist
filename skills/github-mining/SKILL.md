---
name: github-mining
description: "Mine GitHub repositories using the GraphQL API (v4) for empirical software engineering research. Collect commits, issues, pull requests, contributors, and repository statistics in fewer API calls with precise field selection. Falls back to REST API for statistics endpoints. Supports cursor-based pagination, rate-limit handling, and structured data collection."
allowed-tools: [Read, Write, Edit, Bash]
required-keys: [GITHUB_TOKEN]
license: MIT license
metadata:
    skill-author: K-Dense Inc.
---

# GitHub Data Mining via GraphQL API

## Overview

This skill enables systematic collection and analysis of GitHub repository data through the **GraphQL API (v4)** for empirical research purposes. GraphQL is the primary interface — it fetches exactly the fields you need in a single request, batches multiple repositories via aliases, and uses cursor-based pagination. The REST API is used only for endpoints not available in GraphQL (repository statistics, file trees).

**Why GraphQL over REST?**

| Concern | REST v3 | GraphQL v4 |
|---------|---------|------------|
| Requests per repo overview | 5-7 separate calls | **1 call** (nested query) |
| Data fetching | Fixed response shape, over-fetches | Fetch **exactly** what you need |
| Batching repos | 1 request per repo | **N repos in 1 request** (aliases) |
| Pagination | Offset-based (`page=N`) | **Cursor-based** (`after: $endCursor`) |
| Nested data | Impossible (separate calls for PR files, comments) | **One query** fetches PR + files + reviews |
| Point cost | 1 per GET, 5 per POST | **1 per query** (no mutations needed) |

**Critical Principle:** All data collection must respect GitHub API rate limits and use authenticated requests with a `GITHUB_TOKEN` (5,000 points/hour). Always paginate with cursors to retrieve complete datasets, and include `rateLimit` in every query to monitor budget.

### GraphQL vs REST Coverage Matrix

Not everything is available in GraphQL. This matrix shows which API to use for each data type:

| Data Type | GraphQL | REST | Notes |
|-----------|---------|------|-------|
| **Repository metadata** (stars, forks, description, topics, license) | Yes | Yes | GraphQL fetches all in 1 query |
| **Languages** (byte counts per language) | Yes | Yes | `languages` connection |
| **README / file contents** | Yes | Yes | `object(expression: "HEAD:path")` returns `Blob.text` |
| **Commit history** (author, date, message) | Yes | Yes | `history` connection with `since`/`until`/`path` filters |
| **Commit diff stats** (additions/deletions per commit) | Yes | Yes | Inline on each commit node |
| **Full commit diff/patch content** (per-file patches) | **No** | Yes | REST only: `GET /commits/{sha}` with `files[].patch` |
| **Issues** (with labels, assignees, inline comments) | Yes | Yes | GraphQL returns only issues (not PRs mixed in) |
| **Pull requests** (with files, reviews, inline comments) | Yes | Yes | Nested `files`, `reviews` in one query |
| **Git blame** | Yes | Yes | `Blame` object with `ranges` |
| **Repository forks list** | Yes | Yes | `forks` connection |
| **Stargazers with timestamps** | Yes | Yes | `stargazers.edges[].starredAt` |
| **Release assets** (download counts, URLs) | Yes | Yes | `ReleaseAsset` object |
| **Deployments** | Yes | Yes | `deployments` connection |
| **Dependabot alerts** | Yes | Yes | `vulnerabilityAlerts` connection; REST has more filters |
| **Collaborators with permissions** | Yes | Yes | `collaborators` connection with `permission` edge field |
| **Code of conduct / contributing guidelines** | Yes | Yes | `codeOfConduct`, `contributingGuidelines` fields |
| **Commit comments** | Yes | Yes | `commitComments` connection |
| **Filter commits by file path** | Yes | Yes | `history(path: "src/...")` argument |
| **Recursive file tree** (single call) | **No** | Yes | REST `GET /git/trees/{sha}?recursive=1`; GraphQL needs N+1 queries per depth |
| **Repository statistics** (commit_activity, code_frequency, participation, punch_card) | **No** | Yes | REST-only `/stats/*` endpoints |
| **Traffic data** (views, clones, referrers, paths) | **No** | Yes | REST-only; requires push access |
| **Actions workflows** (list, enable, disable, logs, artifacts, runners) | **Partial** | Yes | GraphQL only via `CheckSuite` link; REST has full CRUD |
| **Code scanning alerts** | **No** | Yes | REST-only |
| **Webhooks** (CRUD) | **No** | Yes | REST-only |
| **Community health score** (composite percentage) | **No** | Yes | REST `GET /community/profile`; individual files available in GraphQL |

**Rule of thumb**: Use GraphQL for repositories, commits, issues, PRs, and search. Fall back to REST for statistics, traffic, recursive file trees, Actions workflows, and security alerts.

## When to Use This Skill

Use this skill when you need:

- **Repository Mining**: Collect commit history, contributor lists, language breakdowns, README content, and topics — all in a single GraphQL query
- **Issue & PR Analysis**: Extract issues, pull requests, labels, reviews, and comments with nested queries
- **Contributor Studies**: Analyze contributor patterns, tenure, activity frequency, and collaboration networks
- **Ecosystem Research**: Search and compare repositories by topic, language, and stars across the GitHub ecosystem
- **Cross-Repository Comparison**: Batch multiple repository queries using GraphQL aliases
- **Time-Series Analysis**: Track project metrics over time (commits/month, PR merge rates, issue volumes)
- **Search-Based Studies**: Find repositories, issues, or code matching complex criteria

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

For detailed guidance on creating schematics, refer to the scientific-schematics skill documentation.

---

## API Endpoint and Authentication

**Single endpoint** (always POST):
```
https://api.github.com/graphql
```

**Authentication** — Bearer token in `Authorization` header:
```bash
curl -H "Authorization: bearer $GITHUB_TOKEN" \
     -H "Content-Type: application/json" \
     -X POST -d '{"query": "{ viewer { login } }"}' \
     https://api.github.com/graphql
```

**Using `gh` CLI** (handles auth automatically):
```bash
gh api graphql -f query='{ viewer { login } }'
```

---

## Core Capabilities

### 1. Repository Metadata (Single Query)

Fetch comprehensive repository info including stars, forks, languages, topics, license, and README — all in one request.

```graphql
query GetRepository($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    description
    url
    homepageUrl
    stargazerCount
    forkCount
    watchers { totalCount }
    isArchived
    isFork
    createdAt
    updatedAt
    pushedAt
    diskUsage
    primaryLanguage { name color }
    languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
      totalSize
      edges { size node { name color } }
    }
    repositoryTopics(first: 20) {
      nodes { topic { name } }
    }
    licenseInfo { name spdxId }
    defaultBranchRef { name }
    # README content inline
    readme: object(expression: "HEAD:README.md") {
      ... on Blob { text byteSize }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

**`gh` CLI usage:**
```bash
gh api graphql -F owner="pytorch" -F name="pytorch" -f query='
  query($owner: String!, $name: String!) {
    repository(owner: $owner, name: $name) {
      nameWithOwner description stargazerCount forkCount
      primaryLanguage { name }
      licenseInfo { spdxId }
      repositoryTopics(first: 20) { nodes { topic { name } } }
      readme: object(expression: "HEAD:README.md") {
        ... on Blob { text }
      }
    }
  }
'
```

**Equivalent REST calls replaced**: `GET /repos`, `GET /repos/topics`, `GET /repos/languages`, `GET /repos/readme` — **4 calls → 1 query**.

### 2. Commit History Mining

Collect commit history with author, date, message, and diff stats via cursor pagination.

```graphql
query GetCommits($owner: String!, $name: String!, $cursor: String, $since: GitTimestamp) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor, since: $since) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes {
              oid
              messageHeadline
              message
              committedDate
              additions
              deletions
              changedFilesIfAvailable
              author {
                name
                email
                user { login }
              }
              parents(first: 2) { totalCount }
            }
          }
        }
      }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

**Variables:**
```json
{
  "owner": "pytorch",
  "name": "pytorch",
  "cursor": null,
  "since": "2024-01-01T00:00:00Z"
}
```

**Key advantages**:
- Each commit node includes `additions`, `deletions`, and `changedFilesIfAvailable` directly — REST requires a separate `GET /repos/{owner}/{repo}/commits/{sha}` per commit to get diff stats
- Path filtering is supported: add `path: "src/models/"` to `history()` to get only commits touching that path
- **Limitation**: Full per-file diff/patch content is NOT available in GraphQL — use REST `GET /commits/{sha}` for `files[].patch`

**`gh` CLI with auto-pagination:**
```bash
gh api graphql --paginate -F owner="pytorch" -F name="pytorch" -f query='
  query($owner: String!, $name: String!, $endCursor: String) {
    repository(owner: $owner, name: $name) {
      defaultBranchRef {
        target {
          ... on Commit {
            history(first: 100, after: $endCursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                oid messageHeadline committedDate
                author { name user { login } }
                additions deletions
              }
            }
          }
        }
      }
    }
  }
'
```

**Important**: For `gh --paginate` to work, the cursor variable **must** be named `$endCursor` and the query must include `pageInfo { hasNextPage endCursor }`.

### 3. Issue Mining

Collect issues with labels, state, creator, assignees, comments, and reactions.

```graphql
query GetIssues($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $cursor, states: [OPEN, CLOSED],
           orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        createdAt
        updatedAt
        closedAt
        url
        author { login }
        labels(first: 10) { nodes { name color } }
        assignees(first: 5) { nodes { login } }
        comments(first: 10) {
          totalCount
          nodes {
            body
            createdAt
            author { login }
          }
        }
        reactions { totalCount }
      }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

**Key advantage**: Comments are fetched **inline** with each issue — no need for separate `GET /repos/{owner}/{repo}/issues/{n}/comments` calls. For issues with many comments, paginate the `comments` connection separately.

### 4. Pull Request Mining

Collect PRs with state, merge status, reviewers, file changes, and review comments.

```graphql
query GetPullRequests($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 100, after: $cursor, states: [OPEN, CLOSED, MERGED],
                 orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        isDraft
        createdAt
        updatedAt
        mergedAt
        closedAt
        url
        author { login }
        mergedBy { login }
        additions
        deletions
        changedFiles
        labels(first: 10) { nodes { name color } }
        reviews(first: 5) {
          totalCount
          nodes {
            state
            author { login }
            submittedAt
          }
        }
        comments { totalCount }
        commits { totalCount }
        files(first: 50) {
          nodes { path additions deletions }
        }
      }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

**Key advantage**: PR metadata, files changed, and review data come back in **one query**. REST requires 3 separate paginated calls per PR (`/pulls/{n}`, `/pulls/{n}/files`, `/pulls/{n}/reviews`).

### 5. Repository Search

Find repositories by topic, language, stars, and activity — using GitHub's full search syntax.

```graphql
query SearchRepositories($query: String!, $cursor: String) {
  search(query: $query, type: REPOSITORY, first: 100, after: $cursor) {
    repositoryCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Repository {
        nameWithOwner
        description
        url
        stargazerCount
        forkCount
        createdAt
        updatedAt
        pushedAt
        primaryLanguage { name }
        licenseInfo { spdxId }
        repositoryTopics(first: 10) {
          nodes { topic { name } }
        }
      }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

**Search query syntax** (passed as the `$query` variable):
```
topic:machine-learning language:python stars:>=500 sort:stars-desc
topic:nanoscience stars:>=10 pushed:>2024-01-01
language:rust stars:500..5000 sort:updated-desc
topic:bioinformatics language:python forks:>=50
```

**`gh` CLI with auto-pagination and jq filtering:**
```bash
gh api graphql --paginate -f query='
  query($endCursor: String) {
    search(query: "topic:machine-learning language:python stars:>=500",
           type: REPOSITORY, first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on Repository {
          nameWithOwner stargazerCount description
        }
      }
    }
  }
' --jq '.data.search.nodes[] | [.nameWithOwner, .stargazerCount] | @tsv'
```

**Constraint**: Search returns a maximum of **1,000 total results** regardless of pagination. Use date-range partitioning for larger datasets.

### 6. Batch Multiple Repositories (Aliases)

Query multiple repositories in a single request using GraphQL aliases:

```graphql
query BatchRepos {
  pytorch: repository(owner: "pytorch", name: "pytorch") {
    ...RepoFields
  }
  tensorflow: repository(owner: "tensorflow", name: "tensorflow") {
    ...RepoFields
  }
  transformers: repository(owner: "huggingface", name: "transformers") {
    ...RepoFields
  }
  rateLimit { remaining cost resetAt }
}

fragment RepoFields on Repository {
  nameWithOwner
  stargazerCount
  forkCount
  description
  primaryLanguage { name }
  licenseInfo { spdxId }
  repositoryTopics(first: 10) {
    nodes { topic { name } }
  }
  defaultBranchRef {
    target {
      ... on Commit {
        history(first: 1) { totalCount }
      }
    }
  }
  issues(states: [OPEN, CLOSED]) { totalCount }
  pullRequests(states: [OPEN, CLOSED, MERGED]) { totalCount }
}
```

**REST equivalent**: 3 repos x (metadata + topics + languages + commit count + issue count + PR count) = **18+ requests → 1 query**.

### 7. Contributor Analysis

GraphQL doesn't have a direct `/contributors` equivalent, but you can extract contributors from commit history:

```graphql
query ContributorCommits($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes {
              author {
                name
                email
                user { login avatarUrl }
              }
              committedDate
              additions
              deletions
            }
          }
        }
      }
    }
  }
  rateLimit { remaining cost resetAt }
}
```

For **detailed weekly contributor statistics**, fall back to REST:
```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/{owner}/{repo}/stats/contributors"
```

### 8. Repository File Tree (REST Only)

The recursive file tree endpoint is REST-only:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
```

**Limits**: Max 100,000 entries, 7 MB response. Check `truncated` field.

### 9. Repository Statistics (REST Only)

Pre-computed aggregate statistics are only available via REST:

| Endpoint | Returns |
|----------|---------|
| `GET /repos/{owner}/{repo}/stats/commit_activity` | Weekly commit counts (52 weeks) with daily breakdown |
| `GET /repos/{owner}/{repo}/stats/code_frequency` | Weekly additions and deletions |
| `GET /repos/{owner}/{repo}/stats/participation` | Weekly commits: owner vs all contributors |
| `GET /repos/{owner}/{repo}/stats/punch_card` | Hourly commit distribution `[day, hour, count]` |

**Important**: Stats endpoints may return `202 Accepted` while computing — retry after 2-3 seconds. All exclude merge commits. Code frequency is limited to repos with <10,000 commits.

---

## Cursor-Based Pagination

GraphQL uses **cursor-based pagination** (not offset-based). Every connection (issues, pullRequests, history, etc.) requires:

- `first: N` (1-100) — number of items per page
- `after: $cursor` — cursor from the previous page's `endCursor`
- `pageInfo { hasNextPage endCursor }` — must be included in every paginated query

### Pagination Loop (Python)

```python
import requests
import json
import time

ENDPOINT = "https://api.github.com/graphql"

def graphql_paginated(token, query, variables, path_to_page_info):
    """Fetch all pages from a GraphQL connection.

    Args:
        token: GitHub personal access token
        query: GraphQL query string (must use $cursor variable)
        variables: Dict of query variables (cursor will be updated)
        path_to_page_info: Dot-separated path to pageInfo in response
            e.g., "repository.issues" or "repository.defaultBranchRef.target.history"

    Returns:
        List of all nodes across all pages
    """
    headers = {"Authorization": f"bearer {token}", "Content-Type": "application/json"}
    all_nodes = []
    cursor = None

    while True:
        variables["cursor"] = cursor
        resp = requests.post(ENDPOINT,
                             json={"query": query, "variables": variables},
                             headers=headers)
        data = resp.json()

        if "errors" in data:
            print(f"GraphQL errors: {data['errors']}")
            break

        # Navigate to the connection
        connection = data["data"]
        for key in path_to_page_info.split("."):
            connection = connection[key]

        nodes = connection.get("nodes", [])
        all_nodes.extend(nodes)

        page_info = connection["pageInfo"]
        rate = data["data"].get("rateLimit", {})
        print(f"  Fetched {len(nodes)} items (total: {len(all_nodes)}) | "
              f"Rate limit: {rate.get('remaining', '?')} remaining")

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

        # Rate limit check
        if rate.get("remaining", 100) < 50:
            print("  Low rate limit, waiting 60s...")
            time.sleep(60)

    return all_nodes
```

### Pagination with `gh` CLI (Auto)

The `gh api graphql --paginate` flag automatically follows cursors:

```bash
gh api graphql --paginate --slurp -f query='
  query($endCursor: String) {
    repository(owner: "pytorch", name: "pytorch") {
      issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
        pageInfo { hasNextPage endCursor }
        nodes { number title state createdAt }
      }
    }
  }
' > all_issues.json
```

**Requirements for `--paginate`:**
1. The cursor variable **must** be named `$endCursor`
2. Query **must** include `pageInfo { hasNextPage endCursor }`
3. Use `--slurp` to merge all pages into a single JSON array

---

## Rate Limit Management

### GraphQL Rate Limits

| Category | Limit |
|----------|-------|
| Authenticated (PAT/OAuth) | 5,000 points/hour |
| GitHub Enterprise Cloud | 10,000 points/hour |
| Burst limit | 2,000 points/minute |
| Max concurrent | 100 requests (REST + GraphQL combined) |
| Query timeout | 10 seconds (excess deducts extra points) |

**Point costs:**
- Query (no mutation): **1 point**
- Mutation: **5 points**
- Complex queries with many connections cost more (server estimates nested API calls / 100, minimum 1)

### Always Include `rateLimit` in Queries

```graphql
rateLimit {
  limit       # max points per hour
  cost        # points this query consumed
  remaining   # points left in current window
  resetAt     # ISO 8601 timestamp when limit resets
  used        # points used so far
  nodeCount   # nodes returned by this query
}
```

### Monitoring via Dedicated Query

```graphql
query { rateLimit { limit remaining resetAt used } }
```

Or via `gh`:
```bash
gh api graphql -f query='{ rateLimit { limit remaining resetAt used } }'
```

### Best Practices

1. **Always authenticate** — 5,000 points/hour vs 0 unauthenticated (GraphQL requires auth)
2. **Include `rateLimit`** in every query to track budget in real time
3. **Request only needed fields** — fewer connections = lower point cost
4. **Use aliases to batch** — 10 repos in 1 query costs ~1 point, not 10
5. **Use fragments** — avoid repetition, keep queries readable
6. **Cache responses** — save raw JSON to disk; avoid re-fetching unchanged data
7. **Respect burst limits** — max 2,000 points/minute; add small delays in tight loops
8. **Use `gh --paginate`** for CLI scripts — handles cursor logic automatically

### GraphQL vs REST Rate Comparison (Mining 1 Repo)

| Data Type | REST Requests | GraphQL Queries | GraphQL Savings |
|-----------|--------------|----------------|-----------------|
| Repo metadata + topics + languages + README | 4 | **1** | 75% |
| 1,000 issues with labels | 10 | **10** | Same page count, but richer data per page |
| 1,000 issues + first 5 comments each | 10 + 1,000 = **1,010** | **10** | **99%** |
| 500 PRs + files + reviews | 500 x 3 = **1,500** | **50** (with nested files/reviews) | **97%** |
| 10 repo comparison | 10 x 4 = **40** | **1** (aliases) | **97.5%** |

---

## Data Collection Workflow

### Step 1: Repository Overview (GraphQL)
```
query GetRepository → metadata, topics, languages, license, README
→ 1 query replaces 4+ REST calls
```

### Step 2: File Tree Mapping (REST)
```
GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1
→ REST-only; extract complete directory structure
```

### Step 3: Commit History (GraphQL, paginated)
```
query GetCommits with cursor pagination
→ Each node includes additions/deletions inline (REST needs per-commit GETs)
```

### Step 4: Issue Corpus (GraphQL, paginated)
```
query GetIssues with inline comments(first: 5)
→ Issues + first 5 comments in one pass; no separate comment fetches
```

### Step 5: Pull Request Data (GraphQL, paginated)
```
query GetPullRequests with inline files + reviews
→ PR metadata + changed files + review status in one query
```

### Step 6: Contributor Census (GraphQL + REST)
```
GraphQL: Extract unique authors from commit history
REST: GET /repos/{owner}/{repo}/stats/contributors for weekly stats
```

### Step 7: Activity Statistics (REST)
```
GET .../stats/commit_activity
GET .../stats/code_frequency
GET .../stats/participation
GET .../stats/punch_card
→ REST-only aggregate statistics
```

---

## `gh` CLI Quick Reference

| Flag | Purpose |
|------|---------|
| `-f key=value` | Pass a **string** variable |
| `-F key=value` | Pass a **typed** variable (int, bool, null) |
| `--paginate` | Auto-fetch all pages (requires `$endCursor` + `pageInfo`) |
| `--slurp` | Merge paginated responses into a single JSON array |
| `--jq EXPR` | Filter response with a jq expression |
| `--input FILE` | Read query body from a `.graphql` file |
| `-t TEMPLATE` | Format output with Go template |

### Common Patterns

```bash
# Quick repo info
gh api graphql -F owner="owner" -F name="repo" -f query='
  query($owner: String!, $name: String!) {
    repository(owner: $owner, name: $name) {
      stargazerCount forkCount description
    }
  }
'

# Search + filter to TSV
gh api graphql -f query='
  query {
    search(query: "topic:deep-learning stars:>=500", type: REPOSITORY, first: 50) {
      nodes { ... on Repository { nameWithOwner stargazerCount } }
    }
  }
' --jq '.data.search.nodes[] | [.nameWithOwner, .stargazerCount] | @tsv'

# Paginate all issues to file
gh api graphql --paginate --slurp -f query='
  query($endCursor: String) {
    repository(owner: "pytorch", name: "pytorch") {
      issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
        pageInfo { hasNextPage endCursor }
        nodes { number title state createdAt author { login } }
      }
    }
  }
' > issues.json

# Read query from file
gh api graphql --input queries/repo_overview.graphql \
  -F owner="pytorch" -F name="pytorch"

# Batch repos in one call
gh api graphql -f query='
  query {
    a: repository(owner: "pytorch", name: "pytorch") { stargazerCount }
    b: repository(owner: "tensorflow", name: "tensorflow") { stargazerCount }
    c: repository(owner: "huggingface", name: "transformers") { stargazerCount }
  }
'
```

---

## Output Data Formats

### Recommended Output Structure
```
output_dir/
├── repo_metadata.json          # Repository overview (GraphQL)
├── file_tree.json              # Complete directory structure (REST)
├── contributors.json           # Contributor list with stats
├── commits/
│   ├── commits_2023.json       # Commits segmented by year
│   └── commits_2024.json
├── issues/
│   ├── issues_all.json         # All issues with inline comments
│   └── issue_comments/         # Full comment threads (if needed)
├── pull_requests/
│   ├── prs_all.json            # All PRs with files + reviews
│   └── pr_files/               # Detailed file changes per PR
├── stats/                      # REST-only statistics
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
- Extract issue titles and first N comments (available inline from GraphQL)
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

1. **Completeness**: Always paginate cursors until `hasNextPage` is `false`
2. **Accuracy**: Check for `"errors"` in GraphQL responses; handle rate limits and timeouts
3. **Reproducibility**: Save raw API responses; document all query variables and date ranges
4. **Rate-limit compliance**: Include `rateLimit` in every query; never exceed 5,000 points/hour
5. **Ethical use**: Only access public data; respect repository visibility and contributor privacy
6. **Data integrity**: GraphQL search is capped at 1,000 results — use date partitioning for larger sets
7. **Proper authentication**: GraphQL **requires** a `GITHUB_TOKEN` (no unauthenticated access)

## Common Mistakes to Avoid

1. **Not paginating cursors** — First page returns only `first: N` items; large repos have thousands of issues/commits
2. **Using `page` instead of `$endCursor`** — GraphQL uses cursor-based pagination, not offset
3. **Naming cursor variable wrong for `gh --paginate`** — Must be `$endCursor`, not `$cursor`
4. **Missing `pageInfo` in query** — `gh --paginate` silently fails without `pageInfo { hasNextPage endCursor }`
5. **Exceeding search limits** — Search returns max 1,000 total results; use date-range partitioning
6. **Forgetting `rateLimit`** — Always include it to monitor budget; complex nested queries can cost more than 1 point
7. **Using GraphQL for stats** — `/stats/*` endpoints are REST-only; GraphQL has no equivalent
8. **Using GraphQL for file trees** — `/git/trees` is REST-only; GraphQL `object` can fetch individual files but not recursive trees
9. **Over-fetching nested connections** — Requesting `comments(first: 100)` on 100 issues = 10,000 nodes; start small, paginate separately if needed
10. **Not using fragments** — Repeated field selections bloat queries; use `fragment` for shared shapes

## Resources

- `references/api_endpoints_reference.md` — Complete GraphQL query reference with all fields
- `references/pagination_and_rate_limits.md` — Cursor pagination and rate-limit handling guide
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
