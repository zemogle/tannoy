import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
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
        self.event = Event.objects.create(name='E', date=datetime.date(2026, 1, 1))
        self.other_event = Event.objects.create(name='O', date=datetime.date(2026, 1, 2))

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
        event = Event.objects.create(name='E', date=datetime.date(2026, 1, 1))
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
        self.e1 = Event.objects.create(name='A', date=datetime.date(2026, 1, 1))
        self.e2 = Event.objects.create(name='B', date=datetime.date(2026, 1, 2))
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

        self.event = Event.objects.create(name='E', date=datetime.date(2026, 1, 1))
        self.other_event = Event.objects.create(name='O', date=datetime.date(2026, 1, 2))
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

        self.event = Event.objects.create(name='E', date=datetime.date(2026, 1, 1))
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

        self.oct = Event.objects.create(name='Summit Oct', date=datetime.date(2026, 10, 26))
        self.apr = Event.objects.create(name='Summit Apr', date=datetime.date(2026, 4, 27))
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
        self.assertContains(response, 'p-chip--positive')
        self.assertContains(response, 'p-chip--negative')

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
        self.event = Event.objects.create(name='Summit', date=datetime.date(2026, 10, 26))
        self.alex = Speaker.objects.create(name='Alex Morgan')
        self.jamie = Speaker.objects.create(name='Jamie Lee')
        self.other = Speaker.objects.create(name='Not On Talk')
        self.talk = Talk.objects.create(
            title='Designing for Trust', event=self.event, speaker=self.alex,
            summary='A practical exploration.', duration_minutes=25,
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
        self.event = Event.objects.create(name='Summit', date=datetime.date(2026, 10, 26))
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
        self.event = Event.objects.create(name='E', date=datetime.date(2026, 1, 1))
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
