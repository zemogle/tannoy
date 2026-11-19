from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0009_speaker_headshot_s3_storage'),
    ]

    operations = [
        migrations.RenameField(
            model_name='speaker',
            old_name='is_sponsor',
            new_name='requests_sponsorship',
        ),
        migrations.AlterField(
            model_name='speaker',
            name='requests_sponsorship',
            field=models.BooleanField(default=False, verbose_name='requests sponsorship'),
        ),
    ]
