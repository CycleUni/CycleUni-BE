import hashlib
import json
import logging

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

def clean_and_validate_isbn(isbn_str):
    if not isbn_str:
        return None
    raw_isbn = str(isbn_str).replace('-', '').replace(' ', '').upper()
    if raw_isbn.isdigit() or (len(raw_isbn) == 10 and raw_isbn[:-1].isdigit() and raw_isbn[-1] in '0123456789X'):
        if len(raw_isbn) in (10, 13):
            return raw_isbn
    return None
from django.core.cache import cache

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPEN_LIBRARY_ISBN_URL = "https://openlibrary.org/api/books"
OPEN_LIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"


class GoogleBooksRateLimited(Exception):
    """Raised when Google Books responds 429. Callers (search/catalog views)
    catch this to automatically fall back to the Open Library API instead of
    surfacing an error to the user."""

# Sentinel value used to mark a cached "confirmed not found" result. This is
# stored as JSON, so it must be distinguishable from `json.dumps(None)` /
# `json.dumps([])`, which would otherwise look like a plain cache miss once
# decoded (both are falsy in Python).
_NOT_FOUND_SENTINEL = "__gb_not_found__"

# Negative-result cache TTL: shorter than the positive-hit TTLs so a book
# later added to Google's catalog is eventually rediscovered, while repeated
# no-op queries in the short term don't re-hit the API. This is distinct from
# _GOOGLE_RATE_LIMIT_TTL below — one caches "we confirmed this book doesn't
# exist", the other caches "Google is rate-limiting us right now" — very
# different facts with very different appropriate lifetimes.
_NOT_FOUND_CACHE_TTL = 3600  # 1 hour

# When Google Books responds 429, remember it for a short window so the next
# request (for any ISBN/query) skips straight to the fallback instead of
# immediately re-hitting Google and getting rate-limited again — 429s are a
# property of the whole API key/quota, not of any one query, so this is a
# single global flag rather than per-query. Short on purpose: a rate limit
# is usually transient, so we want to start trying Google again soon rather
# than staying on the fallback for as long as a real "not found" result.
_GOOGLE_RATE_LIMIT_CACHE_KEY = "gb:rate_limited"
_GOOGLE_RATE_LIMIT_TTL = 10  # seconds


def _safe_cache_get(key):
    """cache.get() that degrades to a plain cache miss on a Redis hiccup
    (timeout, connection reset) instead of crashing the whole request — this
    cache is purely a performance optimization over Google/Open Library, not
    a correctness requirement, so callers should keep working live when it's
    unavailable rather than surfacing a 500."""
    try:
        return cache.get(key)
    except Exception:
        logger.exception("Cache backend error on get(%s); treating as a miss", key)
        return None


def _safe_cache_set(key, value, timeout):
    """cache.set() that swallows a Redis hiccup instead of crashing the
    request — see `_safe_cache_get`. Losing this write just means the next
    lookup re-fetches live, which is the same outcome as a cold cache."""
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        logger.exception("Cache backend error on set(%s); continuing without caching", key)


def describe_source(engine, cache_hit):
    """Human-readable provenance label for developer monitoring only (not
    shown in the UI, not the persisted Book.source enum) — distinguishes a
    live external-API call from a cache hit, per engine."""
    label = 'google books' if engine == 'google' else 'Open Library API'
    return f"{label} cache" if cache_hit else label


def _google_books_params(query):
    params = {'q': query}
    api_key = settings.GOOGLE_BOOKS_API_KEY
    if api_key and not api_key.startswith('dev-only'):
        params['key'] = api_key
    return params


def get_google_books_by_isbn(isbn, _meta=None):
    cache_key = f"gb:isbn:{isbn}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for ISBN: %s", isbn)
            return None
        logger.debug("Cache hit for ISBN: %s", isbn)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    if _safe_cache_get(_GOOGLE_RATE_LIMIT_CACHE_KEY):
        logger.debug("Google Books known rate-limited (cached); skipping live call for isbn=%s", isbn)
        raise GoogleBooksRateLimited(f"cached rate-limit, isbn={isbn}")

    params = _google_books_params(f"isbn:{isbn}")
    debug_params = params.copy()
    if 'key' in debug_params:
        debug_params['key'] = '***'
    logger.debug("Google Books ISBN lookup: isbn=%s params=%s", isbn, debug_params)

    try:
        response = requests.get(
            GOOGLE_BOOKS_API_URL,
            params=params,
            timeout=5,
        )
        logger.debug("Google Books ISBN lookup response status=%s", response.status_code)
        if response.status_code == 429:
            logger.warning("Google Books rate-limited (429) for ISBN %s", isbn)
            _safe_cache_set(_GOOGLE_RATE_LIMIT_CACHE_KEY, True, _GOOGLE_RATE_LIMIT_TTL)
            raise GoogleBooksRateLimited(f"429 for isbn={isbn}")
        if response.status_code != 200:
            logger.debug("Google Books ISBN lookup response body: %s", response.text)
        if response.status_code == 200:
            data = response.json()
            if data.get('totalItems', 0) > 0:
                item = data['items'][0]['volumeInfo']
                result = {
                    'title': item.get('title', ''),
                    'authors': ', '.join(item.get('authors', [])),
                    'publisher': item.get('publisher', ''),
                    'published_date': item.get('publishedDate', ''),
                    'cover_url': item.get('imageLinks', {}).get('thumbnail', '').replace('http:', 'https:'),
                    'isbn': isbn,
                }
                # cache for 30 days
                _safe_cache_set(cache_key, json.dumps(result), 86400 * 30)
                return result
            # Confirmed zero results: negatively cache for a shorter TTL.
            _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Google Books ISBN lookup failed for %s", isbn)
    return None


