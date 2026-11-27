import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from .discourse import build_topic_body
from .models import Event, Speaker, Talk


class FakeDiscourse:
    def __init__(self, existing_topics=None, matching_title=None):
        self.existing_topics = existing_topics or {}
        self.matching_title = matching_title
        self.created = []
        self.updated = []
        self.next_id = 500

    def resolve_category(self, reference):
        return 5, 'ubuntu-summit'

    def get_topic(self, topic_id):
        return self.existing_topics.get(topic_id)

    def find_topic_by_title(self, slug, category_id, title):
        if self.matching_title and self.matching_title['title'] == title:
            return self.matching_title
        return None

    def create_topic(self, category_id, title, raw, author_username):
        self.next_id += 1
        self.created.append({
            'id': self.next_id, 'category_id': category_id, 'title': title,
            'raw': raw, 'author': author_username,
        })
        return self.next_id

    def update_topic(self, topic_id, title, raw):
        self.updated.append({'id': topic_id, 'title': title, 'raw': raw})


class PublishTalkTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )
        self.speaker = Speaker.objects.create(
            name='Alex Morgan', affiliation='Canonical', bio='Builds things.',
            email='alex@example.com', submitter=True, username='alexm',
        )
        self.co_speaker = Speaker.objects.create(
            name='Jamie Lee', affiliation='Ubuntu', bio='Writes docs.',
            email='jamie@example.com',
        )
        self.talk = Talk.objects.create(
            title='Open Source in Space', event=self.event, speaker=self.speaker,
            abstract='An abstract about space.', status='accepted',
        )
        self.talk.speakers.add(self.speaker, self.co_speaker)

    def test_creates_topic_when_none_exists(self):
        client = FakeDiscourse()
        topic_id = self.talk.publish(client=client, category='ubuntu-summit')

        self.assertEqual(len(client.created), 1)
        self.assertEqual(client.updated, [])
        created = client.created[0]
        self.assertEqual(created['category_id'], 5)
        self.assertEqual(created['title'], 'Open Source in Space')
        self.assertEqual(topic_id, created['id'])

        self.talk.refresh_from_db()
        self.assertTrue(self.talk.published)
        self.assertEqual(self.talk.published_topic_id, created['id'])

    def test_new_topic_is_authored_by_submitter(self):
        client = FakeDiscourse()
        self.talk.publish(client=client, category='ubuntu-summit')
        self.assertEqual(client.created[0]['author'], 'alexm')

    def test_submitter_is_found_among_linked_speakers(self):
        self.speaker.submitter = False
        self.speaker.save()
        self.co_speaker.submitter = True
        self.co_speaker.username = 'jamielee'
        self.co_speaker.save()
        client = FakeDiscourse()
        self.talk.publish(client=client, category='ubuntu-summit')
        self.assertEqual(client.created[0]['author'], 'jamielee')

    def test_missing_submitter_username_blocks_publishing(self):
        self.speaker.username = ''
        self.speaker.save()
        client = FakeDiscourse()
        with self.assertRaises(ValueError):
            self.talk.publish(client=client, category='ubuntu-summit')
        self.assertEqual(client.created, [])
        self.talk.refresh_from_db()
        self.assertFalse(self.talk.published)

    def test_updates_topic_already_stored_on_talk(self):
        self.talk.published_topic_id = 77
        self.talk.save()
        client = FakeDiscourse(existing_topics={77: {'id': 77}})

        topic_id = self.talk.publish(client=client, category='ubuntu-summit')

        self.assertEqual(topic_id, 77)
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated[0]['id'], 77)

    def test_adopts_topic_with_same_title_instead_of_duplicating(self):
        client = FakeDiscourse(matching_title={'id': 88, 'title': 'Open Source in Space'})

        topic_id = self.talk.publish(client=client, category='ubuntu-summit')

        self.assertEqual(topic_id, 88)
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated[0]['id'], 88)

    def test_stale_stored_topic_falls_back_to_title_then_create(self):
        self.talk.published_topic_id = 99
        self.talk.save()
        client = FakeDiscourse(existing_topics={})

        self.talk.publish(client=client, category='ubuntu-summit')

        self.assertEqual(len(client.created), 1)
        self.assertEqual(client.updated, [])

    def test_only_accepted_talks_can_be_published(self):
        self.talk.status = 'pending'
        self.talk.save()
        client = FakeDiscourse()

        with self.assertRaises(ValidationError):
            self.talk.publish(client=client, category='ubuntu-summit')
        self.assertEqual(client.created, [])
        self.talk.refresh_from_db()
        self.assertFalse(self.talk.published)

    def test_published_talk_cannot_be_published_again(self):
        self.talk.publish(client=FakeDiscourse(), category='ubuntu-summit')
        with self.assertRaises(ValidationError):
            self.talk.publish(client=FakeDiscourse(), category='ubuntu-summit')

    def test_body_contains_abstract_and_speakers_as_markdown(self):
        body = build_topic_body(self.talk)

        self.assertIn('### Abstract\n\nAn abstract about space.', body)
        self.assertIn('### Speaker Bio', body)
        self.assertIn('**Alex Morgan**\n*Canonical*\n\nBuilds things.', body)
        self.assertIn('**Jamie Lee**\n*Ubuntu*\n\nWrites docs.', body)
        self.assertLess(body.index('Alex Morgan'), body.index('Jamie Lee'))

    def test_primary_speaker_listed_once(self):
        body = build_topic_body(self.talk)
        self.assertEqual(body.count('**Alex Morgan**'), 1)

    def test_missing_abstract_and_bio_have_placeholder_or_are_skipped(self):
        self.speaker.bio = ''
        self.speaker.save()
        self.talk.abstract = ''
        self.talk.save()

        body = build_topic_body(self.talk)

        self.assertIn('_No abstract provided._', body)
        self.assertIn('**Alex Morgan**\n*Canonical*\n', body)


class PublishButtonTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )
        self.speaker = Speaker.objects.create(name='Alex Morgan', email='alex@example.com')
        self.speaker.username = 'alexm'
        self.speaker.save()
        self.talk = Talk.objects.create(
            title='Open Source in Space', event=self.event, speaker=self.speaker,
            abstract='An abstract.', status='accepted',
        )
        self.url = reverse('talk-detail', args=[self.talk.pk])
        self.admin_user = get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(self.admin_user)

    def test_publish_button_publishes_to_discourse(self):
        fake = FakeDiscourse()
        with mock.patch('events.models.client_from_settings', return_value=(fake, 'ubuntu-summit')):
            response = self.client.post(self.url, {'action': 'publish'}, follow=True)

        self.talk.refresh_from_db()
        self.assertTrue(self.talk.published)
        self.assertEqual(self.talk.published_topic_id, fake.created[0]['id'])
        self.assertContains(response, 'Talk published to Discourse.')

    def test_discourse_failure_is_shown_and_talk_stays_unpublished(self):
        with mock.patch(
            'events.models.client_from_settings',
            side_effect=ValueError('Missing settings: DISCOURSE_CATEGORY'),
        ):
            response = self.client.post(self.url, {'action': 'publish'}, follow=True)

        self.talk.refresh_from_db()
        self.assertFalse(self.talk.published)
        self.assertIsNone(self.talk.published_topic_id)
        self.assertContains(response, 'Could not publish to Discourse')
        self.assertContains(response, 'Missing settings: DISCOURSE_CATEGORY')

    def test_publish_button_disabled_for_pending_talk(self):
        self.talk.status = 'pending'
        self.talk.save()
        response = self.client.post(self.url, {'action': 'publish'})
        self.assertEqual(response.status_code, 400)
        self.talk.refresh_from_db()
        self.assertFalse(self.talk.published)


class AcceptedPublishedColumnTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )
        speaker = Speaker.objects.create(name='Alex Morgan')
        Talk.objects.create(title='Live talk', event=event, speaker=speaker, status='accepted', published=True)
        Talk.objects.create(title='Waiting talk', event=event, speaker=speaker, status='accepted')
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def test_published_icon_only_for_published_accepted_talks(self):
        response = self.client.get(reverse('submissions'))
        html = response.content.decode()
        accepted = html[html.index('aria-label="Accepted submissions"'):html.index('</table>', html.index('aria-label="Accepted submissions"'))]
        self.assertIn('<th>Published</th>', accepted)
        self.assertEqual(accepted.count('p-icon--success'), 1)
        self.assertIn('Live talk', accepted)


class EventIsNextTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.past = Event.objects.create(
            name='Past', code='P1',
            start_date=today - datetime.timedelta(days=30), end_date=today - datetime.timedelta(days=28),
        )
        self.ongoing = Event.objects.create(
            name='Ongoing', code='O1',
            start_date=today - datetime.timedelta(days=1), end_date=today + datetime.timedelta(days=1),
        )
        self.later = Event.objects.create(
            name='Later', code='L1',
            start_date=today + datetime.timedelta(days=60), end_date=today + datetime.timedelta(days=61),
        )

    def test_ongoing_event_is_next_and_later_is_not(self):
        self.assertTrue(self.ongoing.is_next)
        self.assertFalse(self.later.is_next)
        self.assertFalse(self.past.is_next)

    def test_earliest_upcoming_event_is_next_when_nothing_is_ongoing(self):
        self.ongoing.delete()
        self.assertTrue(self.later.is_next)
        self.assertFalse(self.past.is_next)


class NextSummitChipTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        today = timezone.localdate()
        next_event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=today + datetime.timedelta(days=5), end_date=today + datetime.timedelta(days=6),
        )
        later_event = Event.objects.create(
            name='Ubuntu Summit 27.04', code='27.04',
            start_date=today + datetime.timedelta(days=200), end_date=today + datetime.timedelta(days=201),
        )
        speaker = Speaker.objects.create(name='Alex Morgan')
        self.next_talk = Talk.objects.create(title='Soon', event=next_event, speaker=speaker)
        self.later_talk = Talk.objects.create(
            title='Later', event=later_event, speaker=speaker, status='accepted',
        )
        get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def test_chip_shown_only_for_talks_in_next_event(self):
        soon = self.client.get(reverse('talk-detail', args=[self.next_talk.pk])).content.decode()
        later = self.client.get(reverse('talk-detail', args=[self.later_talk.pk])).content.decode()
        self.assertIn('p-chip--branded', soon)
        self.assertIn('p-icon--circle-of-friends', soon)
        self.assertIn('Next Summit', soon)
        self.assertNotIn('Next Summit', later)

    def test_scheduled_notification_names_the_event(self):
        html = self.client.get(reverse('talk-detail', args=[self.later_talk.pk])).content.decode()
        self.assertIn('p-notification--information', html)
        self.assertIn('Scheduled for Ubuntu Summit 27.04', html)

    def test_requested_notification_for_pending_talks(self):
        self.later_talk.status = 'pending'
        self.later_talk.save()
        html = self.client.get(reverse('talk-detail', args=[self.later_talk.pk])).content.decode()
        self.assertIn('Requested for Ubuntu Summit 27.04', html)
        self.assertNotIn('Scheduled for', html)
