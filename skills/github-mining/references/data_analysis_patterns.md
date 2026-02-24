# Data Analysis Patterns for GitHub Mining

Common analysis patterns for empirical software engineering research using GitHub data.

---

## 1. Growth Trajectory Analysis

Track project evolution through quantitative metrics over time.

### Metrics to Compute
- **Commits per month/quarter/year** — overall project velocity
- **Distinct contributors per period** — community size evolution
- **PRs opened and merged per month** — contribution throughput
- **Issues opened and closed per month** — workload and responsiveness
- **PR merge rate** — `merged_PRs / (merged_PRs + closed_unmerged_PRs)`
- **Time to merge** — `merged_at - created_at` for each PR
- **Time to close** — `closed_at - created_at` for each issue
- **Year-over-year growth rate** — `(current_year - previous_year) / previous_year`

### Implementation Sketch
```python
from collections import Counter
from datetime import datetime

def commits_per_month(commits):
    """Aggregate commits into monthly buckets."""
    monthly = Counter()
    for c in commits:
        date = datetime.fromisoformat(c["commit"]["author"]["date"].replace("Z", "+00:00"))
        key = f"{date.year}-{date.month:02d}"
        monthly[key] += 1
    return dict(sorted(monthly.items()))

def contributors_per_month(commits):
    """Count distinct contributors per month."""
    monthly = {}
    for c in commits:
        date = datetime.fromisoformat(c["commit"]["author"]["date"].replace("Z", "+00:00"))
        key = f"{date.year}-{date.month:02d}"
        author = c.get("author", {})
        login = author.get("login", c["commit"]["author"]["email"]) if author else c["commit"]["author"]["email"]
        monthly.setdefault(key, set()).add(login)
    return {k: len(v) for k, v in sorted(monthly.items())}
```

### Visualization Types
- **Line chart**: Commit count over time (monthly)
- **Stacked area chart**: Commits by top-N contributors over time
- **Bar chart**: Year-over-year growth rates
- **Heatmap**: Commits per weekday/hour (from punch card data)

---

## 2. Contributor Segmentation

Classify contributors by tenure, activity level, and role.

### Tenure-Based Segmentation
```python
def segment_contributors(commits):
    """Segment contributors into tenure cohorts."""
    first_commit = {}
    last_commit = {}
    commit_count = Counter()

    for c in commits:
        author = c.get("author", {})
        login = author.get("login") if author else None
        if not login:
            continue
        date = datetime.fromisoformat(c["commit"]["author"]["date"].replace("Z", "+00:00"))

        if login not in first_commit or date < first_commit[login]:
            first_commit[login] = date
        if login not in last_commit or date > last_commit[login]:
            last_commit[login] = date
        commit_count[login] += 1

    segments = {"newcomer": [], "regular": [], "core": []}
    for login in first_commit:
        tenure_days = (last_commit[login] - first_commit[login]).days
        count = commit_count[login]

        if tenure_days < 30:
            segments["newcomer"].append(login)
        elif count < 50:
            segments["regular"].append(login)
        else:
            segments["core"].append(login)

    return segments, first_commit, commit_count
```

### Activity-Level Classification
| Category | Criteria | Typical Role |
|----------|----------|-------------|
| Core | 100+ commits, 6+ months active | Maintainer, lead contributor |
| Regular | 10-99 commits, 1+ months active | Active community member |
| Casual | 2-9 commits | Drive-by contributor |
| One-shot | 1 commit | Single contribution |

### Newcomer Friction Analysis
1. Identify each contributor's first commit date
2. For issues opened within 30 days of first commit → classify as "newcomer issue"
3. Compare newcomer vs established contributor issues by:
   - Label distribution (bug vs enhancement vs question)
   - Time to first response
   - Time to close
   - Number of comments before resolution
   - Resolution rate (closed vs still open)

---

## 3. Issue Taxonomy (Open Coding)

Build a grounded taxonomy of issue types from issue content.

