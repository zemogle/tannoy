from django.urls import path

from . import views

urlpatterns = [
    path(
        'programmes/<int:programme_id>/talks/',
        views.programme_items,
        name='programme-items',
    ),
]
