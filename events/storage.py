from functools import cache

from django.conf import settings
from django.core.files.storage import Storage, default_storage
from django.utils.deconstruct import deconstructible
from storages.backends.s3 import S3Storage


class HeadshotS3Storage(S3Storage):
    """S3 backend for speaker headshots. Bucket and region come from settings."""


@cache
def _s3_backend():
    return HeadshotS3Storage()


def _active_backend():
    if settings.USE_S3_HEADSHOTS:
        return _s3_backend()
    return default_storage


@deconstructible
class HeadshotStorage(Storage):
    """
    Storage used by Speaker.headshot. Each operation is delegated to S3 when
    USE_S3_HEADSHOTS is enabled, and to the default (local) storage otherwise.
    The choice is made per call, so it follows the current settings.
    """

    def _open(self, name, mode='rb'):
        return _active_backend()._open(name, mode)

    def _save(self, name, content):
        return _active_backend()._save(name, content)

    def get_available_name(self, name, max_length=None):
        return _active_backend().get_available_name(name, max_length=max_length)

    def delete(self, name):
        return _active_backend().delete(name)

    def exists(self, name):
        return _active_backend().exists(name)

    def size(self, name):
        return _active_backend().size(name)

    def url(self, name):
        return _active_backend().url(name)
