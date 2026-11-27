import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings

ALLOWED_DURATIONS = (5, 25, 45)

BULLET_RE = re.compile(r'^\*\s+\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$')
DURATION_RE = re.compile(r'(\d+)\s*minute')

INBOX_SETTINGS = ('DISCOURSE_URL', 'DISCOURSE_API_KEY', 'DISCOURSE_API_USERNAME')
PUBLISH_SETTINGS = INBOX_SETTINGS + ('DISCOURSE_CATEGORY',)


class DiscourseClient:
    def __init__(self, base_url, api_key, api_username):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.api_username = api_username

    def _request(self, method, path, params=None, body=None, username=None):
        url = f'{self.base_url}{path}'
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        data = None
        headers = {
            'Api-Key': self.api_key,
            'Api-Username': username or self.api_username,
            'Accept': 'application/json',
        }
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}

    def _paged_topics(self, path, params=None):
        topics = []
        page = 0
        while True:
            data = self._request('GET', path, params={**(params or {}), 'page': page})
            topic_list = data.get('topic_list', {})
            batch = topic_list.get('topics', [])
            if not batch:
                break
            topics.extend(batch)
            if not topic_list.get('more_topics_url'):
                break
            page += 1
        return topics

    def group_inbox_topics(self, group):
        return self._paged_topics(
            f'/topics/private-messages-group/{self.api_username}/{group}.json',
        )

    def first_post(self, topic_id):
        topic = self._request('GET', f'/t/{topic_id}.json')
        post_id = topic['post_stream']['posts'][0]['id']
        return self._request('GET', f'/posts/{post_id}.json')

    def get_topic(self, topic_id):
        try:
            return self._request('GET', f'/t/{topic_id}.json')
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def resolve_category(self, reference):
        """Return (id, slug) for a category given its numeric ID or slug."""
        categories = self._request('GET', '/categories.json')['category_list']['categories']
        for category in categories:
            if reference in (str(category['id']), category['slug']):
                return category['id'], category['slug']
        raise ValueError(f'No Discourse category matches {reference!r}.')

    def find_topic_by_title(self, category_slug, category_id, title):
        topics = self._paged_topics(f'/c/{category_slug}/{category_id}.json')
        for topic in topics:
            if topic['title'] == title:
                return topic
        return None

    def create_topic(self, category_id, title, raw, author_username):
        data = self._request('POST', '/posts.json', body={
            'title': title,
            'raw': raw,
            'category': category_id,
        }, username=author_username)
        return data['topic_id']

    def update_topic(self, topic_id, title, raw):
        topic = self._request('GET', f'/t/{topic_id}.json')
        post_id = topic['post_stream']['posts'][0]['id']
        self._request('PUT', f'/t/-/{topic_id}.json', body={'title': title})
        self._request('PUT', f'/posts/{post_id}.json', body={'post': {'raw': raw}})


def load_discourse_settings(names=INBOX_SETTINGS):
    file_values = load_env_file(settings.BASE_DIR / '.env')
    values = {}
    for name in names:
        values[name] = os.environ.get(name) or file_values.get(name)
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f'Missing settings: {", ".join(missing)}')
    return values


def client_from_settings():
    config = load_discourse_settings(PUBLISH_SETTINGS)
    client = DiscourseClient(
        config['DISCOURSE_URL'],
        config['DISCOURSE_API_KEY'],
        config['DISCOURSE_API_USERNAME'],
    )
    return client, config['DISCOURSE_CATEGORY']


