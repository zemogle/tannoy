import datetime
import json
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone

from .models import Break, Event, Programme, ProgrammeItem, Speaker, Status, Talk


def dt(hour, minute=0):
    return timezone.make_aware(datetime.datetime(2026, 1, 1, hour, minute))


class ProgrammeDayTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.other_event = Event.objects.create(name='O', code='TST', start_date=datetime.date(2026, 1, 2), end_date=datetime.date(2026, 1, 2))

    def test_event_can_have_multiple_programmes_on_different_days(self):
        Programme.objects.create(name='Day 1', event=self.event, day=1)
        Programme.objects.create(name='Day 2', event=self.event, day=2)
        self.assertEqual(self.event.programmes.count(), 2)

    def test_same_day_rejected_for_same_event(self):
        Programme.objects.create(name='Day 1', event=self.event, day=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Programme.objects.create(name='Also day 1', event=self.event, day=1)

    def test_same_day_allowed_for_different_events(self):
        Programme.objects.create(name='A', event=self.event, day=1)
        Programme.objects.create(name='B', event=self.other_event, day=1)
        self.assertEqual(Programme.objects.filter(day=1).count(), 2)

    def test_validate_constraints_reports_duplicate_day(self):
        Programme.objects.create(name='Day 1', event=self.event, day=1)
        duplicate = Programme(name='Dup', event=self.event, day=1)
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()


class BreakModelTests(TestCase):
    def setUp(self):
        event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.programme = Programme.objects.create(name='P', event=event, day=1)

    def test_end_must_be_after_start(self):
        brk = Break(programme=self.programme, start_time=dt(12), end_time=dt(12))
        with self.assertRaises(ValidationError):
            brk.full_clean()
        with self.assertRaises(ValidationError):
            Break.objects.create(programme=self.programme, start_time=dt(13), end_time=dt(12))

    def test_valid_break_saves(self):
        brk = Break.objects.create(
            programme=self.programme, name='Lunch', start_time=dt(12), end_time=dt(13)
        )
        self.assertEqual(self.programme.breaks.get(), brk)


class ProgrammeItemModelTests(TestCase):
    def setUp(self):
        self.e1 = Event.objects.create(name='A', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.e2 = Event.objects.create(name='B', code='TST', start_date=datetime.date(2026, 1, 2), end_date=datetime.date(2026, 1, 2))
        speaker = Speaker.objects.create(name='S')
        self.t1 = Talk.objects.create(title='T1', event=self.e1, speaker=speaker)
        self.t2 = Talk.objects.create(title='T2', event=self.e2, speaker=speaker)
        self.p = Programme.objects.create(name='P', event=self.e1, day=1)
        self.other_p = Programme.objects.create(name='Q', event=self.e2, day=1)
        self.brk = Break.objects.create(
            programme=self.p, start_time=dt(12), end_time=dt(13)
        )

    def test_talk_and_break_items(self):
        ProgrammeItem.objects.create(programme=self.p, talk=self.t1, position=1)
        ProgrammeItem.objects.create(programme=self.p, brk=self.brk, position=2)
        self.assertEqual(self.p.items.count(), 2)

    def test_item_needs_exactly_one_target(self):
        with self.assertRaises(ValidationError):
            ProgrammeItem(programme=self.p, position=1).full_clean()
        with self.assertRaises(ValidationError):
            ProgrammeItem(programme=self.p, talk=self.t1, brk=self.brk, position=1).full_clean()

    def test_talk_must_match_event(self):
        with self.assertRaises(ValidationError):
            ProgrammeItem.objects.create(programme=self.p, talk=self.t2, position=1)

    def test_break_must_belong_to_programme(self):
        with self.assertRaises(ValidationError):
            ProgrammeItem.objects.create(programme=self.other_p, brk=self.brk, position=1)

    def test_db_constraint_rejects_empty_item(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProgrammeItem.objects.bulk_create([
                ProgrammeItem(programme=self.p, position=1)
            ])


class ProgrammeItemsApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.user = User.objects.create_user('user', password='pw')

        self.event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.other_event = Event.objects.create(name='O', code='TST', start_date=datetime.date(2026, 1, 2), end_date=datetime.date(2026, 1, 2))
        speaker = Speaker.objects.create(name='S')
        self.t1 = Talk.objects.create(title='T1', event=self.event, speaker=speaker)
        self.t2 = Talk.objects.create(title='T2', event=self.event, speaker=speaker)
        self.t3 = Talk.objects.create(title='T3', event=self.event, speaker=speaker)
        self.foreign = Talk.objects.create(title='X', event=self.other_event, speaker=speaker)
        self.programme = Programme.objects.create(name='P', event=self.event, day=1)
        self.other_programme = Programme.objects.create(name='Q', event=self.event, day=2)
        self.lunch = Break.objects.create(
            programme=self.programme, name='Lunch', start_time=dt(12), end_time=dt(13)
        )
        self.foreign_break = Break.objects.create(
            programme=self.other_programme, name='Other', start_time=dt(12), end_time=dt(13)
        )
        self.url = reverse('programme-items', args=[self.programme.id])

        self.client.force_login(self.staff)

    def talk(self, talk):
        return {'type': 'talk', 'id': talk.id}

    def brk(self, brk):
        return {'type': 'break', 'id': brk.id}

    def put(self, body, raw=False):
        payload = body if raw else json.dumps(body)
        return self.client.put(self.url, payload, content_type='application/json')

    def order(self):
        return list(
            ProgrammeItem.objects.filter(programme=self.programme)
            .order_by('position')
            .values_list('talk_id', 'brk_id')
        )

    def test_put_interleaves_talks_and_breaks(self):
        response = self.put({'items': [
            self.talk(self.t1), self.brk(self.lunch), self.talk(self.t2),
        ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order(), [
            (self.t1.id, None), (None, self.lunch.id), (self.t2.id, None),
        ])
        body = response.json()
        self.assertEqual([i['type'] for i in body['items']], ['talk', 'break', 'talk'])
        self.assertEqual([i['position'] for i in body['items']], [1, 2, 3])
        self.assertEqual(body['items'][1]['name'], 'Lunch')

    def test_put_reorders_existing(self):
        self.put({'items': [self.talk(self.t1), self.talk(self.t2), self.brk(self.lunch)]})
        response = self.put({'items': [self.brk(self.lunch), self.talk(self.t2), self.talk(self.t1)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order(), [
            (None, self.lunch.id), (self.t2.id, None), (self.t1.id, None),
        ])

    def test_put_empty_clears(self):
        self.put({'items': [self.talk(self.t1)]})
        self.assertEqual(self.put({'items': []}).status_code, 200)
        self.assertEqual(self.order(), [])

    def test_get_returns_ordered_items(self):
        self.put({'items': [self.brk(self.lunch), self.talk(self.t1)]})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(i['type'], i['id']) for i in response.json()['items']],
            [('break', self.lunch.id), ('talk', self.t1.id)],
        )

    def test_rejects_talk_from_other_event(self):
        response = self.put({'items': [self.talk(self.t1), self.talk(self.foreign)]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.order(), [])

    def test_rejects_break_from_other_programme(self):
        response = self.put({'items': [self.brk(self.foreign_break)]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.order(), [])

    def test_rejects_unknown_ids(self):
        response = self.put({'items': [self.talk(self.t1), {'type': 'talk', 'id': 99999}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['missing'], {'talk': [99999]})

        response = self.put({'items': [{'type': 'break', 'id': 99999}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['missing'], {'break': [99999]})

    def test_rejects_duplicates(self):
        self.assertEqual(
            self.put({'items': [self.talk(self.t1), self.talk(self.t1)]}).status_code, 400
        )

    def test_rejects_bad_shapes(self):
        bad = [
            {'items': [{'type': 'session', 'id': 1}]},
            {'items': [{'type': 'talk', 'id': '1'}]},
            {'items': [{'type': 'talk', 'id': True}]},
            {'items': [{'id': 1}]},
            {'items': 'abc'},
            {'talk_ids': [self.t1.id]},
            {},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.put(body).status_code, 400)
        self.assertEqual(self.put('not json', raw=True).status_code, 400)

    def test_failed_put_keeps_existing_order(self):
        self.put({'items': [self.talk(self.t1), self.brk(self.lunch)]})
        self.put({'items': [self.talk(self.t2), self.talk(self.foreign)]})
        self.assertEqual(self.order(), [(self.t1.id, None), (None, self.lunch.id)])

    def test_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.put({'items': []}).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_requires_staff(self):
        self.client.force_login(self.user)
        self.assertEqual(self.put({'items': []}).status_code, 403)

    def test_unknown_programme_404(self):
        url = reverse('programme-items', args=[9999])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_put_without_csrf_token_rejected(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.put(
            self.url, json.dumps({'items': []}), content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)


class AdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(self.admin_user)

        self.event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.speaker = Speaker.objects.create(name='S')
        self.talk = Talk.objects.create(title='T', event=self.event, speaker=self.speaker)
        self.programme = Programme.objects.create(name='P', event=self.event, day=1)
        self.brk = Break.objects.create(
            programme=self.programme, name='Lunch', start_time=dt(12), end_time=dt(13)
        )

    def test_changelist_and_add_pages_load(self):
        for model in ('event', 'speaker', 'talk', 'programme', 'break'):
            with self.subTest(model=model):
                response = self.client.get(reverse(f'admin:events_{model}_changelist'))
                self.assertEqual(response.status_code, 200)
                response = self.client.get(reverse(f'admin:events_{model}_add'))
                self.assertEqual(response.status_code, 200)

    def test_change_pages_load(self):
        pages = [
            reverse('admin:events_event_change', args=[self.event.id]),
            reverse('admin:events_speaker_change', args=[self.speaker.id]),
            reverse('admin:events_talk_change', args=[self.talk.id]),
            reverse('admin:events_programme_change', args=[self.programme.id]),
            reverse('admin:events_break_change', args=[self.brk.id]),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_break_via_admin(self):
        response = self.client.post(reverse('admin:events_break_add'), {
            'programme': self.programme.id,
            'name': 'Coffee',
            'start_time_0': '2026-01-01',
            'start_time_1': '10:00:00',
            'end_time_0': '2026-01-01',
            'end_time_1': '10:30:00',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Break.objects.filter(name='Coffee').exists())

    def test_create_break_with_bad_window_rejected(self):
        response = self.client.post(reverse('admin:events_break_add'), {
            'programme': self.programme.id,
            'name': 'Bad',
            'start_time_0': '2026-01-01',
            'start_time_1': '11:00:00',
            'end_time_0': '2026-01-01',
            'end_time_1': '10:00:00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Break.objects.filter(name='Bad').exists())

    def test_admin_rejects_duplicate_day_for_event(self):
        response = self.client.post(reverse('admin:events_programme_add'), {
            'name': 'Dup',
            'event': self.event.id,
            'day': '1',
            'breaks-TOTAL_FORMS': '0',
            'breaks-INITIAL_FORMS': '0',
            'breaks-MIN_NUM_FORMS': '0',
            'breaks-MAX_NUM_FORMS': '1000',
            'items-TOTAL_FORMS': '0',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Programme.objects.filter(name='Dup').exists())

    def test_programme_inline_orders_talks_and_breaks(self):
        url = reverse('admin:events_programme_change', args=[self.programme.id])
        response = self.client.post(url, {
            'name': 'P',
            'event': self.event.id,
            'day': '1',
            'breaks-TOTAL_FORMS': '0',
            'breaks-INITIAL_FORMS': '1',
            'breaks-MIN_NUM_FORMS': '0',
            'breaks-MAX_NUM_FORMS': '1000',
            'breaks-0-id': self.brk.id,
            'breaks-0-programme': self.programme.id,
            'breaks-0-name': 'Lunch',
            'breaks-0-start_time_0': '2026-01-01',
            'breaks-0-start_time_1': '12:00:00',
            'breaks-0-end_time_0': '2026-01-01',
            'breaks-0-end_time_1': '13:00:00',
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-position': '1',
            'items-0-talk': self.talk.id,
            'items-0-brk': '',
            'items-1-position': '2',
            'items-1-talk': '',
            'items-1-brk': self.brk.id,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [(i.position, i.talk_id, i.brk_id) for i in self.programme.items.all()],
            [(1, self.talk.id, None), (2, None, self.brk.id)],
        )


class SubmissionsPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.user = User.objects.create_user('user', password='pw')
        self.url = reverse('submissions')

        self.oct = Event.objects.create(name='Summit Oct', code='TST', start_date=datetime.date(2026, 10, 26), end_date=datetime.date(2026, 10, 26))
        self.apr = Event.objects.create(name='Summit Apr', code='TST', start_date=datetime.date(2026, 4, 27), end_date=datetime.date(2026, 4, 27))
        alex = Speaker.objects.create(name='Alex Morgan')
        jamie = Speaker.objects.create(name='Jamie Lee')
        Talk.objects.create(
            title='Designing for Trust', event=self.oct, speaker=alex,
            status=Status.ACCEPTED, submitted_at=dt(9),
        )
        Talk.objects.create(
            title='Open Source at Scale', event=self.apr, speaker=jamie,
            status=Status.PENDING, submitted_at=dt(10),
        )
        Talk.objects.create(
            title='The Future of Teams', event=self.oct, speaker=jamie,
            status=Status.REJECTED, submitted_at=dt(11),
        )
        self.client.force_login(self.staff)

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response['Location'])

    def test_root_redirects_to_submissions(self):
        response = self.client.get('/')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_table_is_populated_from_talks(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Designing for Trust')
        self.assertContains(response, 'Open Source at Scale')
        self.assertContains(response, 'Alex Morgan')
        self.assertContains(response, dt(9).strftime('%d.%m.%y'))
        self.assertContains(response, 'status-icon is-accepted')
        self.assertContains(response, 'status-icon is-rejected')

    def test_quick_status_counts(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['counts'], {'accepted': 1, 'pending': 1, 'rejected': 1})

    def test_search_filter(self):
        response = self.client.get(self.url, {'q': 'jamie'})
        titles = [t.title for t in response.context['talks']]
        self.assertEqual(sorted(titles), ['Open Source at Scale', 'The Future of Teams'])

    def test_status_and_event_filters(self):
        response = self.client.get(self.url, {'status': 'pending'})
        self.assertEqual([t.title for t in response.context['talks']], ['Open Source at Scale'])

        response = self.client.get(self.url, {'event': str(self.oct.id)})
        self.assertEqual(
            sorted(t.title for t in response.context['talks']),
            ['Designing for Trust', 'The Future of Teams'],
        )

    def test_title_filter_and_invalid_status_ignored(self):
        response = self.client.get(self.url, {'title': 'trust'})
        self.assertEqual([t.title for t in response.context['talks']], ['Designing for Trust'])
        response = self.client.get(self.url, {'status': 'bogus'})
        self.assertEqual(len(response.context['talks']), 3)

    def test_csv_export_respects_filters(self):
        response = self.client.get(self.url, {'status': 'accepted', 'export': 'csv'})
        self.assertEqual(response['Content-Type'], 'text/csv')
        lines = response.content.decode().strip().splitlines()
        self.assertEqual(lines[0], 'Talk title,Status,Submitter,Submitted date,Event')
        self.assertEqual(len(lines), 2)
        self.assertIn('Designing for Trust,Accepted,Alex Morgan', lines[1])


class TalkDetailTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.event = Event.objects.create(name='Summit', code='TST', start_date=datetime.date(2026, 10, 26), end_date=datetime.date(2026, 10, 26))
        self.alex = Speaker.objects.create(name='Alex Morgan')
        self.jamie = Speaker.objects.create(name='Jamie Lee')
        self.other = Speaker.objects.create(name='Not On Talk')
        self.talk = Talk.objects.create(
            title='Designing for Trust', event=self.event, speaker=self.alex,
            abstract='A practical exploration.', duration_minutes=25,
        )
        self.jamie.talks.add(self.talk)
        self.url = reverse('talk-detail', args=[self.talk.id])
        self.client.force_login(self.staff)

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_shows_talk_details_and_speakers(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Designing for Trust')
        self.assertContains(response, 'A practical exploration.')
        self.assertContains(response, '25 mins')
        self.assertContains(response, 'Alex Morgan')
        self.assertContains(response, 'Jamie Lee')
        self.assertNotContains(response, 'Not On Talk')

    def test_speakers_are_distinct(self):
        self.alex.talks.add(self.talk)  # main speaker also linked via M2M
        response = self.client.get(self.url)
        self.assertEqual([s.name for s in response.context['speakers']], ['Alex Morgan', 'Jamie Lee'])

    def test_accept_and_reject(self):
        self.client.post(self.url, {'action': 'accept'})
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.status, Status.ACCEPTED)

        response = self.client.post(self.url, {'action': 'reject'})
        self.assertRedirects(response, self.url)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.status, Status.REJECTED)

    def test_unknown_action_rejected(self):
        response = self.client.post(self.url, {'action': 'publish'})
        self.assertEqual(response.status_code, 400)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.status, Status.PENDING)

    def test_missing_talk_404(self):
        self.assertEqual(self.client.get(reverse('talk-detail', args=[9999])).status_code, 404)

    def test_submissions_links_to_detail(self):
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, self.url)


class SpeakerProfileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(self.staff)
        self.event = Event.objects.create(name='Summit', code='TST', start_date=datetime.date(2026, 10, 26), end_date=datetime.date(2026, 10, 26))
        self.speaker = Speaker.objects.create(
            name='Alex Morgan', affiliation='Canonical', requests_sponsorship=True,
            headshot='speakers/alex.png',
        )
        self.talk = Talk.objects.create(title='T', event=self.event, speaker=self.speaker)

    def test_fields_saved(self):
        speaker = Speaker.objects.get(pk=self.speaker.pk)
        self.assertEqual(speaker.affiliation, 'Canonical')
        self.assertTrue(speaker.requests_sponsorship)
        self.assertEqual(speaker.headshot.name, 'speakers/alex.png')

    def test_defaults(self):
        plain = Speaker.objects.create(name='Plain')
        self.assertFalse(plain.requests_sponsorship)
        self.assertEqual(plain.affiliation, '')
        self.assertFalse(plain.headshot)

    def test_talk_detail_shows_profile(self):
        response = self.client.get(reverse('talk-detail', args=[self.talk.id]))
        self.assertContains(response, 'Canonical')
        self.assertContains(response, 'Requests sponsorship')
        self.assertContains(response, 'src="/media/speakers/alex.png"')

    def test_talk_detail_falls_back_to_icon(self):
        self.speaker.headshot = ''
        self.speaker.save()
        response = self.client.get(reverse('talk-detail', args=[self.talk.id]))
        self.assertNotContains(response, '<img class="speaker-headshot"')
        self.assertContains(response, 'p-icon--user')

    def test_admin_speaker_form_accepts_new_fields(self):
        response = self.client.post(reverse('admin:events_speaker_add'), {
            'name': 'Jamie Lee',
            'affiliation': 'Example Corp',
            'requests_sponsorship': 'on',
            'talks': [],
        })
        self.assertEqual(response.status_code, 302)
        jamie = Speaker.objects.get(name='Jamie Lee')
        self.assertEqual(jamie.affiliation, 'Example Corp')
        self.assertTrue(jamie.requests_sponsorship)


PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class HeadshotStorageTests(TestCase):
    @override_settings(USE_S3_HEADSHOTS=False)
    def test_uses_local_storage_when_disabled(self):
        from django.core.files.storage import default_storage
        from events.storage import _active_backend
        self.assertIs(_active_backend(), default_storage)

    @override_settings(USE_S3_HEADSHOTS=True)
    def test_uses_s3_when_enabled(self):
        from events.storage import HeadshotS3Storage, _active_backend
        self.assertIsInstance(_active_backend(), HeadshotS3Storage)

    @override_settings(USE_S3_HEADSHOTS=True)
    def test_saving_headshot_goes_to_s3(self):
        from events.storage import HeadshotS3Storage
        with mock.patch.object(HeadshotS3Storage, 'exists', return_value=False), \
                mock.patch.object(
                    HeadshotS3Storage, '_save', return_value='speakers/alex.png'
                ) as s3_save:
            speaker = Speaker.objects.create(
                name='Alex',
                headshot=SimpleUploadedFile('alex.png', PNG_BYTES, content_type='image/png'),
            )
        self.assertEqual(s3_save.call_count, 1)
        self.assertEqual(s3_save.call_args[0][0], 'speakers/alex.png')
        speaker.refresh_from_db()
        self.assertEqual(speaker.headshot.name, 'speakers/alex.png')


class TalkDurationTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.speaker = Speaker.objects.create(name='S')

    def test_allowed_durations_save(self):
        for minutes in (5, 25, 45):
            with self.subTest(minutes=minutes):
                talk = Talk.objects.create(
                    title=f'T{minutes}', event=self.event, speaker=self.speaker,
                    duration_minutes=minutes,
                )
                talk.full_clean()

    def test_blank_duration_allowed(self):
        talk = Talk.objects.create(title='No duration', event=self.event, speaker=self.speaker)
        self.assertIsNone(talk.duration_minutes)
        talk.full_clean()

    def test_other_durations_rejected_by_validation(self):
        talk = Talk(title='Bad', event=self.event, speaker=self.speaker, duration_minutes=30)
        with self.assertRaises(ValidationError):
            talk.full_clean()

    def test_other_durations_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Talk.objects.create(
                title='Bad', event=self.event, speaker=self.speaker, duration_minutes=30,
            )


class TalkAbstractTopicTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        self.event = Event.objects.create(name='E', code='TST', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        self.speaker = Speaker.objects.create(name='S')

    def test_topic_choices_validated(self):
        talk = Talk(title='T', event=self.event, speaker=self.speaker, topic='topic-ai')
        talk.full_clean()
        talk.topic = 'topic-cloud'
        with self.assertRaises(ValidationError):
            talk.full_clean()

    def test_topic_and_discourse_optional(self):
        talk = Talk.objects.create(title='T', event=self.event, speaker=self.speaker)
        self.assertEqual(talk.topic, '')
        self.assertIsNone(talk.discourse_id)
        talk.full_clean()

    def test_discourse_id_stores_integer(self):
        talk = Talk.objects.create(
            title='T', event=self.event, speaker=self.speaker, discourse_id=12345,
        )
        talk.refresh_from_db()
        self.assertEqual(talk.discourse_id, 12345)

    def test_detail_shows_abstract_and_topic_chip(self):
        talk = Talk.objects.create(
            title='T', event=self.event, speaker=self.speaker,
            abstract='Some abstract text.', topic='topic-security', discourse_id=77,
        )
        response = self.client.get(reverse('talk-detail', args=[talk.id]))
        self.assertContains(response, 'Some abstract text.')
        self.assertContains(response, 'Abstract')
        self.assertContains(response, '<span class="p-chip__value">Security</span>')
        self.assertNotContains(response, 'Discourse ID')


class SpeakerProfileExtraFieldsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(self.admin_user)

    def test_defaults(self):
        speaker = Speaker.objects.create(name='Plain')
        self.assertEqual(speaker.email, '')
        self.assertEqual(speaker.pronouns, '')
        self.assertEqual(speaker.username, '')
        self.assertEqual(speaker.bio, '')
        self.assertTrue(speaker.in_person)

    def test_fields_saved(self):
        speaker = Speaker.objects.create(
            name='Alex', email='alex@example.com', pronouns='they/them',
            username='alexm', bio='Bio text', in_person=True,
        )
        speaker.refresh_from_db()
        self.assertEqual(speaker.email, 'alex@example.com')
        self.assertEqual(speaker.pronouns, 'they/them')
        self.assertEqual(speaker.username, 'alexm')
        self.assertEqual(speaker.bio, 'Bio text')
        self.assertTrue(speaker.in_person)

    def test_invalid_email_rejected(self):
        speaker = Speaker(name='Bad', email='not-an-email')
        with self.assertRaises(ValidationError):
            speaker.full_clean()

    def test_admin_form_accepts_new_fields(self):
        response = self.client.post(reverse('admin:events_speaker_add'), {
            'name': 'Sam',
            'affiliation': '',
            'email': 'sam@example.com',
            'pronouns': 'she/her',
            'username': 'sam',
            'bio': 'Hello',
            'in_person': 'on',
            'talks': [],
        })
        self.assertEqual(response.status_code, 302)
        sam = Speaker.objects.get(name='Sam')
        self.assertEqual(sam.pronouns, 'she/her')
        self.assertTrue(sam.in_person)


class EventDatesTests(TestCase):
    def test_end_before_start_rejected(self):
        with self.assertRaises(ValidationError):
            Event.objects.create(
                name='Bad', code='BAD',
                start_date=datetime.date(2026, 10, 27),
                end_date=datetime.date(2026, 10, 26),
            )

    def test_multi_day_event_saves(self):
        event = Event.objects.create(
            name='Summit', code='SUM26',
            start_date=datetime.date(2026, 10, 26),
            end_date=datetime.date(2026, 10, 28),
        )
        event.refresh_from_db()
        self.assertEqual(event.end_date, datetime.date(2026, 10, 28))
        self.assertEqual(event.code, 'SUM26')


class SpeakerSubmitterTests(TestCase):
    def test_submitter_defaults_to_false(self):
        self.assertFalse(Speaker.objects.create(name='New').submitter)

    def test_submitter_can_be_set(self):
        speaker = Speaker.objects.create(name='Sub', submitter=True)
        speaker.refresh_from_db()
        self.assertTrue(speaker.submitter)


class PlaceholderFixtureTests(TestCase):
    fixtures = ['placeholder_data']

    def test_counts(self):
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(Talk.objects.count(), 10)
        self.assertEqual(Speaker.objects.count(), 12)
        self.assertEqual(
            sorted(Event.objects.values_list('code', flat=True)), ['27.04', '27.10']
        )

    def test_every_talk_has_exactly_one_submitter(self):
        for talk in Talk.objects.all():
            with self.subTest(talk=talk.title):
                linked = Speaker.objects.filter(Q(talks=talk) | Q(pk=talk.speaker_id)).distinct()
                self.assertEqual(linked.filter(submitter=True).count(), 1)

    def test_some_talks_have_multiple_speakers(self):
        multi = [
            talk for talk in Talk.objects.all()
            if Speaker.objects.filter(Q(talks=talk) | Q(pk=talk.speaker_id)).distinct().count() > 1
        ]
        self.assertGreater(len(multi), 0)


class SubmissionsSpeakersAndFiltersTests(TestCase):
    fixtures = ['placeholder_data']

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse('submissions')

    def test_panel_is_titled_filters(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Filters')
        self.assertNotContains(response, 'TOOLS')

    def test_table_lists_all_speakers_and_highlights_submitter(self):
        response = self.client.get(self.url)
        talk = next(t for t in response.context['talks'] if t.pk == 5)
        names = [s.name for s in talk.all_speakers]
        self.assertEqual(len(names), 3)
        self.assertEqual(
            [s.submitter for s in talk.all_speakers].count(True), 1
        )
        self.assertContains(response, 'Fatima Rahman')
        self.assertContains(response, '<strong>Taylor Smith</strong>')

    def test_quick_status_counts_follow_filters(self):
        total = self.client.get(self.url).context['counts']
        self.assertEqual(sum(total.values()), 10)

        response = self.client.get(self.url, {'event': str(Event.objects.get(code='27.04').pk)})
        filtered = response.context['counts']
        self.assertEqual(filtered['accepted'] + filtered['pending'] + filtered['rejected'], 5)
        self.assertEqual(
            filtered,
            {
                'accepted': sum(1 for t in response.context['talks'] if t.status == 'accepted'),
                'pending': sum(1 for t in response.context['talks'] if t.status == 'pending'),
                'rejected': sum(1 for t in response.context['talks'] if t.status == 'rejected'),
            },
        )

    def test_status_filter_updates_counts(self):
        response = self.client.get(self.url, {'status': 'accepted'})
        counts = response.context['counts']
        self.assertGreater(counts['accepted'], 0)
        self.assertEqual(counts['pending'], 0)
        self.assertEqual(counts['rejected'], 0)
        self.assertEqual(counts['accepted'], len(response.context['talks']))


class NavBullhornTests(TestCase):
    def test_bullhorn_icon_in_nav_on_pages(self):
        User = get_user_model()
        staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(staff)
        event = Event.objects.create(name='E', code='E', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        speaker = Speaker.objects.create(name='S')
        talk = Talk.objects.create(title='T', event=event, speaker=speaker)
        for url in (reverse('submissions'), reverse('talk-detail', args=[talk.id])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'events/img/bullhorn.svg')
                self.assertContains(response, 'class="tannoy-logo"')


class TalkTypeIconTests(TestCase):
    fixtures = ['placeholder_data']

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)

    def test_icon_stylesheet_linked(self):
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, 'events/css/vanilla-icons.css')

    def test_submissions_icons_by_duration(self):
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, 'events/img/bolt.svg')
        self.assertContains(response, 'p-icon--priority-low')
        self.assertContains(response, 'p-icon--priority-critical')

    def test_submissions_has_untitled_icon_column(self):
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, '<th></th>', count=3)  # one per status section

    def test_detail_chip_for_long_talk(self):
        talk = Talk.objects.filter(duration_minutes=45).first()
        response = self.client.get(reverse('talk-detail', args=[talk.id]))
        self.assertContains(response, 'class="p-chip"')
        self.assertContains(response, '45 mins')
        self.assertContains(response, 'p-icon--priority-critical')

    def test_detail_chip_for_short_talk(self):
        talk = Talk.objects.filter(duration_minutes=25).first()
        response = self.client.get(reverse('talk-detail', args=[talk.id]))
        self.assertContains(response, '25 mins')
        self.assertContains(response, 'p-icon--priority-low')

    def test_detail_chip_for_lightning_talk(self):
        talk = Talk.objects.filter(duration_minutes=5).first()
        response = self.client.get(reverse('talk-detail', args=[talk.id]))
        self.assertContains(response, '5 mins')
        self.assertContains(response, 'events/img/bolt.svg')


class ApplicationLayoutTests(TestCase):
    def setUp(self):
        User = get_user_model()
        staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(staff)
        event = Event.objects.create(name='E', code='E', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        speaker = Speaker.objects.create(name='S')
        self.talk = Talk.objects.create(title='T', event=event, speaker=speaker)

    def test_pages_use_application_layout(self):
        for url in (reverse('submissions'), reverse('talk-detail', args=[self.talk.id])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'class="l-application"')
                self.assertContains(response, 'class="l-navigation-bar"')
                self.assertContains(response, 'class="l-navigation is-collapsed"')
                self.assertContains(response, 'class="l-main"')
                self.assertContains(response, 'js-menu-toggle')
                self.assertNotContains(response, 'p-navigation')

    def test_logo_is_in_side_drawer_for_wide_screens(self):
        # .l-navigation-bar is display:none on wide screens, so the brand
        # must also appear inside the drawer.
        response = self.client.get(reverse('submissions'))
        drawer = response.content.decode().split('class="l-navigation__drawer"', 1)[1]
        self.assertIn('class="tannoy-logo"', drawer.split('</header>', 1)[0])
        self.assertIn('Tannoy', drawer.split('</header>', 1)[0])

    def test_submissions_is_current_nav_item(self):
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, 'aria-current="page">', count=1)


class TalkDetailToolbarTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        event = Event.objects.create(name='E', code='E', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        speaker = Speaker.objects.create(name='S')
        self.talk = Talk.objects.create(title='T', event=event, speaker=speaker)

    def test_controls_are_above_content_and_no_card(self):
        response = self.client.get(reverse('talk-detail', args=[self.talk.id]))
        html = response.content.decode()
        self.assertNotIn('Editorial Controls', html)
        self.assertNotIn('class="p-card"', html)
        controls = html.index('name="action" value="accept"')
        self.assertLess(controls, html.index('<h2 class="p-heading--5">Speakers</h2>'))
        self.assertIn('class="p-button--positive"', html)
        self.assertIn('class="p-button--negative"', html)
        self.assertIn('class="p-button--brand"', html)


class SubmissionSectionsTests(TestCase):
    fixtures = ['placeholder_data']

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)

    def test_sections_in_pending_accepted_rejected_order(self):
        response = self.client.get(reverse('submissions'))
        self.assertEqual(
            [section['key'] for section in response.context['sections']],
            ['pending', 'accepted', 'rejected'],
        )
        html = response.content.decode()
        pending = html.index('Pending (')
        accepted = html.index('Accepted (')
        rejected = html.index('Rejected (')
        self.assertLess(pending, accepted)
        self.assertLess(accepted, rejected)

    def test_each_talk_appears_in_its_own_section_only(self):
        response = self.client.get(reverse('submissions'))
        total = 0
        for section in response.context['sections']:
            for talk in section['talks']:
                self.assertEqual(talk.status, section['key'])
            total += len(section['talks'])
        self.assertEqual(total, Talk.objects.count())

    def test_section_headers_show_counts(self):
        response = self.client.get(reverse('submissions'))
        for section in response.context['sections']:
            self.assertContains(
                response,
                f"{section['label']} ({len(section['talks'])})",
            )

    def test_empty_section_shows_message(self):
        Talk.objects.filter(status=Status.REJECTED).delete()
        response = self.client.get(reverse('submissions'))
        self.assertContains(response, 'No rejected submissions match these filters.')


class TalkPublishingStateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)
        event = Event.objects.create(name='E', code='E', start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 1))
        speaker = Speaker.objects.create(name='S', username='speaker-s')
        self.talk = Talk.objects.create(title='T', event=event, speaker=speaker)
        self.url = reverse('talk-detail', args=[self.talk.id])

    def button_is_disabled(self, html, action):
        match = re.search(r'<button[^>]*value="%s"[^>]*>' % action, html)
        self.assertIsNotNone(match, action)
        return ' disabled' in match.group(0)

    def set_state(self, status, published=False):
        self.talk.status = status
        self.talk.published = published
        self.talk.save()

    def test_published_defaults_to_false(self):
        self.assertFalse(Talk.objects.get(pk=self.talk.pk).published)

    def test_allowed_actions_per_state(self):
        cases = {
            Status.PENDING: {'accept', 'reject'},
            Status.ACCEPTED: {'reject', 'publish'},
            Status.REJECTED: {'accept'},
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.set_state(status)
                self.assertEqual(self.talk.allowed_actions(), expected)
        self.set_state(Status.ACCEPTED, published=True)
        self.assertEqual(self.talk.allowed_actions(), set())

    def test_accepted_disables_accept_and_enables_publish(self):
        self.set_state(Status.ACCEPTED)
        html = self.client.get(self.url).content.decode()
        self.assertTrue(self.button_is_disabled(html, 'accept'))
        self.assertFalse(self.button_is_disabled(html, 'publish'))

    def test_rejected_disables_publish(self):
        self.set_state(Status.REJECTED)
        html = self.client.get(self.url).content.decode()
        self.assertTrue(self.button_is_disabled(html, 'publish'))
        self.assertFalse(self.button_is_disabled(html, 'accept'))

    def test_published_disables_all_buttons(self):
        self.set_state(Status.ACCEPTED, published=True)
        response = self.client.get(self.url)
        html = response.content.decode()
        for action in ('accept', 'reject', 'publish'):
            with self.subTest(action=action):
                self.assertTrue(self.button_is_disabled(html, action))
        self.assertContains(response, 'Published')

    def test_publish_sets_published(self):
        self.set_state(Status.ACCEPTED)
        discourse = mock.Mock()
        discourse.resolve_category.return_value = (5, 'ubuntu-summit')
        discourse.get_topic.return_value = None
        discourse.find_topic_by_title.return_value = None
        discourse.create_topic.return_value = 15
        with mock.patch('events.models.client_from_settings', return_value=(discourse, 'ubuntu-summit')):
            response = self.client.post(self.url, {'action': 'publish'})
        self.assertRedirects(response, self.url)
        self.talk.refresh_from_db()
        self.assertTrue(self.talk.published)
        self.assertEqual(self.talk.status, Status.ACCEPTED)

    def test_publish_rejected_unless_accepted(self):
        self.set_state(Status.PENDING)
        self.assertEqual(self.client.post(self.url, {'action': 'publish'}).status_code, 400)
        self.talk.refresh_from_db()
        self.assertFalse(self.talk.published)

    def test_no_actions_once_published(self):
        self.set_state(Status.ACCEPTED, published=True)
        for action in ('accept', 'reject', 'publish'):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url, {'action': action}).status_code, 400)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.status, Status.ACCEPTED)
        self.assertTrue(self.talk.published)

    def test_accept_rejected_when_already_accepted(self):
        self.set_state(Status.ACCEPTED)
        self.assertEqual(self.client.post(self.url, {'action': 'accept'}).status_code, 400)
