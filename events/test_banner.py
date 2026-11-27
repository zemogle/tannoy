import re
import datetime
import io
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .banner import HEIGHT, WIDTH, load_background
from .models import Event, Speaker, Talk


def _png_bytes(colour):
    buffer = io.BytesIO()
    Image.new('RGB', (400, 300), colour).save(buffer, format='PNG')
    return buffer.getvalue()


class TalkBannerTests(TestCase):
    def setUp(self):
        event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )
        self.alex = Speaker.objects.create(name='Alex Morgan', affiliation='Canonical')
        self.jamie = Speaker.objects.create(name='Jamie Lee', affiliation='')
        self.talk = Talk.objects.create(
            title='Open Source in Space and Other Long Titles That Wrap',
            event=event, speaker=self.alex,
        )
        self.talk.speakers.add(self.alex, self.jamie)

    def test_banner_is_16_by_9(self):
        banner = self.talk.generate_banner()
        self.assertEqual(banner.size, (WIDTH, HEIGHT))
        self.assertEqual(WIDTH * 9, HEIGHT * 16)

    def test_banner_uses_template_background(self):
        banner = self.talk.generate_banner()
        self.assertEqual(banner.getpixel((1900, 1070)), load_background().getpixel((1900, 1070)))

    def test_orange_tag_sits_top_left(self):
        banner = self.talk.generate_banner()
        r, g, b = banner.getpixel((140, 230))[:3]
        self.assertTrue(r > 200 and g < 120 and b < 80)

    def test_event_name_is_split_across_lines(self):
        from .banner import banner_speakers, event_label

        self.assertEqual(event_label(self.talk.event).split(), ['Ubuntu', 'Summit', '26.10'])
        self.assertEqual(len(banner_speakers(self.talk)), 2)

    def test_banner_renders_for_single_speaker_talk(self):
        solo = Talk.objects.create(title='Solo talk', event=self.talk.event, speaker=self.jamie)
        banner = solo.generate_banner()
        self.assertEqual(banner.size, (WIDTH, HEIGHT))

    def test_no_headshots_shown_when_a_speaker_has_none(self):
        self.talk.speaker.headshot = 'speakers/alex.png'
        self.talk.speaker.save()
        with mock.patch('events.banner._headshot_thumbnail') as thumbnail:
            self.talk.generate_banner()
        thumbnail.assert_not_called()



class TalkBannerViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        event = Event.objects.create(
            name='Ubuntu Summit 26.10', code='26.10',
            start_date=datetime.date(2026, 11, 12), end_date=datetime.date(2026, 11, 13),
        )
        speaker = Speaker.objects.create(name='Alex Morgan', affiliation='Canonical')
        self.talk = Talk.objects.create(
            title='Open Source in Space', event=event, speaker=speaker, status='accepted',
        )
        self.talk.speakers.add(speaker)
        self.url = reverse('api-talk-banner', args=[self.talk.pk])
        self.admin_user = get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(self.admin_user)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_accepted_talk_banner_is_stored_and_reported_as_json(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['talk_id'], self.talk.pk)
        self.assertEqual(body['banner_url'], f'/media/banners/talk-{self.talk.pk}.png')

        saved = Path(self.media.name) / 'banners' / f'talk-{self.talk.pk}.png'
        self.assertTrue(saved.exists())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (WIDTH, HEIGHT))
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.banner.name, f'banners/talk-{self.talk.pk}.png')

    def test_regenerating_replaces_the_previous_banner(self):
        self.client.post(self.url)
        self.client.post(self.url)
        files = list((Path(self.media.name) / 'banners').iterdir())
        self.assertEqual(len(files), 1)

    def test_pending_talk_gets_json_error(self):
        self.talk.status = 'pending'
        self.talk.save()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')
        self.assertFalse((Path(self.media.name) / 'banners').exists())

    def test_unknown_talk_gets_json_404(self):
        response = self.client.post(reverse('api-talk-banner', args=[999999]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['status'], 'error')

    def test_banner_api_requires_staff(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'error')
        self.assertFalse((Path(self.media.name) / 'banners').exists())

    def test_banner_api_only_accepts_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_detail_enables_generate_button_when_accepted(self):
        response = self.client.get(reverse('talk-detail', args=[self.talk.pk]))
        button = re.search(r'<button[^>]*id="generate-banner"[^>]*>', response.content.decode())
        self.assertIsNotNone(button)
        self.assertNotIn('disabled', button.group(0))
        self.assertIn(f'data-url="{self.url}"', button.group(0))

    def test_detail_disables_generate_button_until_accepted(self):
        self.talk.status = 'pending'
        self.talk.save()
        response = self.client.get(reverse('talk-detail', args=[self.talk.pk]))
        button = re.search(r'<button[^>]*id="generate-banner"[^>]*>', response.content.decode())
        self.assertIn('disabled', button.group(0))

    def test_headshot_file_is_used_when_present(self):
        second = Speaker.objects.create(name='Jamie Lee', headshot='speakers/photo.png')
        self.talk.speakers.add(second)
        self.talk.speaker.headshot = 'speakers/photo.png'
        self.talk.speaker.save()
        buffer = io.BytesIO()
        Image.new('RGB', (400, 300), (200, 0, 0)).save(buffer, format='PNG')
        png = buffer.getvalue()
        with mock.patch(
            'django.db.models.fields.files.FieldFile.open',
            side_effect=lambda mode='rb': io.BytesIO(png),
        ) as opened:
            banner = self.talk.generate_banner()
        opened.assert_called()
        self.assertEqual(banner.size, (WIDTH, HEIGHT))


class SpeakerCentringTests(TestCase):
    def test_crop_is_centred_on_face_when_room_allows(self):
        from . import banner

        image = Image.new('RGB', (800, 600))
        with mock.patch.object(banner, '_face_centre', return_value=(400, 300)):
            cropped = banner._portrait_crop(image)
        self.assertEqual(cropped.size, (449, 600))

    def test_crop_stays_inside_image_near_an_edge(self):
        from . import banner

        image = Image.new('RGB', (800, 600))
        with mock.patch.object(banner, '_face_centre', return_value=(780, 40)):
            cropped = banner._portrait_crop(image)
        self.assertEqual(cropped.size, (449, 600))

    def test_no_face_falls_back_to_middle_of_photo(self):
        from . import banner

        image = Image.new('RGB', (800, 600))
        with mock.patch.object(banner, '_face_centre', return_value=None):
            cropped = banner._portrait_crop(image)
        self.assertEqual(cropped.size, (449, 600))

    def test_face_detector_finds_no_face_in_blank_image(self):
        from . import banner

        self.assertIsNone(banner._face_centre(Image.new('RGB', (300, 300), (120, 120, 120))))

    def test_headshot_thumbnail_is_greyscale_portrait_rectangle(self):
        from . import banner

        thumb = banner._headshot_thumbnail(Image.new("RGB", (400, 300), (200, 40, 40)), 202, 270)
        self.assertEqual(thumb.size, (202, 270))
        self.assertEqual(thumb.mode, 'RGB')
        r, g, b = thumb.getpixel((101, 135))
        self.assertEqual(r, g)
        self.assertEqual(g, b)
