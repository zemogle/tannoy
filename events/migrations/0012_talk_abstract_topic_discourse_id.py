from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0011_talk_duration_choices'),
    ]

    operations = [
        migrations.RenameField(
            model_name='talk',
            old_name='summary',
            new_name='abstract',
        ),
        migrations.AddField(
            model_name='talk',
            name='topic',
            field=models.CharField(
                blank=True,
                choices=[
                    ('topic-devices', 'Devices'),
                    ('topic-security', 'Security'),
                    ('topic-dev', 'Dev'),
                    ('topic-ai', 'AI'),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='talk',
            name='discourse_id',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
