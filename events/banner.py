from functools import cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_DIR = Path(__file__).resolve().parent
DESIGN_DIR = APP_DIR / 'design'
BACKGROUND_PATH = DESIGN_DIR / 'summit-background.png'
TAG_PATH = DESIGN_DIR / 'Tag-CoF-Orange-Digital.png'
FONT_DIR = APP_DIR / 'static' / 'events' / 'fonts'

WIDTH = 1920
HEIGHT = 1080

TAG_X = 108
TAG_Y = 202
TAG_WIDTH = 62
EVENT_X = 546
TITLE_X = 980
TOP = 248
HEADSHOT_WIDTH = 202
HEADSHOT_HEIGHT = 270
HEADSHOT_GAP = 24
SPEAKER_NAME_TOP = 776
SPEAKER_NAME_TOP_NO_HEADSHOTS = 492
SPEAKER_HEADSHOT_TOP = SPEAKER_NAME_TOP - HEADSHOT_GAP - HEADSHOT_HEIGHT
TWO_SPEAKER_PITCH = 434
THREE_SPEAKER_PITCH = 217
SPEAKER_TEXT_GAP = 16
SINGLE_SPEAKER_TEXT_WIDTH = 400
TITLE_MAX_WIDTH = WIDTH - TITLE_X - 100

LINE_PITCH = 74
NAME_LINE_PITCH = 28
AFFILIATION_GAP = 38
AFFILIATION_LINE_PITCH = 26
EVENT_COLOUR = (170, 170, 170)
TITLE_COLOUR = (255, 255, 255)
NAME_COLOUR = (255, 255, 255)
AFFILIATION_COLOUR = (150, 150, 150)


def _fonts():
    return {
        'event': ImageFont.truetype(str(FONT_DIR / 'Ubuntu-L.ttf'), 64),
        'title': ImageFont.truetype(str(FONT_DIR / 'Ubuntu-L.ttf'), 64),
        'name': ImageFont.truetype(str(FONT_DIR / 'Ubuntu-R.ttf'), 30),
        'affiliation': ImageFont.truetype(str(FONT_DIR / 'Ubuntu-L.ttf'), 24),
    }


def _wrap(draw, text, font, max_width):
    lines = []
    for paragraph in text.splitlines() or ['']:
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if draw.textlength(candidate, font=font) <= max_width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def load_background():
    return Image.open(BACKGROUND_PATH).convert('RGB').resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def _tag():
    tag = Image.open(TAG_PATH).convert('RGBA')
    height = round(TAG_WIDTH * tag.height / tag.width)
    return tag.resize((TAG_WIDTH, height), Image.Resampling.LANCZOS)


def event_label(event):
    return f'Ubuntu Summit {event.code}'


def _baseline_for_cap_top(cap_top, font):
    """Baseline y that puts the top of capital letters at cap_top."""
    return cap_top - font.getbbox('H', anchor='ls')[1]


def _draw_line(draw, text, font, x, cap_top, fill):
    draw.text((x, _baseline_for_cap_top(cap_top, font)), text, font=font, fill=fill, anchor='ls')


@cache
def _face_detector():
    return cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')


def _face_centre(image):
    """Centre (x, y) of the largest detected face, or None if no face is found."""
    grey = np.array(ImageOps.grayscale(image))
    faces = _face_detector().detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
    return x + w / 2, y + h / 2


def _portrait_crop(image, aspect=HEADSHOT_WIDTH / HEADSHOT_HEIGHT):
    """Largest crop with the given width:height ratio, centred on the speaker's face."""
    width, height = image.size
    if width / height > aspect:
        crop_height = height
        crop_width = round(height * aspect)
    else:
        crop_width = width
        crop_height = round(width / aspect)
    centre_x, centre_y = _face_centre(image) or (width / 2, height / 2)
    left = min(max(centre_x - crop_width / 2, 0), width - crop_width)
    top = min(max(centre_y - crop_height / 2, 0), height - crop_height)
    left, top = round(left), round(top)
    return image.crop((left, top, left + crop_width, top + crop_height))


def _headshot_thumbnail(image, width, height):
    image = ImageOps.exif_transpose(image.convert('RGB'))
    cropped = _portrait_crop(image, width / height)
    return ImageOps.grayscale(cropped).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)


def _headshot_image(speaker, width, height):
    with speaker.headshot.open('rb') as handle:
        return _headshot_thumbnail(Image.open(handle), width, height)


def banner_speakers(talk):
    """The main speaker first, then any other speakers on the talk."""
    others = [speaker for speaker in talk.speakers.all() if speaker.pk != talk.speaker_id]
    return [talk.speaker] + others


def render_talk_banner(talk):
    """
    Render the 16:9 Ubuntu Summit banner: the event name on the left, the talk
    title, and a row of speakers, each with a headshot, name and affiliation.
    """
    fonts = _fonts()
    canvas = load_background()
    tag = _tag()
    canvas.paste(tag, (TAG_X, TAG_Y), tag)
    draw = ImageDraw.Draw(canvas)

    for index, word in enumerate(event_label(talk.event).split()):
        _draw_line(draw, word, fonts['event'], EVENT_X, TOP + index * LINE_PITCH, EVENT_COLOUR)

    title_lines = _wrap(draw, talk.title, fonts['title'], TITLE_MAX_WIDTH)
    for index, line in enumerate(title_lines):
        _draw_line(draw, line, fonts['title'], TITLE_X, TOP + index * LINE_PITCH, TITLE_COLOUR)

    speakers = banner_speakers(talk)
    if len(speakers) == 1:
        pitch, text_width = 0, SINGLE_SPEAKER_TEXT_WIDTH
    elif len(speakers) == 2:
        pitch, text_width = TWO_SPEAKER_PITCH, TWO_SPEAKER_PITCH - SPEAKER_TEXT_GAP
    else:
        pitch, text_width = THREE_SPEAKER_PITCH, THREE_SPEAKER_PITCH - SPEAKER_TEXT_GAP

    show_headshots = all(speaker.headshot for speaker in speakers)
    name_top = SPEAKER_NAME_TOP if show_headshots else SPEAKER_NAME_TOP_NO_HEADSHOTS

    for index, speaker in enumerate(speakers):
        x = TITLE_X + index * pitch

        if show_headshots:
            thumb = _headshot_image(speaker, HEADSHOT_WIDTH, HEADSHOT_HEIGHT)
            canvas.paste(thumb, (x, SPEAKER_HEADSHOT_TOP))

        cap_top = name_top
        name_lines = _wrap(draw, speaker.name, fonts['name'], text_width)
        for name_line in name_lines:
            _draw_line(draw, name_line, fonts['name'], x, cap_top, NAME_COLOUR)
            cap_top += NAME_LINE_PITCH
        if speaker.affiliation:
            cap_top = cap_top - NAME_LINE_PITCH + AFFILIATION_GAP
            for affiliation_line in _wrap(draw, speaker.affiliation, fonts['affiliation'], text_width):
                _draw_line(draw, affiliation_line, fonts['affiliation'], x, cap_top, AFFILIATION_COLOUR)
                cap_top += AFFILIATION_LINE_PITCH
    return canvas
