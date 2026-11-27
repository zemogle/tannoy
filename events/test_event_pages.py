import datetime
from zoneinfo import ZoneInfo

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
        pen = html[html.index('id="holding-pen"'):html.index('id="break-blocks"')]
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

    def test_breaks_box_offers_four_generic_breaks(self):
        html = self.client.get(self.url).content.decode()
        box = html[html.index('id="break-blocks"'):html.index('<input type="hidden" id="csrf-token"')]
        self.assertEqual(box.count('data-type="new_break"'), 4)
        for name, minutes in [('Coffee &amp; Networking', 20), ('Coffee &amp; Networking', 30), ('Lunch', 60), ('Lunch', 90)]:
            self.assertIn(f'data-name="{name}" data-minutes="{minutes}"', box)

    def test_layout_has_75_percent_content_and_25_percent_sidebar(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('class="grid-col-6"', html)
        self.assertIn('class="grid-col-2"', html)
        self.assertLess(html.index('class="grid-col-6"'), html.index('class="grid-col-2"'))


class ProgrammeBreakSaveTests(TestCase):
    def setUp(self):
        import json

        self.json = json
        self.event = Event.objects.create(
            name='Save Summit', code='S1',
            start_date=datetime.date(2026, 12, 1), end_date=datetime.date(2026, 12, 2),
        )
        speaker = Speaker.objects.create(name='Sam Ray')
        self.talk = Talk.objects.create(
            title='First talk', event=self.event, speaker=speaker, status='accepted', duration_minutes=25,
        )
        self.programme = Programme.objects.create(name='Day one', event=self.event, day=1)
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))
        self.url = reverse('programme-items', args=[self.programme.pk])

    def save(self, items):
        return self.client.put(
            self.url, self.json.dumps({'items': items}), content_type='application/json',
        )

    def test_new_breaks_become_break_objects_in_order(self):
        from .models import Break

        response = self.save([
            {'type': 'talk', 'id': self.talk.pk},
            {'type': 'new_break', 'name': 'Coffee & Networking', 'minutes': 20},
            {'type': 'new_break', 'name': 'Lunch', 'minutes': 60},
        ])
        self.assertEqual(response.status_code, 200)
        breaks = list(Break.objects.filter(programme=self.programme).order_by('start_time'))
        self.assertEqual([b.name for b in breaks], ['Coffee & Networking', 'Lunch'])
        # Day starts 09:30 London time; talk 09:30-09:55, coffee 09:55-10:15, lunch 10:15-11:15.
        self.assertEqual(breaks[0].start_time.astimezone(ZoneInfo('Europe/London')).strftime('%H:%M'), '09:55')
        self.assertEqual(breaks[0].end_time.astimezone(ZoneInfo('Europe/London')).strftime('%H:%M'), '10:15')
        self.assertEqual(breaks[1].end_time.astimezone(ZoneInfo('Europe/London')).strftime('%H:%M'), '11:15')
        self.assertEqual(
            [item['type'] for item in response.json()['items']], ['talk', 'break', 'break'],
        )

    def test_saved_break_is_kept_when_day_is_saved_again(self):
        from .models import Break

        self.save([{'type': 'new_break', 'name': 'Lunch', 'minutes': 90}])
        brk = Break.objects.get(programme=self.programme)
        response = self.save([{'type': 'break', 'id': brk.pk}])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Break.objects.filter(programme=self.programme).count(), 1)

    def test_invalid_break_length_is_rejected(self):
        response = self.save([{'type': 'new_break', 'name': 'Tea', 'minutes': 45}])
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_blank_break_name_is_rejected(self):
        response = self.save([{'type': 'new_break', 'name': '  ', 'minutes': 20}])
        self.assertEqual(response.status_code, 400)




class ProgrammeBlockTimeTests(TestCase):
    def setUp(self):
        from .models import Break, ProgrammeItem

        self.event = Event.objects.create(
            name='Clock Summit', code='C1',
            start_date=datetime.date(2027, 4, 27), end_date=datetime.date(2027, 4, 27),
        )
        speaker = Speaker.objects.create(name='Jo Park')
        talk = Talk.objects.create(
            title='Timed talk', event=self.event, speaker=speaker, status='accepted', duration_minutes=25,
        )
        programme = Programme.objects.create(name='Day one', event=self.event, day=1)
        # The talk runs 09:30-09:55 London time, so the one-hour lunch starts at 09:55.
        # Stored in UTC, where that is 08:55.
        brk = Break.objects.create(
            programme=programme, name='Lunch',
            start_time=datetime.datetime(2027, 4, 27, 8, 0, tzinfo=datetime.timezone.utc),
            end_time=datetime.datetime(2027, 4, 27, 9, 0, tzinfo=datetime.timezone.utc),
        )
        ProgrammeItem.objects.create(programme=programme, position=1, talk=talk)
        ProgrammeItem.objects.create(programme=programme, position=2, brk=brk)
        self.brk = brk
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def test_block_times_shown_before_titles_in_summit_timezone(self):
        html = self.client.get(reverse('programme-builder', args=[self.event.pk])).content.decode()
        self.assertIn('<strong>09:30</strong> Timed talk', html)
        self.assertIn('<strong>09:55</strong> Lunch', html)
        self.assertNotIn('2027', html.split('Day 1')[1].split('Unscheduled talks')[0])


class BreakSequenceTests(ProgrammeBreakSaveTests):
    def test_saving_moves_existing_break_to_follow_the_talk_before_it(self):
        from .models import Break

        brk = Break.objects.create(
            programme=self.programme, name='Lunch',
            start_time=datetime.datetime(2027, 1, 1, 12, 0, tzinfo=ZoneInfo('Europe/London')),
            end_time=datetime.datetime(2027, 1, 1, 13, 0, tzinfo=ZoneInfo('Europe/London')),
        )
        response = self.save([{'type': 'talk', 'id': self.talk.pk}, {'type': 'break', 'id': brk.pk}])
        self.assertEqual(response.status_code, 200)
        brk.refresh_from_db()
        london = brk.start_time.astimezone(ZoneInfo('Europe/London'))
        self.assertEqual(london.strftime('%H:%M'), '09:55')
        self.assertEqual((brk.end_time - brk.start_time).total_seconds(), 3600)


class TalkTypeColourTests(ProgrammeBuilderTests):
    def test_cards_carry_a_class_for_each_talk_length(self):
        Talk.objects.filter(pk=self.placed.pk).update(duration_minutes=45)
        Talk.objects.filter(pk=self.accepted.pk).update(duration_minutes=5)
        html = self.client.get(self.url).content.decode()
        self.assertIn('builder-card--long', html)
        self.assertIn('builder-card--lightning', html)
        self.assertIn('--short', html)  # defined in the stylesheet


class FaviconTests(EventPagesTests):
    def test_pages_link_the_ubuntu_favicon(self):
        html = self.client.get(reverse('events')).content.decode()
        self.assertIn('rel="icon" type="image/x-icon" href="/static/events/img/favicon.ico"', html)
        self.assertIn('href="/static/events/img/favicon-64.png"', html)
        self.assertIn('href="/static/events/img/favicon-180.png"', html)


class ProgrammeKeyTests(ProgrammeBuilderTests):
    def test_key_explains_each_colour(self):
        html = self.client.get(self.url).content.decode()
        key = html[html.index('<h4>Key</h4>'):html.index('<h4>Unscheduled talks</h4>')]
        self.assertIn('Lightning talk (5 mins)', key)
        self.assertIn('Short talk (25 mins)', key)
        self.assertIn('Long talk (45 mins)', key)
        self.assertIn('Break', key)
