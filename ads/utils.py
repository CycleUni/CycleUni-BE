import logging
import re
from django.core.files.storage import default_storage
from rest_framework import serializers
from core.uploads import r2_client_and_options, storage_key_from_url

logger = logging.getLogger(__name__)

TMP_AD_PREFIX = "tmp/ads"
PERMANENT_AD_PREFIX = "ads"

_TMP_KEY_RE = re.compile(
    rf'^{TMP_AD_PREFIX}/(?:(?P<user_id>\d+)/)?(?P<name>[^/]+)$'
)

def tmp_ad_key_prefix(user_id):
    """Key prefix that uploads by `user_id` are written under."""
    return f"{TMP_AD_PREFIX}/{user_id}/"

def promote_tmp_ad_photos(photo_urls, user_id, request=None):
    if not photo_urls:
        return photo_urls

    client, options = r2_client_and_options()
    updated_urls = []

    for url in photo_urls:
        key = storage_key_from_url(url, request)
        if key is None:
            raise serializers.ValidationError('Invalid photo URL')

        match = _TMP_KEY_RE.match(key)
        if not match:
            updated_urls.append(url)
            continue

        if match.group('user_id') != str(user_id):
            logger.warning("Refused to promote tmp key %s for user %s", key, user_id)
            raise serializers.ValidationError('Photo not owned by this user')

        new_key = f"{PERMANENT_AD_PREFIX}/{match.group('name')}"
        try:
            if client and options:
                client.copy_object(
                    Bucket=options["bucket_name"],
                    CopySource={'Bucket': options["bucket_name"], 'Key': key},
                    Key=new_key,
                )
                client.delete_object(Bucket=options["bucket_name"], Key=key)
            else:
                if not default_storage.exists(key):
                    raise FileNotFoundError(key)
                with default_storage.open(key, 'rb') as f:
                    new_key = default_storage.save(new_key, f)
                default_storage.delete(key)
        except Exception:
            logger.exception("Failed to promote tmp ad photo %s", key)
            raise serializers.ValidationError('Failed to promote ad photo')

        updated_urls.append(url.replace(key, new_key, 1))

    return updated_urls
