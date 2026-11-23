from django.db import migrations, models


def copy_start_to_end(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    for event in Event.objects.all():
        event.end_date = event.start_date
        event.save(update_fields=['end_date'])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0014_alter_speaker_in_person'),
    ]

    operations = [
        migrations.RenameField(
            model_name='event',
            old_name='date',
            new_name='start_date',
        ),
        migrations.AddField(
            model_name='event',
            name='end_date',
            field=models.DateField(null=True),
        ),
        migrations.RunPython(copy_start_to_end, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='event',
            name='end_date',
            field=models.DateField(),
        ),
        migrations.AddField(
            model_name='event',
            name='code',
            field=models.CharField(default='', max_length=5),
            preserve_default=False,
        ),
    ]