### Data Preparation
```python
def prepare_issues_for_coding(issues, max_comments=5):
    """Extract coding units from issues."""
    coding_units = []
    for issue in issues:
        # Skip pull requests
        if "pull_request" in issue:
            continue

        unit = {
            "number": issue["number"],
            "title": issue["title"],
            "body": (issue.get("body") or "")[:2000],  # Truncate long bodies
            "labels": [l["name"] for l in issue.get("labels", [])],
            "state": issue["state"],
            "creator": issue["user"]["login"],
            "created_at": issue["created_at"],
            "comment_count": issue.get("comments", 0),
        }
        coding_units.append(unit)
    return coding_units
```

### Suggested Taxonomy Categories (for formal proof/software projects)

| Category | Description | Example Indicators |
|----------|-------------|-------------------|
| **Technical Bug** | Unexpected behavior, crashes, type errors | "error", "crash", "fails", "unexpected" |
| **Missing Feature** | Requested functionality not yet implemented | "add", "support", "implement", "missing" |
| **Documentation** | Missing, unclear, or incorrect docs | "doc", "example", "clarify", "README" |
| **Performance** | Slow compilation, memory issues, timeouts | "slow", "timeout", "memory", "performance" |
| **Build/CI** | Build failures, CI configuration, tooling | "build", "CI", "compile", "toolchain" |
| **API/Interface** | Public interface design, breaking changes | "API", "breaking", "deprecate", "rename" |
| **Dependency** | Issues with dependencies or version conflicts | "dependency", "version", "upgrade", "conflict" |
| **Refactoring** | Code structure improvements, cleanup | "refactor", "clean up", "reorganize", "move" |
| **Question** | User seeking help or clarification | "how to", "question", "help", "??" |
| **Meta/Process** | Project governance, process, policy | "RFC", "policy", "process", "decision" |

### Inter-Rater Reliability
- Two independent coders apply taxonomy to a random sample (100+ issues)
- Compute Cohen's kappa: `kappa = (observed_agreement - expected_agreement) / (1 - expected_agreement)`
- Target: kappa >= 0.70 (substantial agreement)
- Resolve disagreements through discussion and refine coding scheme

---

## 4. Domain Coverage Mapping

Map repository directory structure to subject domains.

### Directory-to-Domain Mapping
```python
def map_directories_to_domains(file_tree, domain_mapping):
    """Map file tree entries to subject domains.

    Args:
        file_tree: List of tree entries from GitHub API
        domain_mapping: Dict mapping top-level dir prefixes to domain names
            e.g., {"Mathlib/Algebra": "Algebra", "Mathlib/Analysis": "Analysis"}

    Returns:
        Dict of domain -> {files: int, total_size: int, paths: list}
    """
    coverage = {}
    for entry in file_tree:
        if entry["type"] != "blob":
            continue

        domain = "Other"
        for prefix, name in sorted(domain_mapping.items(), key=lambda x: -len(x[0])):
            if entry["path"].startswith(prefix):
                domain = name
                break

        coverage.setdefault(domain, {"files": 0, "total_size": 0, "paths": []})
        coverage[domain]["files"] += 1
        coverage[domain]["total_size"] += entry.get("size", 0)
        coverage[domain]["paths"].append(entry["path"])

    return coverage
```

### Activity per Domain
Cross-reference domains with commit activity:
```python
def domain_commit_activity(commits, domain_mapping):
    """Count commits touching files in each domain.

    Requires per-commit file data (from individual commit GETs).
    For efficiency, sample commits or use PR files.
    """
    domain_commits = Counter()
    for commit in commits:
        for file_info in commit.get("files", []):
            path = file_info["filename"]
            for prefix, domain in domain_mapping.items():
                if path.startswith(prefix):
                    domain_commits[domain] += 1
                    break
    return dict(domain_commits)
```

