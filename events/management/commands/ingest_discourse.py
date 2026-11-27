import datetime
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from events.discourse import (
    INBOX_SETTINGS,
    DiscourseClient,
    load_discourse_settings as _load_settings,
    parse_submission,
)
from events.models import Event, Speaker, Talk, Unconference

CFP_PREFIX = '[CFP Submission]'


def load_discourse_settings():
    try:
        return _load_settings(INBOX_SETTINGS)
    except ValueError as error:
        raise CommandError(str(error))


def _without_none(data):
    return {key: value for key, value in data.items() if value is not None}


class Command(BaseCommand):
    help = 'Ingest CFP submissions from the Discourse group inbox.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--event-code',
            required=True,
            help='Code of the Event the talks belong to.',
        )
        parser.add_argument(
            '--group',
            default='ubuntu-summit-core',
            help='Discourse group whose message inbox is read.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the parsed submissions and roll back all changes.',
        )

    def handle(self, *args, **options):
        try:
            event = Event.objects.get(code=options['event_code'])
        except Event.DoesNotExist:
            raise CommandError(f'No event with code {options["event_code"]!r}.')

        config = load_discourse_settings()
        client = DiscourseClient(
            config['DISCOURSE_URL'],
            config['DISCOURSE_API_KEY'],
            config['DISCOURSE_API_USERNAME'],
        )

        topics = [
            topic for topic in client.group_inbox_topics(options['group'])
            if topic['title'].startswith(CFP_PREFIX)
        ]
        self.stdout.write(f'Found {len(topics)} CFP submission(s).')

        counts = {'talks': 0, 'speakers': 0, 'unconferences': 0}
        with transaction.atomic():
            for topic in topics:
                post = client.first_post(topic['id'])
                parsed = parse_submission(post['raw'])
                if options['dry_run']:
                    self.stdout.write(json.dumps(
                        {'topic_id': topic['id'], **parsed}, indent=2,
                    ))
                self._ingest(topic, post, parsed, event, counts)
            if options['dry_run']:
                transaction.set_rollback(True)

        prefix = 'Would create or update' if options['dry_run'] else 'Created or updated'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}: {counts["talks"]} talk(s), '
            f'{counts["speakers"]} speaker(s), '
            f'{counts["unconferences"]} unconference(s).'
        ))

    def _ingest(self, topic, post, parsed, event, counts):
        if not parsed['speaker']['name']:
            self.stderr.write(f'Skipping topic {topic["id"]}: no primary speaker name.')
            return
        primary = self._upsert_speaker({**parsed['speaker'], 'submitter': True})
        counts['speakers'] += 1
        co_speakers = []
        for co_data in parsed['co_speakers']:
            co_speaker = self._upsert_speaker(co_data)
            if co_speaker is not None:
                co_speakers.append(co_speaker)
                counts['speakers'] += 1

        talk_data = parsed['talk']
        title = talk_data['title'] or topic['title'].removeprefix(CFP_PREFIX).strip()
        talk, _ = Talk.objects.update_or_create(
            discourse_id=topic['id'],
            defaults=_without_none({
                'title': title,
                'event': event,
                'speaker': primary,
                'abstract': talk_data['abstract'],
                'duration_minutes': talk_data['duration_minutes'],
                'slides': talk_data['slides'],
                'byod': talk_data['byod'],
                'submitted_at': datetime.datetime.fromisoformat(post['created_at']),
            }),
        )
        talk.speakers.add(primary, *co_speakers)
        counts['talks'] += 1

        if parsed['unconference'] is not None:
            Unconference.objects.get_or_create(
                speaker=primary,
                description=parsed['unconference']['description'],
            )
            counts['unconferences'] += 1

    def _upsert_speaker(self, data):
        if not data['name']:
            return None
        if data['email']:
            lookup = {'email__iexact': data['email']}
        elif data['username']:
            lookup = {'username__iexact': data['username']}
        else:
            lookup = {'name__iexact': data['name']}

        speaker = Speaker.objects.filter(**lookup).first()
        fields = _without_none(data)
        if speaker is None:
            return Speaker.objects.create(**fields)
        changed = False
        for field, value in fields.items():
            if not getattr(speaker, field):
                setattr(speaker, field, value)
                changed = True
        if changed:
            speaker.save()
        return speaker
