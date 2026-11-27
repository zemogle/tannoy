from django.urls import path

from . import views

urlpatterns = [
    path(
        'programmes/<int:programme_id>/talks/',
        views.programme_items,
        name='programme-items',
    ),
    path(
        'talks/<int:talk_id>/banner/',
        views.api_talk_banner,
        name='api-talk-banner',
    ),
]