def load_env_file(path):
    values = {}
    if not Path(path).exists():
        return values
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def build_topic_body(talk):
    """Markdown body for a published talk: abstract, then each speaker and bio."""
    lines = [
        '### Abstract',
        '',
        talk.abstract or '_No abstract provided._',
        '',
        '### Speaker Bio',
        '',
    ]
    speakers = [talk.speaker] + [
        speaker for speaker in talk.speakers.all() if speaker.pk != talk.speaker_id
    ]
    for speaker in speakers:
        lines.append(f'**{speaker.name}**')
        if speaker.affiliation:
            lines.append(f'*{speaker.affiliation}*')
        if speaker.bio:
            lines += ['', speaker.bio]
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def publish_talk(talk, client, category_reference):
    """
    Create or update the Discourse topic for a talk and return its topic ID.

    An existing topic is found first by the stored published_topic_id, then by
    title within the category, so re-publishing never creates a duplicate.
    A new topic is authored by the talk's submitter.
    """
    submitter = talk.submitter_speaker()
    if not submitter.username:
        raise ValueError(
            f'{submitter.name} has no Discourse username, so the topic cannot be authored.'
        )
    category_id, category_slug = client.resolve_category(category_reference)
    title = talk.title
    raw = build_topic_body(talk)

    if talk.published_topic_id and client.get_topic(talk.published_topic_id):
        client.update_topic(talk.published_topic_id, title, raw)
        return talk.published_topic_id

    match = client.find_topic_by_title(category_slug, category_id, title)
    if match:
        client.update_topic(match['id'], title, raw)
        return match['id']

    return client.create_topic(category_id, title, raw, submitter.username)


def parse_sections(raw):
    """
    Convert the markdown body of a CFP submission into
    {heading: {field: value}}. Multi-line values (such as the abstract)
    are joined with newlines.
    """
    sections = {}
    current = None
    multiline = None
    for line in raw.splitlines():
        line = line.rstrip()
        if line.startswith('### '):
            current = line[4:].strip()
            sections[current] = {}
            multiline = None
            continue
        if line.startswith('## '):
            current = None
            multiline = None
            continue
        if current is None:
            continue
        if line.strip() == '**Abstract:**':
            multiline = 'Abstract'
            sections[current][multiline] = ''
            continue
        match = BULLET_RE.match(line)
        if match:
            multiline = None
            sections[current][match.group('key').strip()] = match.group('value').strip()
            continue
        if multiline and line.strip():
            existing = sections[current][multiline]
            sections[current][multiline] = f'{existing}\n{line.strip()}'.strip()
    return sections


def _clean(value):
    if value is None:
        return None
    value = value.strip()
    if value in ('', 'Not provided'):
        return None
    return value


def _is_yes(value):
    return (value or '').strip().lower() == 'yes'


def parse_duration(session_type):
    match = DURATION_RE.search(session_type or '')
    if not match:
        return None
    minutes = int(match.group(1))
    return minutes if minutes in ALLOWED_DURATIONS else None


def _speaker_fields(fields):
    in_person = _clean(fields.get('In-person attendance'))
    sponsorship = _clean(fields.get('Travel sponsorship'))
    return {
        'name': _clean(fields.get('Name')),
        'email': _clean(fields.get('Email')),
        'pronouns': _clean(fields.get('Pronouns')),
        'affiliation': _clean(fields.get('Affiliation')),
        'username': _clean(fields.get('Discourse username')),
        'bio': _clean(fields.get('Bio')),
        'in_person': None if in_person is None else _is_yes(in_person),
        'requests_sponsorship': None if sponsorship is None else _is_yes(sponsorship),
    }


def _slides_and_byod(value):
    value = _clean(value)
    if value is None:
        return {'slides': None, 'byod': False}
    if value.lower().startswith(('http://', 'https://')):
        return {'slides': value, 'byod': False}
    return {'slides': None, 'byod': 'byod' in value.lower()}


def parse_submission(raw):
    sections = parse_sections(raw)
    primary = {
        **sections.get('Primary speaker', {}),
        **sections.get('Speaker needs', {}),
    }
    session = sections.get('Session', {})
    unconference = sections.get('Unconference', {})

    co_speakers = []
    for heading, fields in sections.items():
        if not heading.startswith('Co-speaker'):
            continue
        included = _clean(fields.get('Included'))
        if included is None:
            included_flag = _clean(fields.get('Name')) is not None
        else:
            included_flag = _is_yes(included)
        if included_flag:
            co_speakers.append(_speaker_fields(fields))

    wants_unconference = _is_yes(unconference.get('Would you like an unconference slot?'))
    return {
        'speaker': _speaker_fields(primary),
        'co_speakers': co_speakers,
        'talk': {
            'title': _clean(session.get('Title')),
            'abstract': _clean(session.get('Abstract')) or '',
            'duration_minutes': parse_duration(session.get('Session type')),
            **_slides_and_byod(session.get('Google Slides/BYOD')),
        },
        'unconference': (
            {'description': _clean(unconference.get('Idea')) or ''}
            if wants_unconference else None
        ),
    }
