#!/usr/bin/env python3
"""
arXiv Search Client with Caching and Rate Limiting

Provides a robust interface to arXiv API for literature surveys.
Implements caching to avoid redundant API calls and rate limiting to respect arXiv's 3 req/sec limit.
"""

import arxiv
import json
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class ArxivSearchClient:
    """Client for searching arXiv with caching and rate limiting."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Initialize arXiv search client.

        Args:
            cache_dir: Directory for caching results. Defaults to ~/.cache/literature-survey/arxiv
        """
        self.cache_dir = cache_dir or Path.home() / ".cache" / "literature-survey" / "arxiv"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit_delay = 0.34  # ~3 requests/second (arXiv limit)
        self.last_request_time = 0

    def _rate_limit(self):
        """Enforce rate limiting."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_request_time = time.time()

    def _cache_key(self, query: str, category: Optional[str], max_results: int) -> str:
        """Generate cache key from search parameters."""
        key_parts = [query]
        if category:
            key_parts.append(f"cat:{category}")
        key_parts.append(f"n:{max_results}")
        return "_".join(key_parts).replace(" ", "_").replace(":", "-")

    def _load_cache(self, cache_key: str) -> Optional[List[Dict]]:
        """Load cached results if available and recent (< 7 days)."""
        cache_file = self.cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None

        # Check cache age
        cache_age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if cache_age_days > 7:
            return None

        with open(cache_file, "r") as f:
            return json.load(f)

    def _save_cache(self, cache_key: str, results: List[Dict]):
        """Save results to cache."""
        cache_file = self.cache_dir / f"{cache_key}.json"
        with open(cache_file, "w") as f:
            json.dump(results, f, indent=2)

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        max_results: int = 100,
        sort_by: arxiv.SortCriterion = arxiv.SortCriterion.Relevance,
        use_cache: bool = True,
    ) -> List[Dict]:
        """
        Search arXiv for papers.

        Args:
            query: Search query string
            category: arXiv category (e.g., "cs.LG", "cs.AI")
            max_results: Maximum number of results to return
            sort_by: Sort criterion (Relevance, LastUpdatedDate, SubmittedDate)
            use_cache: Whether to use cached results

        Returns:
            List of paper dictionaries with metadata
        """
        # Build full query
        full_query = query
        if category:
            full_query = f"cat:{category} AND {query}"

        # Check cache
        cache_key = self._cache_key(query, category, max_results)
        if use_cache:
            cached = self._load_cache(cache_key)
            if cached:
                print(f"[Cache hit] Loaded {len(cached)} papers from cache")
                return cached

        # Execute search with rate limiting
        self._rate_limit()
        search = arxiv.Search(
            query=full_query,
            max_results=max_results,
            sort_by=sort_by
        )

        papers = []
        print(f"[arXiv] Searching for: {full_query}")

        for result in search.results():
            paper = {
                "title": result.title,
                "authors": [author.name for author in result.authors],
                "abstract": result.summary,
                "arxiv_id": result.entry_id.split("/")[-1].replace("v", "").split("v")[0],
                "published": result.published.isoformat(),
                "updated": result.updated.isoformat(),
                "pdf_url": result.pdf_url,
                "categories": result.categories,
                "primary_category": result.primary_category,
                "comment": result.comment,
                "journal_ref": result.journal_ref,
                "doi": result.doi,
            }
            papers.append(paper)

        print(f"[arXiv] Found {len(papers)} papers")

        # Save to cache
        if use_cache:
            self._save_cache(cache_key, papers)

        return papers

    def search_by_date_range(
        self,
        query: str,
        start_date: str,
        end_date: str,
        category: Optional[str] = None,
        max_results: int = 100,
    ) -> List[Dict]:
        """
        Search arXiv within a date range.

        Args:
            query: Search query
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            category: arXiv category
            max_results: Maximum results

        Returns:
            List of papers within date range
        """
        # Search and filter by date
        papers = self.search(query, category, max_results * 2)  # Over-fetch

        filtered = []
        for paper in papers:
            pub_date = datetime.fromisoformat(paper["published"]).date()
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()

            if start <= pub_date <= end:
                filtered.append(paper)

            if len(filtered) >= max_results:
                break

        return filtered


def main():
    """Example usage."""
    import argparse

    parser = argparse.ArgumentParser(description="Search arXiv for papers")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--category", help="arXiv category (e.g., cs.LG)")
    parser.add_argument("--max-results", type=int, default=50, help="Max results")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache")

    args = parser.parse_args()

    client = ArxivSearchClient()

    if args.start_date and args.end_date:
        papers = client.search_by_date_range(
            args.query,
            args.start_date,
            args.end_date,
            args.category,
            args.max_results,
        )
    else:
        papers = client.search(
            args.query,
            args.category,
            args.max_results,
            use_cache=not args.no_cache,
        )

    # Print results
    print(f"\n{'='*80}")
    print(f"Found {len(papers)} papers")
    print(f"{'='*80}\n")

    for i, paper in enumerate(papers[:10], 1):
        print(f"{i}. {paper['title']}")
        print(f"   Authors: {', '.join(paper['authors'][:3])}")
        print(f"   arXiv: {paper['arxiv_id']}")
        print(f"   Published: {paper['published'][:10]}")
        print()


if __name__ == "__main__":
    main()