def search_google_books(query, _meta=None):
    query_hash = hashlib.sha1(query.encode('utf-8')).hexdigest()
    cache_key = f"gb:q:{query_hash}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for query: %r", query)
            return []
        logger.debug("Cache hit for query: %r", query)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    if _safe_cache_get(_GOOGLE_RATE_LIMIT_CACHE_KEY):
        logger.debug("Google Books known rate-limited (cached); skipping live call for query=%r", query)
        raise GoogleBooksRateLimited(f"cached rate-limit, query={query!r}")

    params = _google_books_params(query)
    debug_params = params.copy()
    if 'key' in debug_params:
        debug_params['key'] = '***'
    logger.debug("Google Books search: query=%r params=%s", query, debug_params)

    try:
        response = requests.get(
            GOOGLE_BOOKS_API_URL,
            params=params,
            timeout=5,
        )
        logger.debug("Google Books search response status=%s", response.status_code)
        if response.status_code == 429:
            logger.warning("Google Books rate-limited (429) for query %r", query)
            _safe_cache_set(_GOOGLE_RATE_LIMIT_CACHE_KEY, True, _GOOGLE_RATE_LIMIT_TTL)
            raise GoogleBooksRateLimited(f"429 for query={query!r}")
        if response.status_code != 200:
            logger.debug("Google Books search response body: %s", response.text)
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            if not items:
                # Confirmed zero results: negatively cache for a shorter TTL.
                _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
                return []
            results = []
            for item in items[:10]:
                info = item.get('volumeInfo', {})
                results.append({
                    'title': info.get('title', ''),
                    'authors': ', '.join(info.get('authors', [])),
                    'publisher': info.get('publisher', ''),
                    'published_date': info.get('publishedDate', ''),
                    'cover_url': info.get('imageLinks', {}).get('thumbnail', '').replace('http:', 'https:'),
                    'isbn': next((id['identifier'] for id in info.get('industryIdentifiers', []) if id['type'] == 'ISBN_13'), None)
                })
            # cache for 24 hours
            _safe_cache_set(cache_key, json.dumps(results), 86400)
            return results
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Google Books search failed for query %r", query)
    return []


def get_open_library_book_by_isbn(isbn, _meta=None):
    """Open Library equivalent of `get_google_books_by_isbn`, used as the
    automatic fallback when Google Books rate-limits us (429), or when the
    caller explicitly picks Open Library as the search engine."""
    cache_key = f"ol:isbn:{isbn}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for Open Library ISBN: %s", isbn)
            return None
        logger.debug("Cache hit for Open Library ISBN: %s", isbn)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    logger.debug("Open Library ISBN lookup: isbn=%s", isbn)

    try:
        response = requests.get(
            OPEN_LIBRARY_ISBN_URL,
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=5,
        )
        logger.debug("Open Library ISBN lookup response status=%s", response.status_code)
        if response.status_code == 200:
            data = response.json()
            entry = data.get(f"ISBN:{isbn}")
            if not entry:
                _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
                return None
            result = {
                'title': entry.get('title', ''),
                'authors': ', '.join(a.get('name', '') for a in entry.get('authors', [])),
                'publisher': ', '.join(p.get('name', '') for p in entry.get('publishers', [])),
                'published_date': entry.get('publish_date', ''),
                'cover_url': entry.get('cover', {}).get('medium', ''),
                'isbn': isbn,
            }
            _safe_cache_set(cache_key, json.dumps(result), 86400 * 30)
            return result
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Open Library ISBN lookup failed for %s", isbn)
    return None


def search_open_library_books(query, _meta=None):
    """Open Library equivalent of `search_google_books`."""
    query_hash = hashlib.sha1(query.encode('utf-8')).hexdigest()
    cache_key = f"ol:q:{query_hash}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for Open Library query: %r", query)
            return []
        logger.debug("Cache hit for Open Library query: %r", query)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    logger.debug("Open Library search: query=%r", query)

    try:
        response = requests.get(
            OPEN_LIBRARY_SEARCH_URL,
            # Open Library's search.json omits `isbn` by default; without it,
            # every doc below would have isbn=None and get silently dropped
            # by the dedup step in search/views.py (it only keeps items with
            # a truthy isbn), so search results would never actually appear.
            params={"q": query, "limit": 10, "fields": "title,author_name,publisher,first_publish_year,cover_i,isbn"},
            timeout=5,
        )
        logger.debug("Open Library search response status=%s", response.status_code)
        if response.status_code == 200:
            data = response.json()
            docs = data.get('docs', [])[:10]
            if not docs:
                _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
                return []
            results = []
            for doc in docs:
                cover_id = doc.get('cover_i')
                isbns = doc.get('isbn') or []
                # A work can list dozens of ISBNs across editions/formats,
                # mixing ISBN-10 and ISBN-13; prefer an ISBN-13 (matches what
                # Google Books gives us and what the rest of the app expects),
                # falling back to whatever's first if none is 13 digits.
                isbn13 = next((i for i in isbns if len(i) == 13), None) or (isbns[0] if isbns else None)
                results.append({
                    'title': doc.get('title', ''),
                    'authors': ', '.join(doc.get('author_name', [])),
                    'publisher': (doc.get('publisher') or [''])[0],
                    'published_date': str(doc.get('first_publish_year', '')),
                    'cover_url': OPEN_LIBRARY_COVER_URL.format(cover_id=cover_id) if cover_id else '',
                    'isbn': isbn13,
                })
            # cache for 24 hours, matching search_google_books
            _safe_cache_set(cache_key, json.dumps(results), 86400)
            return results
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Open Library search failed for query %r", query)
    return []