### Coverage Gap Identification
1. Compute files per domain → identify domains with disproportionately few files
2. Compute commits per domain per year → identify stagnant domains
3. Compute issues per domain (match file paths in issue text/labels) → identify domains with high difficulty
4. Compare against external taxonomy (e.g., Mathematics Subject Classification) → identify absent domains

---

## 5. Collaboration Network Analysis

Build contributor interaction graphs from shared activity.

### Co-Authorship Network
```python
from collections import defaultdict
from itertools import combinations

def build_coauthorship_network(pull_requests):
    """Build co-authorship edges from PR author + reviewers."""
    edges = Counter()
    for pr in pull_requests:
        participants = set()
        participants.add(pr["user"]["login"])
        for reviewer in pr.get("requested_reviewers", []):
            participants.add(reviewer["login"])
        for a, b in combinations(sorted(participants), 2):
            edges[(a, b)] += 1
    return edges
```

### Co-Participation Network
```python
def build_coparticipation_network(issues_with_comments):
    """Build edges from users commenting on the same issues."""
    edges = Counter()
    for issue in issues_with_comments:
        participants = {issue["user"]["login"]}
        for comment in issue.get("_comments", []):
            participants.add(comment["user"]["login"])
        for a, b in combinations(sorted(participants), 2):
            edges[(a, b)] += 1
    return edges
```

### Network Metrics
- **Degree centrality**: Most connected contributors
- **Betweenness centrality**: Bridge contributors connecting subcommunities
- **Clustering coefficient**: Community cohesion
- **Connected components**: Subcommunity identification

---

## 6. PR Lifecycle Analysis

Analyze the full lifecycle of pull requests.

### Key Metrics
```python
def pr_lifecycle_stats(prs):
    """Compute PR lifecycle statistics."""
    stats = {
        "time_to_merge": [],    # For merged PRs
        "time_to_close": [],    # For closed-unmerged PRs
        "merge_rate": 0,
        "files_changed": [],
        "additions": [],
        "deletions": [],
    }
    merged = 0
    closed_unmerged = 0

    for pr in prs:
        created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))

        if pr.get("merged_at"):
            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            stats["time_to_merge"].append((merged_at - created).total_seconds() / 3600)
            merged += 1
        elif pr["state"] == "closed":
            closed_at = datetime.fromisoformat(pr["closed_at"].replace("Z", "+00:00"))
            stats["time_to_close"].append((closed_at - created).total_seconds() / 3600)
            closed_unmerged += 1

    total = merged + closed_unmerged
    stats["merge_rate"] = merged / total if total > 0 else 0
    return stats
```

---

## 7. Temporal Pattern Detection

Identify seasonal, weekly, or event-driven patterns.

### Weekly/Hourly Patterns
Use the punch card endpoint data (`/stats/punch_card`) to visualize:
- Which days of the week have most activity
- Which hours are most active (by timezone)
- Whether weekends show contributor activity

### Event Detection
Look for activity spikes correlated with:
- Major releases (tag dates)
- Conference deadlines
- Breaking changes or migrations
- External announcements
- School/academic calendar effects

### Moving Averages
```python
def moving_average(values, window=4):
    """Compute simple moving average for smoothing time series."""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        result.append(sum(values[start:i+1]) / (i - start + 1))
    return result
```

---

## 8. Output Formats

### Summary Statistics CSV
```csv
metric,value
total_commits,45230
total_contributors,342
total_issues,8721
total_prs,12450
merge_rate,0.87
median_time_to_merge_hours,24.5
active_domains,15
```

### Monthly Time Series CSV
```csv
month,commits,contributors,issues_opened,issues_closed,prs_merged
2023-01,234,45,67,52,89
2023-02,256,48,73,61,95
```

### Domain Coverage Matrix
```csv
domain,files,total_size_kb,commits_2024,prs_2024,issues_2024
Algebra,1250,3400,890,234,45
Analysis,980,2800,720,189,38
Topology,450,1200,210,67,22
```
