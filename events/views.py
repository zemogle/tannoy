import csv
import datetime
import io
import json
from zoneinfo import ZoneInfo

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import DetailView
from django.views.decorators.http import require_POST, require_http_methods

from .models import Break, Event, Programme, ProgrammeItem, Speaker, Status, Talk


def _error(message, status=400, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


def _item_payload(item):
    if item.talk_id:
        return {
            'type': 'talk',
            'id': item.talk_id,
            'title': item.talk.title,
            'position': item.position,
        }
    return {
        'type': 'break',
        'id': item.brk_id,
        'name': item.brk.name,
        'start_time': item.brk.start_time.isoformat(),
        'end_time': item.brk.end_time.isoformat(),
        'position': item.position,
    }


def _programme_payload(programme):
    items = (
        ProgrammeItem.objects.filter(programme=programme)
        .select_related('talk', 'brk')
        .order_by('position')
    )
    return {
        'programme_id': programme.id,
        'items': [_item_payload(item) for item in items],
    }


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


_ITEM_FORMAT_ERROR = (
    'Each item must be {"type": "talk" | "break", "id": <integer>} '
    'or {"type": "new_break", "name": <text>, "minutes": <integer>}.'
)
NEW_BREAK_MINUTES = (20, 30, 60, 90)
DAY_START = datetime.time(9, 30)
DAY_TIMEZONE = ZoneInfo('Europe/London')
DEFAULT_TALK_MINUTES = 25


def _resolve_running_order(programme, refs, talks, breaks):
    """
    Turn (kind, id) references into talk and break objects. Every item starts
    when the previous one ends, so each break's stored times are set from the
    running order. New breaks are created here.
    """
    date = programme.event.start_date + datetime.timedelta(days=programme.day - 1)
    cursor = datetime.datetime.combine(date, DAY_START, tzinfo=DAY_TIMEZONE)
    resolved = []
    for kind, ident in refs:
        if kind == 'talk':
            talk = talks[ident]
            cursor += datetime.timedelta(minutes=talk.duration_minutes or DEFAULT_TALK_MINUTES)
            resolved.append(('talk', talk))
        elif kind == 'break':
            brk = breaks[ident]
            length = brk.end_time - brk.start_time
            brk.start_time = cursor
            brk.end_time = cursor + length
            brk.save(update_fields=['start_time', 'end_time'])
            cursor = brk.end_time
            resolved.append(('break', brk))
        else:
            name, minutes = ident
            start = cursor
            cursor = start + datetime.timedelta(minutes=minutes)
            brk = Break.objects.create(
                programme=programme, name=name, start_time=start, end_time=cursor,
            )
            resolved.append(('break', brk))
    return resolved


@require_http_methods(['GET', 'PUT'])
def programme_items(request, programme_id):
    """
    GET: return the programme's running order (talks and breaks).
    PUT: replace the running order.

    PUT body (JSON):
        {"items": [
            {"type": "talk", "id": 3},
            {"type": "break", "id": 1},
            {"type": "talk", "id": 1}
        ]}
    Position is 1-based and follows list order. Breaks must already exist
    in this programme.
    Requires an authenticated staff user and the X-CSRFToken header.
    """
    if not request.user.is_authenticated:
        return _error('Authentication required.', status=401)
    if not request.user.is_staff:
        return _error('Staff permission required.', status=403)

    programme = get_object_or_404(Programme, pk=programme_id)

    if request.method == 'GET':
        return JsonResponse(_programme_payload(programme))

    try:
        data = json.loads(request.body or b'null')
    except json.JSONDecodeError:
        return _error('Request body must be valid JSON.')

    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return _error('"items" must be a list.')

    refs = []
    for entry in items:
        if not isinstance(entry, dict):
            return _error(_ITEM_FORMAT_ERROR)
        if entry.get('type') == 'new_break':
            name = entry.get('name')
            minutes = entry.get('minutes')
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 200
                or minutes not in NEW_BREAK_MINUTES
            ):
                return _error(
                    'New breaks must be {"type": "new_break", "name": <text>, '
                    '"minutes": 20 | 30 | 60 | 90}.'
                )
            refs.append(('new_break', (name.strip(), minutes)))
            continue
        if (
            entry.get('type') not in ('talk', 'break')
            or not _is_int(entry.get('id'))
        ):
            return _error(_ITEM_FORMAT_ERROR)
        refs.append((entry['type'], entry['id']))

    existing_refs = [ref for ref in refs if ref[0] != 'new_break']
    if len(set(existing_refs)) != len(existing_refs):
        return _error('Items must not contain duplicates.')

    talk_ids = [ident for kind, ident in refs if kind == 'talk']
    break_ids = [ident for kind, ident in refs if kind == 'break']

    talks = Talk.objects.in_bulk(talk_ids)
    missing_talks = [i for i in talk_ids if i not in talks]
    if missing_talks:
        return _error('Unknown talk ids.', missing={'talk': missing_talks})

    wrong_event = [i for i in talk_ids if talks[i].event_id != programme.event_id]
    if wrong_event:
        return _error(
            'Talks must belong to the same event as the programme.',
            talk_ids=wrong_event,
        )

    breaks = Break.objects.in_bulk(break_ids)
    missing_breaks = [i for i in break_ids if i not in breaks]
    if missing_breaks:
        return _error('Unknown break ids.', missing={'break': missing_breaks})

    wrong_programme = [i for i in break_ids if breaks[i].programme_id != programme.id]
    if wrong_programme:
        return _error(
            'Breaks must belong to this programme.',
            break_ids=wrong_programme,
        )

    # Replace the whole running order in one transaction. Deleting first
    # avoids tripping the unique position constraint while positions shift.
    # bulk_create bypasses ProgrammeItem.save(), so the checks above are the
    # validation for this path.
    with transaction.atomic():
        resolved = _resolve_running_order(programme, refs, talks, breaks)
        ProgrammeItem.objects.filter(programme=programme).delete()
        ProgrammeItem.objects.bulk_create([
            ProgrammeItem(
                programme=programme,
                position=position,
                talk=obj if kind == 'talk' else None,
                brk=obj if kind == 'break' else None,
            )
            for position, (kind, obj) in enumerate(resolved, start=1)
        ])

    return JsonResponse(_programme_payload(programme))


