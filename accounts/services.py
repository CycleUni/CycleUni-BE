import uuid
from datetime import timedelta
from django.core.cache import cache
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

# Refresh token TTL defaults to 14 days
REFRESH_TOKEN_LIFETIME = timedelta(days=14)

# Rotation grace period: after a refresh token is rotated, reusing the old one
# within this window (concurrent refresh from multiple tabs) returns the same
# new token pair instead of kicking the user out.
REFRESH_ROTATION_GRACE = timedelta(seconds=60)

def issue_tokens(user):
    """
    Issue a token pair and record the refresh token JTI in the cache
    (Redis in production, LocMemCache in development) as a whitelist.
    """
    refresh = RefreshToken.for_user(user)
    refresh.set_exp(lifetime=REFRESH_TOKEN_LIFETIME)

    jti = refresh['jti']
    user_id = user.id

    # jwt:rt:{jti} = user_id, expiring with the token
    cache_key = f"jwt:rt:{jti}"
    timeout = int(REFRESH_TOKEN_LIFETIME.total_seconds())
    cache.set(cache_key, user_id, timeout=timeout)

    # Maintain the per-user JTI collection (a list, since LocMemCache has no set type)
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = cache.get(user_set_key, [])
    if jti not in current_tokens:
        current_tokens.append(jti)
    cache.set(user_set_key, current_tokens, timeout=timeout)

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }

def get_grace_tokens(jti, user_id):
    """
    If the old JTI is within the rotation grace period (value is a dict),
    return the token pair issued when it was rotated; otherwise return None.
    """
    stored = cache.get(f"jwt:rt:{jti}")
    if isinstance(stored, dict) and stored.get('user_id') == user_id:
        return stored.get('tokens')
    return None


def store_grace_tokens(jti, user_id, tokens):
    """
    After rotation, keep the new token pair under the old JTI for a short grace
    period so other tabs refreshing concurrently receive the same pair instead
    of being logged out.
    """
    timeout = int(REFRESH_ROTATION_GRACE.total_seconds())
    cache.set(f"jwt:rt:{jti}", {'user_id': user_id, 'tokens': tokens}, timeout=timeout)


def verify_and_revoke_refresh_token(jti, user_id):
    """
    Check that the refresh token is whitelisted; if so, revoke it so a new
    pair can be issued.
    """
    cache_key = f"jwt:rt:{jti}"
    stored_user_id = cache.get(cache_key)
    if stored_user_id != user_id:
        return False

    # Revoke the old token
    cache.delete(cache_key)

    # Remove the JTI from the user's token collection
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = cache.get(user_set_key, [])
    if jti in current_tokens:
        current_tokens.remove(jti)
        cache.set(user_set_key, current_tokens)

    return True

def revoke_all_tokens_for_user(user_id):
    """
    Revoke every refresh token for the user across all devices.
    """
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = cache.get(user_set_key, [])
    for jti in current_tokens:
        cache.delete(f"jwt:rt:{jti}")
    cache.delete(user_set_key)

def email_already_used(email, excluding_user_id):
    """
    Check if the email is already in use by another user's email or edu_email field.
    """
    User = get_user_model()
    return (
        User.objects.filter(email=email).exclude(id=excluding_user_id).exists()
        or User.objects.filter(edu_email=email).exclude(id=excluding_user_id).exists()
    )
