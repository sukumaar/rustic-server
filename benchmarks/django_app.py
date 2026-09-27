"""Minimal WSGI app: no middleware, database, or per-request JSON encoding."""
from django.conf import settings

settings.configure(
    DEBUG=False,
    SECRET_KEY="local-benchmark-only",
    ROOT_URLCONF=__name__,
    ALLOWED_HOSTS=["127.0.0.1"],
    MIDDLEWARE=[],
    INSTALLED_APPS=[],
)

from django.core.wsgi import get_wsgi_application
from django.http import HttpResponse
from django.urls import path

BODY = b'{"status":"ok"}'


def health(request):
    return HttpResponse(BODY, content_type="application/json")


urlpatterns = [path("health", health)]
app = get_wsgi_application()
