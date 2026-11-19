from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .storage import HeadshotStorage


class Status(models.TextChoices):
    PENDING = 'pending', 'Pending'
    ACCEPTED = 'accepted', 'Accepted'
    REJECTED = 'rejected', 'Rejected'

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
    talks = models.ManyToManyField('Talk', related_name='speakers', blank=True)

    def __str__(self):
        return self.name


class Event(models.Model):
    name = models.CharField(max_length=200)
    date = models.DateField()

    def __str__(self):
        return f'{self.name} ({self.date})'


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
    discourse_id = models.IntegerField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(
        choices=DURATION_CHOICES,
        null=True,
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
