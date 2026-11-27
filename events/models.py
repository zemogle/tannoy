import datetime
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .banner import render_talk_banner
from .schedule import DAY_END, DAY_START, DEFAULT_TALK_MINUTES, FIXED_BREAK_MINUTES_PER_DAY
from .discourse import client_from_settings, publish_talk
from .storage import HeadshotStorage


class Status(models.TextChoices):
    PENDING = 'pending', 'Pending'
    ACCEPTED = 'accepted', 'Accepted'
    REJECTED = 'rejected', 'Rejected'
    PUBLISHED = 'published', 'Published'

class Topic(models.TextChoices):
    DEVICES = 'topic-devices', 'Devices'
    SECURITY = 'topic-security', 'Security'
    DEV = 'topic-dev', 'Developers'
    AI = 'topic-ai', 'AI'


class Speaker(models.Model):
    name = models.CharField(max_length=200)
    headshot = models.ImageField(
        upload_to='speakers/',
        storage=HeadshotStorage(),
        blank=True,
    )
    affiliation = models.CharField(
        'company / affiliation',
        max_length=200,
        blank=True,
    )
    requests_sponsorship = models.BooleanField('requests sponsorship', default=False)
    email = models.EmailField(blank=True)
    pronouns = models.CharField(max_length=50, blank=True)
    username = models.CharField(max_length=150, blank=True)
    bio = models.TextField(blank=True)
    in_person = models.BooleanField(default=True)
    submitter = models.BooleanField(default=False)
    talks = models.ManyToManyField('Talk', related_name='speakers', blank=True)

    def __str__(self):
        return self.name


class Unconference(models.Model):
    speaker = models.ForeignKey(
        Speaker,
        on_delete=models.CASCADE,
        related_name='unconferences',
    )
    description = models.TextField(blank=True)
    needs_power = models.BooleanField('needs power', default=False)

    def __str__(self):
        return f'{self.speaker.name}: {self.description[:50]}'


class Event(models.Model):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=5)
    start_date = models.DateField()
    end_date = models.DateField()

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def capacity(self):
        """
        Percentage of the event's programme time that is booked: accepted talks
        plus the fixed breaks for each day, out of the time between DAY_START and DAY_END.
        """
        days = (self.end_date - self.start_date).days + 1
        day_minutes = (
            datetime.datetime.combine(datetime.date.min, DAY_END)
            - datetime.datetime.combine(datetime.date.min, DAY_START)
        ).seconds // 60
        available = days * day_minutes
        talk_minutes = sum(
            talk.duration_minutes or DEFAULT_TALK_MINUTES
            for talk in self.talks.filter(status=Status.ACCEPTED)
        )
        booked = days * FIXED_BREAK_MINUTES_PER_DAY + talk_minutes
        return round(100 * booked / available, 1) if available else 0.0

    @property
    def is_next(self):
        """True if this is the earliest event that has not finished yet (today counts)."""
        upcoming = (
            Event.objects.filter(end_date__gte=timezone.localdate())
            .order_by('start_date', 'pk')
            .first()
        )
        return upcoming is not None and upcoming.pk == self.pk

    def __str__(self):
        return f'{self.name} ({self.start_date})'


