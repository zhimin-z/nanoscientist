# GitHub REST API Endpoints Reference

Complete reference for all GitHub REST API endpoints used in repository mining.

**Base URL**: `https://api.github.com`
**API Version**: `2022-11-28`
**Required Headers**:
```
Authorization: Bearer {GITHUB_TOKEN}
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

---

## Repository Endpoints

### Get a Repository
```
GET /repos/{owner}/{repo}
```
Returns full repository metadata: name, description, stars, forks, language, license, topics, dates.

### List Organization Repositories
```
GET /orgs/{org}/repos
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | `all`, `public`, `private`, `forks`, `sources`, `member` |
| `sort` | string | `created`, `updated`, `pushed`, `full_name` |
| `direction` | string | `asc`, `desc` |
| `per_page` | int | Max 100 (default 30) |
| `page` | int | Page number (default 1) |

### List Repository Contributors
```
GET /repos/{owner}/{repo}/contributors
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `anon` | string | Set to `1` to include anonymous contributors |
| `per_page` | int | Max 100 |
| `page` | int | Page number |

Returns: `login`, `id`, `avatar_url`, `contributions` (commit count).

### List Repository Languages
```
GET /repos/{owner}/{repo}/languages
```
Returns a JSON object mapping language names to byte counts: `{"Python": 124500, "Shell": 3200}`.

### Get Repository Topics
```
GET /repos/{owner}/{repo}/topics
```
Returns: `{"names": ["machine-learning", "python", "data-science"]}`.

---

## Commit Endpoints

### List Commits
```
GET /repos/{owner}/{repo}/commits
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `sha` | string | Branch name or SHA to start listing from |
| `path` | string | Only commits containing this file path |
| `author` | string | GitHub login or email |
| `committer` | string | GitHub login or email |
| `since` | string | ISO 8601 date — commits after this date |
| `until` | string | ISO 8601 date — commits before this date |
| `per_page` | int | Max 100 |
| `page` | int | Page number |

**Key response fields per commit**:
- `sha` — Commit hash
- `commit.message` — Commit message
- `commit.author.name` — Author name
- `commit.author.email` — Author email
- `commit.author.date` — Author date (ISO 8601)
- `commit.committer.date` — Committer date
- `author.login` — GitHub username (may be null)
- `parents` — Array of parent commits (2+ parents = merge commit)

### Get a Single Commit
```
GET /repos/{owner}/{repo}/commits/{ref}
```
Returns full commit with `stats` (total/additions/deletions) and `files` array (filename, status, additions, deletions, patch).

---

## Issue Endpoints

### List Repository Issues
```
GET /repos/{owner}/{repo}/issues
```
**NOTE**: Returns both issues AND pull requests. Filter by checking `pull_request` key is absent for pure issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `state` | string | `open`, `closed`, `all` (default: `open`) |
| `labels` | string | Comma-separated label names |
| `sort` | string | `created`, `updated`, `comments` (default: `created`) |
| `direction` | string | `asc`, `desc` (default: `desc`) |
| `since` | string | ISO 8601 — only issues updated after this date |
| `creator` | string | Username of the issue creator |
| `assignee` | string | Username, `none`, or `*` |
| `mentioned` | string | Username mentioned in issue |
| `milestone` | string | Milestone number, `*`, or `none` |
| `per_page` | int | Max 100 |
| `page` | int | Page number |

**Key response fields per issue**:
- `number` — Issue number
- `title` — Issue title
- `body` — Issue body (Markdown)
- `state` — `open` or `closed`
- `labels` — Array of label objects (`name`, `color`, `description`)
- `user.login` — Creator username
- `assignees` — Array of assigned users
- `created_at` — Creation timestamp
- `updated_at` — Last update timestamp
- `closed_at` — Close timestamp (null if open)
- `comments` — Number of comments
- `pull_request` — Present ONLY if the item is a PR (use to filter)

### Get a Single Issue
```
GET /repos/{owner}/{repo}/issues/{issue_number}
```

### List Issue Comments
```
GET /repos/{owner}/{repo}/issues/{issue_number}/comments
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `since` | string | ISO 8601 — comments updated after this date |
| `per_page` | int | Max 100 |
| `page` | int | Page number |

Returns: `id`, `user.login`, `body`, `created_at`, `updated_at`.

### List All Issue Comments for a Repository
```
GET /repos/{owner}/{repo}/issues/comments
```
Returns all comments on all issues, sorted by `updated_at`. Useful for bulk collection.

---

## Pull Request Endpoints

### List Pull Requests
```
GET /repos/{owner}/{repo}/pulls
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `state` | string | `open`, `closed`, `all` (default: `open`) |
| `head` | string | Filter by head user/org and branch: `user:branch` |
| `base` | string | Filter by base branch name |
| `sort` | string | `created`, `updated`, `popularity`, `long-running` |
| `direction` | string | `asc`, `desc` |
| `per_page` | int | Max 100 |
| `page` | int | Page number |