def _schedule_with_times(programme):
    """
    Running order for display: each item starts when the previous one ends,
    using talk durations and break lengths. Times are in the summit's timezone.
    """
    date = programme.event.start_date + datetime.timedelta(days=programme.day - 1)
    cursor = datetime.datetime.combine(date, DAY_START, tzinfo=DAY_TIMEZONE)
    schedule = []
    for item in programme.items.select_related('talk', 'brk').order_by('position'):
        if item.talk_id:
            start = cursor
            cursor += datetime.timedelta(minutes=item.talk.duration_minutes or DEFAULT_TALK_MINUTES)
            schedule.append({
                'type': 'talk', 'id': item.talk.pk, 'label': item.talk.title,
                'duration': item.talk.duration_minutes,
                'time': start.astimezone(DAY_TIMEZONE).strftime('%H:%M'),
            })
        else:
            brk = item.brk
            start = cursor
            cursor += brk.end_time - brk.start_time
            schedule.append({
                'type': 'break', 'id': brk.pk, 'label': brk.name or 'Break',
                'time': start.astimezone(DAY_TIMEZONE).strftime('%H:%M'),
            })
    return schedule


def talk_speakers(talk):
    """All speakers on a talk: the main speaker plus any linked via Speaker.talks."""
    speakers = {talk.speaker.pk: talk.speaker}
    for speaker in talk.speakers.all():
        speakers.setdefault(speaker.pk, speaker)
    return sorted(speakers.values(), key=lambda speaker: speaker.name)


@staff_member_required
def submissions(request):
    """Review list of talk submissions with filters and status counts."""
    talks = Talk.objects.select_related('event', 'speaker').prefetch_related('speakers')

    search = request.GET.get('q', '').strip()
    title = request.GET.get('title', '').strip()
    status = request.GET.get('status', '')
    event_id = request.GET.get('event', '')

    if search:
        talks = talks.filter(
            Q(title__icontains=search)
            | Q(speaker__name__icontains=search)
            | Q(event__name__icontains=search)
        )
    if title:
        talks = talks.filter(title__icontains=title)
    if status in Status.values:
        talks = talks.filter(status=status)
    if event_id.isdigit():
        talks = talks.filter(event_id=int(event_id))

    # Counts reflect the filtered set, so they update with the filters.
    counts = talks.aggregate(
        accepted=Count('id', filter=Q(status=Status.ACCEPTED)),
        pending=Count('id', filter=Q(status=Status.PENDING)),
        rejected=Count('id', filter=Q(status=Status.REJECTED)),
    )

    talks = talks.order_by('-submitted_at')

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="submissions.csv"'
        writer = csv.writer(response)
        writer.writerow(['Talk title', 'Status', 'Submitter', 'Submitted date', 'Event'])
        for talk in talks:
            writer.writerow([
                talk.title,
                talk.get_status_display(),
                talk.speaker.name,
                talk.submitted_at.strftime('%d.%m.%y'),
                talk.event.start_date.strftime('%d.%m'),
            ])
        return response

    talks = list(talks)
    for talk in talks:
        talk.all_speakers = talk_speakers(talk)

    # One section per status, in review order: pending, accepted, rejected.
    sections = [
        {
            'key': status_value,
            'label': status_label,
            'talks': [talk for talk in talks if talk.status == status_value],
        }
        for status_value, status_label in (
            (Status.PENDING, 'Pending'),
            (Status.ACCEPTED, 'Accepted'),
            (Status.REJECTED, 'Rejected'),
        )
    ]

    return render(request, 'events/submissions.html', {
        'section': 'submissions',
        'talks': talks,
        'sections': sections,
        'events': Event.objects.order_by('start_date'),
        'statuses': Status.choices,
        'counts': counts,
        'search': search,
        'title': title,
        'status': status,
        'event_id': event_id,
    })


