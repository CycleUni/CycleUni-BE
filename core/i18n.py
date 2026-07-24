"""Lightweight content localization helpers.

Convention: models store canonical English values in their regular fields and
carry a `translations` JSONField shaped as
{"zh-TW": {"field": "value", ...}, "<lang>": {...}} for any number of
languages. Requests resolve a language via the `lang` query parameter or the
Accept-Language header; unknown languages fall back to the canonical fields.
"""

DEFAULT_LANGUAGE = 'en'


def normalize_language(lang):
    """Normalize a raw language tag. Any Chinese variant maps to zh-TW (the
    only Chinese localization we ship); other tags pass through unchanged so
    custom languages stored in `translations` keep working."""
    if not lang:
        return DEFAULT_LANGUAGE
    lang = lang.strip().replace('_', '-')
    if not lang:
        return DEFAULT_LANGUAGE
    if lang.lower().startswith('zh'):
        return 'zh-TW'
    return lang


def resolve_language(request):
    """Language for this request: ?lang= wins, then Accept-Language, then English."""
    lang = request.GET.get('lang', '').strip()
    if not lang:
        accept = request.headers.get('Accept-Language', '')
        lang = accept.split(',')[0].split(';')[0].strip()
    return normalize_language(lang)


def pick_translation(translations, lang):
    """The translation dict for `lang`, trying the exact tag then its base
    language (e.g. de-AT → de). Returns {} when nothing matches, so callers
    can fall back to canonical fields."""
    if not isinstance(translations, dict) or not lang:
        return {}
    exact = translations.get(lang)
    if isinstance(exact, dict):
        return exact
    base = lang.split('-')[0]
    for key, value in translations.items():
        if key.split('-')[0] == base and isinstance(value, dict):
            return value
    return {}
