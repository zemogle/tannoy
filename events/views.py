import csv
import json

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

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
        if (
            not isinstance(entry, dict)
            or entry.get('type') not in ('talk', 'break')
            or not _is_int(entry.get('id'))
        ):
            return _error(
                'Each item must be {"type": "talk" | "break", "id": <integer>}.'
            )
        refs.append((entry['type'], entry['id']))

    if len(set(refs)) != len(refs):
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
        ProgrammeItem.objects.filter(programme=programme).delete()
        ProgrammeItem.objects.bulk_create([
            ProgrammeItem(
                programme=programme,
                position=position,
                talk=talks[ident] if kind == 'talk' else None,
                brk=breaks[ident] if kind == 'break' else None,
            )
            for position, (kind, ident) in enumerate(refs, start=1)
        ])

    return JsonResponse(_programme_payload(programme))


@staff_member_required
def submissions(request):
    """Review list of talk submissions with filters and status counts."""
    talks = Talk.objects.select_related('event', 'speaker')

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
                talk.event.date.strftime('%d.%m'),
            ])
        return response

    counts = Talk.objects.aggregate(
        accepted=Count('id', filter=Q(status=Status.ACCEPTED)),
        pending=Count('id', filter=Q(status=Status.PENDING)),
        rejected=Count('id', filter=Q(status=Status.REJECTED)),
    )

    return render(request, 'events/submissions.html', {
        'section': 'submissions',
        'talks': talks,
        'events': Event.objects.order_by('date'),
        'statuses': Status.choices,
        'counts': counts,
        'search': search,
        'title': title,
        'status': status,
        'event_id': event_id,
    })


@staff_member_required
@require_http_methods(['GET', 'POST'])
def talk_detail(request, talk_id):
    """
    GET: show a talk with its speakers, summary and current status.
    POST (action=accept|reject): set the talk's status, then redirect back.
    """
    talk = get_object_or_404(Talk.objects.select_related('event', 'speaker'), pk=talk_id)

    if request.method == 'POST':
        new_status = {
            'accept': Status.ACCEPTED,
            'reject': Status.REJECTED,
        }.get(request.POST.get('action'))
        if new_status is None:
            return HttpResponseBadRequest('Unknown action.')
        talk.status = new_status
        talk.save(update_fields=['status'])
        return redirect('talk-detail', talk_id=talk.id)

    # Speakers include the talk's main speaker and any linked via Speaker.talks.
    speakers = (
        Speaker.objects.filter(Q(pk=talk.speaker_id) | Q(talks=talk))
        .distinct()
        .order_by('name')
    )
    return render(request, 'events/talk_detail.html', {
        'section': 'submissions',
        'talk': talk,
        'speakers': speakers,
    })
