import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Event, Programme, Speaker, Talk


class EventPagesTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.past = Event.objects.create(
            name='Past Summit', code='P1',
            start_date=today - datetime.timedelta(days=60), end_date=today - datetime.timedelta(days=59),
        )
        self.next = Event.objects.create(
            name='Next Summit Event', code='N1',
            start_date=today + datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=11),
        )
        self.later = Event.objects.create(
            name='Later Summit', code='L1',
            start_date=today + datetime.timedelta(days=200), end_date=today + datetime.timedelta(days=201),
        )
        speaker = Speaker.objects.create(name='Alex Morgan')
        Talk.objects.create(title='Opening Talk', event=self.next, speaker=speaker, status='accepted')
        Programme.objects.create(name='Main Stage', event=self.next, day=1)
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def test_events_page_requires_staff(self):
        self.client.logout()
        response = self.client.get(reverse('events'))
        self.assertEqual(response.status_code, 302)

    def test_next_event_is_in_its_own_table(self):
        html = self.client.get(reverse('events')).content.decode()
        next_table = html[html.index('aria-label="Next event"'):]
        next_table = next_table[:next_table.index('</table>')]
        self.assertIn('Next Summit Event', next_table)
        self.assertNotIn('Later Summit', next_table)

        all_table = html[html.index('aria-label="All events"'):]
        all_table = all_table[:all_table.index('</table>')]
        self.assertNotIn('Next Summit Event', all_table)
        self.assertIn('Past Summit', all_table)
        self.assertIn('Later Summit', all_table)

    def test_other_events_are_date_sorted(self):
        html = self.client.get(reverse('events')).content.decode()
        all_table = html[html.index('aria-label="All events"'):]
        self.assertLess(all_table.index('Past Summit'), all_table.index('Later Summit'))

    def test_rows_link_to_event_detail(self):
        html = self.client.get(reverse('events')).content.decode()
        self.assertIn(f'href="{reverse("event-detail", args=[self.next.pk])}"', html)

    def test_event_detail_shows_talks_and_programmes(self):
        response = self.client.get(reverse('event-detail', args=[self.next.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Opening Talk')
        self.assertContains(response, 'Alex Morgan')
        self.assertContains(response, 'Main Stage')
        self.assertContains(response, 'Next Summit')

    def test_event_detail_hides_next_chip_for_other_events(self):
        response = self.client.get(reverse('event-detail', args=[self.later.pk]))
        self.assertNotContains(response, 'Next Summit</span>')
        self.assertContains(response, 'No talks for this event yet.')

    def test_unknown_event_is_404(self):
        self.assertEqual(self.client.get(reverse('event-detail', args=[999999])).status_code, 404)


class ProgrammeBuilderTests(TestCase):
    def setUp(self):
        from .models import Break, ProgrammeItem

        self.event = Event.objects.create(
            name='Build Summit', code='B1',
            start_date=datetime.date(2026, 12, 1), end_date=datetime.date(2026, 12, 2),
        )
        speaker = Speaker.objects.create(name='Pat Lee')
        self.accepted = Talk.objects.create(
            title='Accepted Talk', event=self.event, speaker=speaker, status='accepted', duration_minutes=25,
        )
        self.pending = Talk.objects.create(title='Pending Talk', event=self.event, speaker=speaker)
        self.placed = Talk.objects.create(
            title='Placed Talk', event=self.event, speaker=speaker, status='accepted',
        )
        self.url = reverse('programme-builder', args=[self.event.pk])
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

        self.day_one = Programme.objects.create(name='Day one', event=self.event, day=1)
        self.brk = Break.objects.create(
            programme=self.day_one, name='Lunch',
            start_time=timezone.make_aware(datetime.datetime(2026, 12, 1, 12, 0)),
            end_time=timezone.make_aware(datetime.datetime(2026, 12, 1, 13, 0)),
        )
        ProgrammeItem.objects.create(programme=self.day_one, position=1, talk=self.placed)
        ProgrammeItem.objects.create(programme=self.day_one, position=2, brk=self.brk)

    def test_builder_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_one_programme_per_event_day_is_created(self):
        self.client.get(self.url)
        self.assertEqual(sorted(self.event.programmes.values_list('day', flat=True)), [1, 2])

    def test_only_accepted_unplaced_talks_are_offered(self):
        html = self.client.get(self.url).content.decode()
        pen = html[html.index('id="holding-pen"'):html.index('<h4>Day 1')]
        self.assertIn('Accepted Talk', pen)
        self.assertNotIn('Pending Talk', pen)
        self.assertNotIn('Placed Talk', pen)

    def test_day_columns_show_scheduled_talks_and_breaks(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-type="talk" data-id="%d"' % self.placed.pk, html)
        self.assertIn('data-type="break" data-id="%d"' % self.brk.pk, html)
        self.assertIn('Lunch', html)

    def test_each_day_saves_through_programme_api(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn(reverse('programme-items', args=[self.day_one.pk]), html)