**Key response fields per PR**:
- `number`, `title`, `body`, `state`
- `user.login` — PR author
- `created_at`, `updated_at`, `closed_at`, `merged_at`
- `labels` — Array of label objects
- `requested_reviewers` — Array of reviewer users
- `draft` — Boolean
- `merged` — Boolean (only on single-PR GET)
- `additions`, `deletions`, `changed_files` (only on single-PR GET)

### Get a Single Pull Request
```
GET /repos/{owner}/{repo}/pulls/{pull_number}
```
Includes merge metadata: `merged`, `merged_by`, `merge_commit_sha`, `additions`, `deletions`, `changed_files`.

### List PR Commits
```
GET /repos/{owner}/{repo}/pulls/{pull_number}/commits
```
Max 250 commits per PR. Paginated.

### List PR Files
```
GET /repos/{owner}/{repo}/pulls/{pull_number}/files
```
Returns: `filename`, `status` (added/modified/removed), `additions`, `deletions`, `changes`, `patch`.
Max 3,000 files per PR. Paginated.

### List PR Review Comments
```
GET /repos/{owner}/{repo}/pulls/{pull_number}/comments
```
Returns inline review comments with `path`, `position`, `body`, `user.login`.

---

## Git Data Endpoints

### Get a Tree (File Listing)
```
GET /repos/{owner}/{repo}/git/trees/{tree_sha}
```
| Parameter | Type | Description |
|-----------|------|-------------|
| `recursive` | string | Set to any value for recursive listing |

Use `tree_sha` = branch name (e.g., `main`) to get the tree of the latest commit.

**Response fields per entry**:
- `path` — File path relative to repo root
- `mode` — `100644` (file), `100755` (executable), `040000` (directory), `120000` (symlink), `160000` (submodule)
- `type` — `blob` (file) or `tree` (directory)
- `sha` — Object SHA
- `size` — File size in bytes (blobs only)

**Limits**: Max 100,000 entries, 7 MB response. Check `truncated: true`.

---

## Statistics Endpoints

All stats endpoints may return `202 Accepted` while computing. Retry after 2-3 seconds. All exclude merge commits.

### Contributor Stats
```
GET /repos/{owner}/{repo}/stats/contributors
```
Per-contributor weekly data: `weeks[].{w, a, d, c}` (week timestamp, additions, deletions, commits).
**Note**: Returns 0 for additions/deletions in repos with 10,000+ commits.

### Commit Activity (Last Year)
```
GET /repos/{owner}/{repo}/stats/commit_activity
```
52 weeks of data: `{days: [Sun..Sat], total: N, week: unix_ts}`.

### Code Frequency
```
GET /repos/{owner}/{repo}/stats/code_frequency
```
Weekly `[timestamp, additions, deletions]`. Limited to repos with <10,000 commits.

### Participation
```
GET /repos/{owner}/{repo}/stats/participation
```
Two arrays of 52 integers: `all` (all contributors) and `owner` (repo owner only).

### Punch Card
```
GET /repos/{owner}/{repo}/stats/punch_card
```
Array of `[day (0=Sun..6=Sat), hour (0-23), commit_count]`.

---

## Search Endpoints

### Search Repositories
```
GET /search/repositories?q={query}
```
Sort: `stars`, `forks`, `help-wanted-issues`, `updated`. Order: `asc`, `desc`.

### Search Issues and PRs
```
GET /search/issues?q={query}
```
Sort: `comments`, `reactions`, `created`, `updated`, `interactions`. Order: `asc`, `desc`.

### Search Commits
```
GET /search/commits?q={query}
```
Sort: `author-date`, `committer-date`. Order: `asc`, `desc`.

### Search Code
```
GET /search/code?q={query}
```
Rate limit: 10 requests/minute. Requires authentication. No sort parameter.

### Search Users
```
GET /search/users?q={query}
```
Sort: `followers`, `repositories`, `joined`. Order: `asc`, `desc`.

### Search Topics
```
GET /search/topics?q={query}
```
No sort parameter. Qualifiers: `is:featured`, `is:curated`.

**All search endpoints**: Max 1,000 total results, max 100 per page, 256-char query limit.