class Talk(models.Model):
    LIGHTNING = 5
    SHORT = 25
    LONG = 45
    DURATION_CHOICES = [  # noqa: RUF012
        (LIGHTNING, 'Lightning talk (5 mins)'),
        (SHORT, 'Short talk (25 mins)'),
        (LONG, 'Long talk (45 mins)'),
    ]

    title = models.CharField(max_length=200)
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='talks',
    )
    speaker = models.ForeignKey(
        Speaker,
        on_delete=models.PROTECT,
        related_name='presented_talks',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    submitted_at = models.DateTimeField(default=timezone.now)
    abstract = models.TextField(blank=True)
    topic = models.CharField(
        max_length=32,
        choices=Topic.choices,
        blank=True,
    )
    duration_minutes = models.PositiveIntegerField(
        choices=DURATION_CHOICES,
        null=True,
        blank=True,
    )
    published = models.BooleanField(default=False)
    slides = models.URLField(blank=True)
    youtube_url = models.URLField('YouTube link', blank=True)
    byod = models.BooleanField('BYOD', default=False)
    discourse_id = models.IntegerField(null=True, blank=True)
    published_topic_id = models.IntegerField(null=True, blank=True)
    banner = models.FileField(
        upload_to='banners/',
        storage=HeadshotStorage(),
        blank=True,
    )

    class Meta:
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=models.Q(duration_minutes__isnull=True)
                | models.Q(duration_minutes__in=[5, 25, 45]),
                name='talk_duration_is_valid_choice',
            ),
        ]

    def allowed_actions(self):
        """
        Editorial actions available for the talk's current state.

        Pending: accept, reject. Accepted: reject, publish.
        Rejected: accept. Published: none (the talk is locked).
        """
        if self.published:
            return set()
        actions = set()
        if self.status != Status.ACCEPTED:
            actions.add('accept')
        if self.status != Status.REJECTED:
            actions.add('reject')
        if self.status == Status.ACCEPTED:
            actions.add('publish')
        return actions

    def generate_banner(self):
        """A 16:9 PIL image: the title above each speaker's headshot, name and affiliation."""
        return render_talk_banner(self)

    def submitter_speaker(self):
        """The speaker who submitted the talk: the flagged submitter, else the primary speaker."""
        return (
            Speaker.objects.filter(Q(pk=self.speaker_id) | Q(talks=self), submitter=True).first()
            or self.speaker
        )

    def publish(self, client=None, category=None):
        """
        Publish an accepted talk as a topic in the Discourse category, then lock it.

        Creates the topic, or updates it if it already exists on Discourse.
        Returns the Discourse topic ID.
        """
        if 'publish' not in self.allowed_actions():
            raise ValidationError(f'A talk with status {self.status!r} cannot be published.')
        if client is None:
            client, category = client_from_settings()
        self.published_topic_id = publish_talk(self, client, category)
        self.published = True
        self.save()
        return self.published_topic_id

    def __str__(self):
        return self.title


class Programme(models.Model):
    name = models.CharField(max_length=200)
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='programmes',
    )
    day = models.PositiveIntegerField()

    class Meta:
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(
                fields=['event', 'day'],
                name='unique_day_per_event',
            ),
        ]

    def __str__(self):
        return f'{self.name} - {self.event.name}'


class Break(models.Model):
    """A generic time window within a programme (e.g. lunch, coffee)."""

    programme = models.ForeignKey(
        Programme,
        on_delete=models.CASCADE,
        related_name='breaks',
    )
    name = models.CharField(max_length=200, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    class Meta:
        ordering = ['start_time']

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({'end_time': 'End time must be after start time.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.name or 'Break'
        return f'{label} ({self.start_time:%Y-%m-%d %H:%M} - {self.end_time:%H:%M})'


class ProgrammeItem(models.Model):
    """
    One slot in a programme's running order. Holds either a talk or a break,
    and `position` gives the order across both.
    """

    programme = models.ForeignKey(
        Programme,
        on_delete=models.CASCADE,
        related_name='items',
    )
    position = models.PositiveIntegerField()
    talk = models.ForeignKey(
        Talk,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='programme_items',
    )
    brk = models.ForeignKey(
        Break,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='programme_items',
    )

    class Meta:
        ordering = ['position']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(talk__isnull=False, brk__isnull=True)
                    | models.Q(talk__isnull=True, brk__isnull=False)
                ),
                name='item_is_talk_or_break',
            ),
            models.UniqueConstraint(
                fields=['programme', 'position'],
                name='unique_position_per_programme',
            ),
            models.UniqueConstraint(
                fields=['programme', 'talk'],
                condition=models.Q(talk__isnull=False),
                name='unique_talk_per_programme',
            ),
            models.UniqueConstraint(
                fields=['programme', 'brk'],
                condition=models.Q(brk__isnull=False),
                name='unique_break_per_programme',
            ),
        ]

    def clean(self):
        super().clean()
        if (self.talk_id is None) == (self.brk_id is None):
            raise ValidationError('An item must have exactly one of talk or break.')
        if self.talk_id and self.programme_id and self.talk.event_id != self.programme.event_id:
            raise ValidationError(
                'The talk must belong to the same event as the programme.'
            )
        if self.brk_id and self.programme_id and self.brk.programme_id != self.programme_id:
            raise ValidationError('The break must belong to this programme.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.talk.title if self.talk_id else str(self.brk)
        return f'{self.position}. {target} ({self.programme.name})'
