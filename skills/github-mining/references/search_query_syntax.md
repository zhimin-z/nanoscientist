# GitHub Search Query Syntax

Complete guide to constructing search queries for the GitHub Search API.

---

## General Syntax

Search queries are passed via the `q` parameter. They consist of **search terms** and **qualifiers**.

```
q=search+terms+qualifier:value+qualifier:value
```

- Use `+` to separate terms (URL-encoded spaces)
- Qualifiers use `key:value` format
- Multiple qualifiers are ANDed by default
- Maximum query length: 256 characters (excluding operators/qualifiers)
- Maximum 5 AND/OR/NOT operators per query
- Maximum 1,000 results total per search

---

## Search Repositories

**Endpoint**: `GET /search/repositories?q={query}`

### Qualifiers

| Qualifier | Example | Description |
|-----------|---------|-------------|
| `in:name` | `lean in:name` | Search in repo name |
| `in:description` | `theorem prover in:description` | Search in description |
| `in:readme` | `formalization in:readme` | Search in README |
| `in:topics` | `math in:topics` | Search in topics |
| `user:{username}` | `user:leanprover-community` | Repos by user/org |
| `org:{orgname}` | `org:leanprover-community` | Repos by organization |
| `language:{lang}` | `language:lean` | Filter by primary language |
| `stars:{n}` | `stars:>100` | Star count filter |
| `forks:{n}` | `forks:>=50` | Fork count filter |
| `size:{n}` | `size:>1000` | Repo size in KB |
| `created:{date}` | `created:>2023-01-01` | Creation date |
| `pushed:{date}` | `pushed:>2024-01-01` | Last push date |
| `license:{key}` | `license:mit` | License type |
| `archived:{bool}` | `archived:false` | Archived status |
| `is:public` / `is:private` | `is:public` | Visibility |
| `topic:{name}` | `topic:machine-learning` | Has topic |
| `topics:{n}` | `topics:>=3` | Number of topics |

### Sort Options
`stars`, `forks`, `help-wanted-issues`, `updated`

### Examples
```
# Lean repositories with 50+ stars
q=language:lean+stars:>50

# Formal verification repos created after 2022
q=formal+verification+language:lean+created:>2022-01-01

# Mathlib-related repos in an organization
q=org:leanprover-community+mathlib+in:name
```

---

## Search Issues and Pull Requests

**Endpoint**: `GET /search/issues?q={query}`

This endpoint searches BOTH issues and PRs. Use `is:issue` or `is:pr` to filter.

### Qualifiers

| Qualifier | Example | Description |
|-----------|---------|-------------|
| `is:issue` / `is:pr` | `is:issue` | Filter to issues or PRs |
| `is:open` / `is:closed` | `is:closed` | State filter |
| `is:merged` / `is:unmerged` | `is:merged` | PR merge status |
| `repo:{owner}/{repo}` | `repo:leanprover-community/mathlib4` | Specific repository |
| `user:{username}` | `user:leanprover-community` | Issues in user's repos |
| `author:{username}` | `author:octocat` | Created by user |
| `assignee:{username}` | `assignee:octocat` | Assigned to user |
| `mentions:{username}` | `mentions:octocat` | Mentions user |
| `commenter:{username}` | `commenter:octocat` | User commented |
| `label:{name}` | `label:bug` | Has label |
| `milestone:{title}` | `milestone:"v1.0"` | In milestone |
| `in:title` | `tactic in:title` | Search in title |
| `in:body` | `simp in:body` | Search in body |
| `in:comments` | `sorry in:comments` | Search in comments |
| `created:{date}` | `created:>2024-01-01` | Creation date |
| `updated:{date}` | `updated:>2024-06-01` | Last update date |
| `closed:{date}` | `closed:>2024-01-01` | Close date |
| `comments:{n}` | `comments:>10` | Number of comments |
| `reactions:{n}` | `reactions:>5` | Number of reactions |
| `language:{lang}` | `language:lean` | Repository language |
| `no:label` | `no:label` | Issues without labels |
| `no:milestone` | `no:milestone` | Issues without milestone |
| `no:assignee` | `no:assignee` | Unassigned issues |

### Sort Options
`comments`, `reactions`, `created`, `updated`, `interactions`

### Examples
```
# All closed bug issues in mathlib4
q=repo:leanprover-community/mathlib4+is:issue+is:closed+label:bug

# Issues mentioning "tactic" created in 2024
q=repo:leanprover-community/mathlib4+is:issue+tactic+in:title+created:2024-01-01..2024-12-31

# Merged PRs by a specific author
q=repo:leanprover-community/mathlib4+is:pr+is:merged+author:username

# Issues with many comments (active discussions)
q=repo:leanprover-community/mathlib4+is:issue+comments:>20

# Recent unresolved issues
q=repo:leanprover-community/mathlib4+is:issue+is:open+created:>2024-06-01
```

---

## Search Commits

**Endpoint**: `GET /search/commits?q={query}`

### Qualifiers

