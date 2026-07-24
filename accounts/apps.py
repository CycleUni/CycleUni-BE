import logging

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        post_migrate.connect(_create_default_superuser, sender=self)


def _create_default_superuser(sender, **kwargs):
    """Bootstraps an initial admin account on a fresh production deployment
    that has no superuser yet — for when there's no interactive terminal to
    run `createsuperuser` against (e.g. a one-shot deploy migration step).

    DEBUG=False only: local/dev databases already have one from a fixture
    or manual createsuperuser, and this must never run against the DEBUG=True
    databases pytest spins up per test run (post_migrate also fires there).

    Off by default — both DEFAULT_SUPERUSER_EMAIL and DEFAULT_SUPERUSER_PASSWORD
    must be set, or neither; a half-set pair fails loud rather than silently
    skipping or crashing on a None password, matching this project's existing
    R2/Mailjet "all or nothing" config guard convention.
    """
    from django.conf import settings

    if settings.DEBUG:
        return

    from django.contrib.auth import get_user_model
    User = get_user_model()
    if User.objects.filter(is_superuser=True).exists():
        return

    email = settings.DEFAULT_SUPERUSER_EMAIL
    password = settings.DEFAULT_SUPERUSER_PASSWORD
    if not email and not password:
        return
    if not email or not password:
        raise ImproperlyConfigured(
            "DEFAULT_SUPERUSER_EMAIL and DEFAULT_SUPERUSER_PASSWORD must both be "
            "set to bootstrap an initial superuser, or neither to skip it."
        )

    User.objects.create_superuser(
        email=email,
        first_name=settings.DEFAULT_SUPERUSER_FIRST_NAME,
        last_name=settings.DEFAULT_SUPERUSER_LAST_NAME,
        password=password,
    )
    logger.info("Bootstrapped initial superuser %s", email)