@require_POST
def api_talk_banner(request, talk_id):
    """
    POST: generate a talk's banner and store it in Talk.banner.
    Returns JSON: {"status": "ok", "talk_id": ..., "banner_url": ...} on success,
    or {"status": "error", "message": ...} otherwise.
    """
    if not request.user.is_staff:
        return JsonResponse({'status': 'error', 'message': 'Staff access required.'}, status=403)
    talk = Talk.objects.filter(pk=talk_id).first()
    if talk is None:
        return JsonResponse({'status': 'error', 'message': 'Talk not found.'}, status=404)
    if talk.status != Status.ACCEPTED:
        return JsonResponse(
            {'status': 'error', 'message': 'Only accepted talks have a banner.'}, status=400,
        )
    buffer = io.BytesIO()
    talk.generate_banner().save(buffer, format='PNG')
    if talk.banner:
        talk.banner.delete(save=False)
    talk.banner.save(f'talk-{talk.pk}.png', ContentFile(buffer.getvalue()), save=True)
    url = talk.banner.url
    if not url.startswith(('http://', 'https://', '/')):
        url = f'/{url}'
    return JsonResponse({'status': 'ok', 'talk_id': talk.pk, 'banner_url': url})


@method_decorator(staff_member_required, name='dispatch')
class TalkDetailView(DetailView):
    """
    GET: show a talk with its speakers, abstract and current status.
    POST (action=accept|reject|publish): apply an editorial action that is
    allowed for the talk's current state, then redirect back.
    """

    model = Talk
    template_name = 'events/talk_detail.html'
    context_object_name = 'talk'
    pk_url_kwarg = 'talk_id'
    http_method_names = ['get', 'post', 'head', 'options']

    ACTIONS = {  # noqa: RUF012
        'accept': Status.ACCEPTED,
        'reject': Status.REJECTED,
    }

    def get_queryset(self):
        return Talk.objects.select_related('event', 'speaker')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Speakers include the talk's main speaker and any linked via Speaker.talks.
        context['speakers'] = (
            Speaker.objects.filter(Q(pk=self.object.speaker_id) | Q(talks=self.object))
            .distinct()
            .order_by('name')
        )
        context['section'] = 'submissions'
        context['allowed_actions'] = self.object.allowed_actions()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get('action')
        if action not in self.ACTIONS and action != 'publish':
            return HttpResponseBadRequest('Unknown action.')
        if action not in self.object.allowed_actions():
            return HttpResponseBadRequest('Action not allowed for this talk.')

        if action == 'publish':
            try:
                self.object.publish()
            except (ValueError, OSError) as error:
                messages.error(request, f'Could not publish to Discourse: {error}')
            else:
                messages.success(request, 'Talk published to Discourse.')
        else:
            self.object.status = self.ACTIONS[action]
            self.object.save(update_fields=['status'])
        return redirect('talk-detail', talk_id=self.object.pk)


@staff_member_required
def events(request):
    """Upcoming event in its own table, then all events in date order."""
    today = timezone.localdate()
    all_events = list(Event.objects.annotate(talk_count=Count('talks')).order_by('start_date', 'pk'))
    upcoming = [event for event in all_events if event.end_date >= today]
    next_event = upcoming[0] if upcoming else None
    other_events = [event for event in all_events if event is not next_event]
    return render(request, 'events/events.html', {
        'section': 'events',
        'next_event': next_event,
        'other_events': other_events,
    })


@staff_member_required
def event_detail(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    talks = list(event.talks.select_related('speaker').order_by('title'))
    for talk in talks:
        talk.all_speakers = talk_speakers(talk)
    return render(request, 'events/event_detail.html', {
        'section': 'events',
        'event': event,
        'talks': talks,
        'programmes': event.programmes.order_by('day'),
        'is_next': event.is_next,
    })


@staff_member_required
def programme_builder(request, event_id):
    """
    Drag accepted talks and existing breaks into one column per event day.
    Each column saves its running order through the programme_items API.
    """
    event = get_object_or_404(Event, pk=event_id)
    day_count = (event.end_date - event.start_date).days + 1
    for day in range(1, day_count + 1):
        Programme.objects.get_or_create(
            event=event, day=day, defaults={'name': f'{event.name} - Day {day}'},
        )

    programmes = list(event.programmes.order_by('day'))
    for programme in programmes:
        programme.date = event.start_date + datetime.timedelta(days=programme.day - 1)
        programme.schedule = _schedule_with_times(programme)

    placed_talk_ids = set(
        ProgrammeItem.objects.filter(programme__event=event, talk__isnull=False)
        .values_list('talk_id', flat=True)
    )
    unscheduled = [
        talk for talk in event.talks.filter(status=Status.ACCEPTED).select_related('speaker')
        .order_by('title')
        if talk.pk not in placed_talk_ids
    ]
    return render(request, 'events/programme_builder.html', {
        'section': 'events',
        'event': event,
        'programmes': programmes,
        'unscheduled': unscheduled,
    })