| Qualifier | Example | Description |
|-----------|---------|-------------|
| `repo:{owner}/{repo}` | `repo:leanprover-community/mathlib4` | Specific repository |
| `author:{username}` | `author:octocat` | Commit author |
| `committer:{username}` | `committer:octocat` | Committer |
| `author-name:{name}` | `author-name:"John Doe"` | Author real name |
| `committer-name:{name}` | `committer-name:"Jane"` | Committer real name |
| `author-email:{email}` | `author-email:user@example.com` | Author email |
| `author-date:{date}` | `author-date:>2024-01-01` | Author date |
| `committer-date:{date}` | `committer-date:>2024-01-01` | Committer date |
| `merge:true` / `merge:false` | `merge:false` | Merge commit filter |
| `is:public` / `is:private` | `is:public` | Repo visibility |

### Sort Options
`author-date`, `committer-date`

### Examples
```
# Commits mentioning "simp" in mathlib4 from 2024
q=repo:leanprover-community/mathlib4+simp+author-date:>2024-01-01

# Non-merge commits by a specific author
q=repo:leanprover-community/mathlib4+author:username+merge:false
```

---

## Search Code

**Endpoint**: `GET /search/code?q={query}`

**Rate limit**: 10 requests/minute (requires authentication).
Only searches the default branch. Files must be <384 KB. At least one search term required.

### Qualifiers

| Qualifier | Example | Description |
|-----------|---------|-------------|
| `in:file` | `sorry in:file` | Search in file content |
| `in:path` | `Algebra in:path` | Search in file path |
| `repo:{owner}/{repo}` | `repo:leanprover-community/mathlib4` | Specific repo |
| `user:{username}` | `user:leanprover-community` | User's repos |
| `language:{lang}` | `language:lean` | File language |
| `path:{path}` | `path:Mathlib/Algebra` | File path prefix |
| `filename:{name}` | `filename:Basic.lean` | Exact filename |
| `extension:{ext}` | `extension:lean` | File extension |
| `size:{n}` | `size:>1000` | File size in bytes |

### Examples
```
# Find "sorry" (incomplete proofs) in Lean files in mathlib4
q=sorry+repo:leanprover-community/mathlib4+language:lean

# Find files in the Algebra directory
q=repo:leanprover-community/mathlib4+path:Mathlib/Algebra+extension:lean

# Find import statements for a specific module
q="import+Mathlib.Algebra"+repo:leanprover-community/mathlib4
```

---

## Search Users

**Endpoint**: `GET /search/users?q={query}`

### Qualifiers

| Qualifier | Example | Description |
|-----------|---------|-------------|
| `type:user` / `type:org` | `type:user` | Account type |
| `in:login` | `lean in:login` | Search in username |
| `in:name` | `lean in:name` | Search in display name |
| `in:email` | `in:email` | Search in email |
| `repos:{n}` | `repos:>10` | Number of repos |
| `followers:{n}` | `followers:>100` | Follower count |
| `location:{place}` | `location:zurich` | User location |
| `language:{lang}` | `language:lean` | Primary language |
| `created:{date}` | `created:>2020-01-01` | Account creation date |

### Sort Options
`followers`, `repositories`, `joined`

---

## Date Syntax

GitHub search supports several date formats:

| Format | Example | Meaning |
|--------|---------|---------|
| `>YYYY-MM-DD` | `created:>2024-01-01` | After date |
| `>=YYYY-MM-DD` | `created:>=2024-01-01` | On or after date |
| `<YYYY-MM-DD` | `created:<2024-01-01` | Before date |
| `<=YYYY-MM-DD` | `created:<=2024-01-01` | On or before date |
| `YYYY-MM-DD..YYYY-MM-DD` | `created:2024-01-01..2024-06-30` | Date range |
| `*..YYYY-MM-DD` | `created:*..2024-01-01` | Up to date |
| `YYYY-MM-DD..*` | `created:2024-01-01..*` | From date onward |

Dates can include time: `YYYY-MM-DDTHH:MM:SSZ` (UTC).

## Numeric Range Syntax

| Syntax | Example | Meaning |
|--------|---------|---------|
| `>n` | `stars:>100` | Greater than |
| `>=n` | `forks:>=50` | Greater than or equal |
| `<n` | `size:<1000` | Less than |
| `<=n` | `comments:<=5` | Less than or equal |
| `n..m` | `stars:10..50` | Range inclusive |
| `n..*` | `stars:100..*` | At least n |
| `*..n` | `forks:*..10` | At most n |

## Boolean Operators

| Operator | Example | Meaning |
|----------|---------|---------|
| (space/+) | `lean mathlib` | AND (implicit) |
| `OR` | `label:bug OR label:enhancement` | Either term |
| `NOT` / `-` | `NOT is:archived` / `-label:wontfix` | Exclude |

Maximum 5 AND/OR/NOT operators per query.

## Overcoming the 1,000-Result Limit

Search endpoints return at most 1,000 results. Strategies to collect more:

1. **Date partitioning**: Split query by `created:` date ranges
   ```
   created:2024-01-01..2024-03-31
   created:2024-04-01..2024-06-30
   created:2024-07-01..2024-09-30
   created:2024-10-01..2024-12-31
   ```

2. **Label partitioning**: Run separate queries per label

3. **Author partitioning**: Query per contributor

4. **Use list endpoints instead**: For single-repo data, the list endpoints (`/repos/{owner}/{repo}/issues`) have no result cap — they paginate without limit
