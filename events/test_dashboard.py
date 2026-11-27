import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Speaker, Talk


class CapacityTests(TestCase):
    def setUp(self):
        self.speaker = Speaker.objects.create(name='Casey Lin')

    def event(self, days):
        start = timezone.localdate() + datetime.timedelta(days=10)
        return Event.objects.create(
            name='Capacity Summit', code='CAP',
            start_date=start, end_date=start + datetime.timedelta(days=days - 1),
        )

    def test_empty_programme_counts_only_fixed_breaks(self):
        # 08:30 to 18:00 is 510 minutes; two 20 minute breaks and a 60 minute lunch is 100.
        self.assertEqual(self.event(days=1).capacity, round(100 * 100 / 510, 1))

    def test_accepted_talks_add_their_length(self):
        event = self.event(days=1)
        Talk.objects.create(title='A', event=event, speaker=self.speaker, status='accepted', duration_minutes=25)
        Talk.objects.create(title='B', event=event, speaker=self.speaker, status='accepted', duration_minutes=45)
        self.assertEqual(event.capacity, round(100 * (100 + 70) / 510, 1))

    def test_unaccepted_talks_are_not_booked(self):
        event = self.event(days=1)
        Talk.objects.create(title='Pending', event=event, speaker=self.speaker, duration_minutes=45)
        self.assertEqual(event.capacity, round(100 * 100 / 510, 1))

    def test_fixed_breaks_scale_with_days(self):
        event = self.event(days=2)
        self.assertEqual(event.capacity, round(100 * 200 / 1020, 1))


class DashboardTests(TestCase):
    def setUp(self):
        self.speaker = Speaker.objects.create(name='Dana Ow')
        start = timezone.localdate() + datetime.timedelta(days=10)
        self.next = Event.objects.create(
            name='Next Summit', code='NXT', start_date=start, end_date=start,
        )
        self.later = Event.objects.create(
            name='Later Summit', code='LTR',
            start_date=start + datetime.timedelta(days=100), end_date=start + datetime.timedelta(days=100),
        )
        statuses = ['pending', 'pending', 'accepted', 'rejected']
        for index, status in enumerate(statuses):
            Talk.objects.create(
                title=f'Talk {index}', event=self.next, speaker=self.speaker, status=status,
            )
        Talk.objects.create(title='Later talk', event=self.later, speaker=self.speaker, status='accepted')
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def test_root_is_the_dashboard(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/dashboard.html')

    def test_dashboard_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_counts_are_for_the_next_event_only(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Next event: Next Summit', html)
        spotlight = html[html.index('p-data-spotlight--4-blocks'):html.index('Programme capacity')]
        self.assertIn('<p class="p-heading--1 u-no-margin u-no-padding">4</p>', spotlight)
        self.assertIn('<p class="p-heading--1 u-no-margin u-no-padding">2</p>', spotlight)
        self.assertIn('<p class="p-heading--1 u-no-margin u-no-padding">1</p>', spotlight)
        self.assertNotIn('Later talk', spotlight)

    def test_capacity_is_shown_as_a_progress_bar(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        expected = round(self.next.capacity, 1)
        self.assertIn(f'<progress max="100" value="{expected}"', html)

    def test_no_upcoming_event_message(self):
        Event.objects.all().delete()
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('There is no upcoming event.', html)
