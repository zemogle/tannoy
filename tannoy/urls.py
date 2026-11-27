"""
URL configuration for tannoy project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from events import views as event_views

urlpatterns = [
    path('', event_views.dashboard, name='dashboard'),
    path('submissions/', event_views.submissions, name='submissions'),
    path('submissions/<int:talk_id>/', event_views.TalkDetailView.as_view(), name='talk-detail'),
    path('events/', event_views.events, name='events'),
    path('events/<int:event_id>/', event_views.event_detail, name='event-detail'),
    path('events/<int:event_id>/programme/', event_views.programme_builder, name='programme-builder'),
    path('admin/', admin.site.urls),
    path('api/', include('events.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
