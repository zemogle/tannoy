import datetime
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .discourse import parse_duration, parse_sections, parse_submission
from .models import Event, Speaker, Talk, Unconference

SUBMISSION_WITH_UNCONFERENCE = """## New Ubuntu Summit CFP Proposal

### Primary speaker
* **Email:** edward.gomez@canonical.com
* **Name:** Edward Gomez
* **Pronouns:** Not provided
* **Affiliation:** Not provided
* **Discourse username:** zemogle
* **Headshot:** Not provided
* **Bio:** biobio

### Session
* **Title:** Open Source in Space
* **Session type:** Talk: 25 minutes
* **Difficulty:** Beginner
* **Google Slides/BYOD:** https://github.com/zemogle
**Abstract:**
Some absract

### Unconference
* **Would you like an unconference slot?:** Yes
* **Idea:** Demos

### Speaker needs
* **In-person attendance:** Yes
* **Accommodations:** Nope
* **Travel sponsorship:** No
* **UK Visa/ETA:** No
* **Home airport:**

### Co-speaker 1
* **Included:** No
* **Email:** Not provided
* **Name:** Not provided

### Privacy and review
* **Privacy notice accepted:** Yes
I confirm that I have reviewed this proposal and agree to the privacy notice.
"""

SUBMISSION_WITH_CO_SPEAKER = """## New Ubuntu Summit CFP Proposal

### Primary speaker
* **Email:** edward.gomez@canonical.com
* **Name:** sdgfhs
* **Pronouns:** Not provided
* **Affiliation:** fdhadsh
* **Discourse username:** Not provided
* **Headshot:** Not provided
* **Bio:** dsfhsadfh

### Session
* **Title:** dfghsadfhj
* **Session type:** Talk: 25 minutes
* **Difficulty:** Not provided
* **Google Slides/BYOD:** Not provided
**Abstract:**
First line of abstract.

Second paragraph of abstract.

### Unconference
* **Would you like an unconference slot?:** No
* **Idea:** Not provided

### Speaker needs
* **In-person attendance:** Yes
* **Accommodations:** None
* **Travel sponsorship:** Yes
* **UK Visa/ETA:** Consular Visa
* **Home airport:** CW:
* **Extra speakers:** 1

### Co-speaker 1
* **Email:** zemogle@gmail.com
* **Name:** Zemogle
* **Pronouns:** Not provided
* **Affiliation:** Not provided
* **Discourse username:** Not provided
* **Headshot:** Not provided
* **Bio:** Not provided
* **In-person attendance:** Not provided
* **Travel sponsorship:** Not provided

### Privacy and review
* **Privacy notice accepted:** Yes
I confirm that I have reviewed this proposal and agree to the privacy notice.
"""

INBOX_TOPICS = [
    {'id': 9, 'title': '[CFP Submission] Open Source in Space'},
    {'id': 13, 'title': '[CFP Submission] dfghsadfhj'},
    {'id': 7, 'title': 'Ubuntu Summit 26.10'},
]

POSTS = {
    9: {'raw': SUBMISSION_WITH_UNCONFERENCE, 'created_at': '2026-09-22T16:22:55.955Z'},
    13: {'raw': SUBMISSION_WITH_CO_SPEAKER, 'created_at': '2026-11-05T12:20:17.022Z'},
}


class ParseSubmissionTests(TestCase):
    def test_sections_are_keyed_by_heading_and_field(self):
        sections = parse_sections(SUBMISSION_WITH_UNCONFERENCE)
        self.assertEqual(sections['Session']['Title'], 'Open Source in Space')
        self.assertEqual(sections['Unconference']['Idea'], 'Demos')

    def test_multiline_abstract_is_joined(self):
        sections = parse_sections(SUBMISSION_WITH_CO_SPEAKER)
        self.assertEqual(
            sections['Session']['Abstract'],
            'First line of abstract.\nSecond paragraph of abstract.',
        )

    def test_primary_speaker_and_talk_fields(self):
        parsed = parse_submission(SUBMISSION_WITH_UNCONFERENCE)
        self.assertEqual(parsed['speaker']['name'], 'Edward Gomez')
        self.assertEqual(parsed['speaker']['email'], 'edward.gomez@canonical.com')
        self.assertEqual(parsed['speaker']['username'], 'zemogle')
        self.assertIsNone(parsed['speaker']['pronouns'])
        self.assertTrue(parsed['speaker']['in_person'])
        self.assertFalse(parsed['speaker']['requests_sponsorship'])
        self.assertEqual(parsed['talk'], {
            'title': 'Open Source in Space',
            'abstract': 'Some absract',
            'duration_minutes': 25,
            'slides': 'https://github.com/zemogle',
            'byod': False,
        })

    def test_unconference_only_when_requested(self):
        parsed = parse_submission(SUBMISSION_WITH_UNCONFERENCE)
        self.assertEqual(parsed['unconference'], {'description': 'Demos'})
        parsed = parse_submission(SUBMISSION_WITH_CO_SPEAKER)
        self.assertIsNone(parsed['unconference'])

    def test_excluded_co_speaker_is_skipped(self):
        parsed = parse_submission(SUBMISSION_WITH_UNCONFERENCE)
        self.assertEqual(parsed['co_speakers'], [])

    def test_co_speaker_without_included_flag_is_kept_when_named(self):
        parsed = parse_submission(SUBMISSION_WITH_CO_SPEAKER)
        self.assertEqual(len(parsed['co_speakers']), 1)
        self.assertEqual(parsed['co_speakers'][0]['name'], 'Zemogle')
        self.assertEqual(parsed['co_speakers'][0]['email'], 'zemogle@gmail.com')
        self.assertTrue(parsed['speaker']['requests_sponsorship'])

    def test_slides_url_is_captured(self):
        parsed = parse_submission(SUBMISSION_WITH_UNCONFERENCE)
        self.assertEqual(parsed['talk']['slides'], 'https://github.com/zemogle')
        self.assertFalse(parsed['talk']['byod'])

    def test_byod_is_captured_without_slides(self):
        raw = SUBMISSION_WITH_UNCONFERENCE.replace(
            '* **Google Slides/BYOD:** https://github.com/zemogle',
            '* **Google Slides/BYOD:** BYOD',
        )
        parsed = parse_submission(raw)
        self.assertIsNone(parsed['talk']['slides'])
        self.assertTrue(parsed['talk']['byod'])

    def test_not_provided_slides_are_empty(self):
        parsed = parse_submission(SUBMISSION_WITH_CO_SPEAKER)
        self.assertIsNone(parsed['talk']['slides'])
        self.assertFalse(parsed['talk']['byod'])

    def test_duration_mapping(self):
        self.assertEqual(parse_duration('Talk: 5 minutes'), 5)
        self.assertEqual(parse_duration('Talk: 45 minutes'), 45)
        self.assertIsNone(parse_duration('Talk: 30 minutes'))
        self.assertIsNone(parse_duration(None))


class IngestDiscourseCommandTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )

    def _run(self, *args):
        client = mock.Mock()
        client.group_inbox_topics.return_value = INBOX_TOPICS
        client.first_post.side_effect = lambda topic_id: {
            'raw': POSTS[topic_id]['raw'],
            'created_at': POSTS[topic_id]['created_at'],
        }
        out = io.StringIO()
        with mock.patch(
            'events.management.commands.ingest_discourse.DiscourseClient',
            return_value=client,
        ), mock.patch(
            'events.management.commands.ingest_discourse.load_discourse_settings',
            return_value={
                'DISCOURSE_URL': 'http://discourse.test',
                'DISCOURSE_API_KEY': 'key',
                'DISCOURSE_API_USERNAME': 'zemogle',
            },
        ):
            call_command('ingest_discourse', *args, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_ingest_creates_talks_speakers_and_unconference(self):
        self._run('--event-code', '26.10')
        talk = Talk.objects.get(discourse_id=9)
        self.assertEqual(talk.title, 'Open Source in Space')
        self.assertEqual(talk.event, self.event)
        self.assertEqual(talk.duration_minutes, 25)
        self.assertEqual(talk.slides, 'https://github.com/zemogle')
        self.assertFalse(talk.byod)
        self.assertEqual(talk.speaker.email, 'edward.gomez@canonical.com')
        self.assertTrue(talk.speaker.submitter)
        self.assertEqual(talk.submitted_at.isoformat(), '2026-09-22T16:22:55.955000+00:00')
        self.assertTrue(Unconference.objects.filter(speaker=talk.speaker, description='Demos').exists())

        talk2 = Talk.objects.get(discourse_id=13)
        self.assertEqual(talk2.abstract, 'First line of abstract.\nSecond paragraph of abstract.')
        self.assertEqual(talk2.slides, '')
        self.assertTrue(talk2.speaker.submitter)
        self.assertEqual(
            set(talk2.speakers.values_list('name', flat=True)),
            {'Edward Gomez', 'Zemogle'},
        )
        self.assertEqual(Unconference.objects.count(), 1)

    def test_non_cfp_topics_are_ignored(self):
        self._run('--event-code', '26.10')
        self.assertFalse(Talk.objects.filter(discourse_id=7).exists())

    def test_rerun_updates_instead_of_duplicating(self):
        self._run('--event-code', '26.10')
        self._run('--event-code', '26.10')
        self.assertEqual(Talk.objects.count(), 2)
        self.assertEqual(Speaker.objects.count(), 2)
        self.assertEqual(Unconference.objects.count(), 1)

    def test_same_email_is_one_speaker_and_keeps_existing_values(self):
        existing = Speaker.objects.create(
            name='Edward Gomez', email='edward.gomez@canonical.com',
            bio='Curated bio', affiliation='',
        )
        self._run('--event-code', '26.10')
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Edward Gomez')
        self.assertEqual(existing.bio, 'Curated bio')
        self.assertEqual(existing.affiliation, 'fdhadsh')
        self.assertEqual(Speaker.objects.filter(email='edward.gomez@canonical.com').count(), 1)
        self.assertEqual(Talk.objects.get(discourse_id=13).speaker, existing)

    def test_dry_run_writes_nothing(self):
        output = self._run('--event-code', '26.10', '--dry-run')
        self.assertIn('"topic_id": 9', output)
        self.assertEqual(Talk.objects.count(), 0)
        self.assertEqual(Speaker.objects.count(), 0)
        self.assertEqual(Unconference.objects.count(), 0)

    def test_unknown_event_code_is_rejected(self):
        with self.assertRaises(CommandError):
            self._run('--event-code', 'NOPE')
